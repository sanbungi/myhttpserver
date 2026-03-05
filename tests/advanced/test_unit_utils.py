"""src/server 向け単体テスト。

RFC 2616 の主要要件を新 async 実装レイヤーで検証する:
- Section 4 / 5.1: Request-Line と Request-URI 解析
- Section 8.1: HTTP/1.1 の持続接続前提
- Section 9.2: OPTIONS
- Section 14.23: Host ヘッダー要件
"""

import asyncio
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.server.config_model import HeadersConfig
from src.server.core import _resolve_tls_min_version
from src.server.protocol import HttpError, HTTPRequest, HTTPResponse, parse_request
from src.server.router import (
    apply_response_headers_from_config,
    build_server_file_path,
    find_best_route,
    get_content_type,
    get_preferred_encoding,
    normalize_request_path,
    resolve_route,
)
from src.server.worker import vetify_request


class TestProtocolParseRequest:
    """Section 4/5.1: Request-Line とヘッダーの最低限解析。"""

    def test_parse_origin_form_get(self):
        raw = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req = parse_request(raw, "127.0.0.1")

        assert req is not None
        assert req.method == "GET"
        assert req.path == "/index.html"
        assert req.version == "HTTP/1.1"
        assert req.headers["Host"] == "localhost"

    def test_parse_preserves_absolute_uri_for_router(self):
        raw = (
            b"GET http://localhost:8001/index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        req = parse_request(raw, "127.0.0.1")

        assert req is not None
        assert req.path == "http://localhost:8001/index.html"

    def test_invalid_request_line_returns_none(self):
        raw = b"BROKEN\r\nHost: localhost\r\n\r\n"
        assert parse_request(raw, "127.0.0.1") is None


class TestWorkerVerifyRequest:
    """Section 14.23 と method 制約の検証。"""

    @staticmethod
    def _req(method="GET", version="HTTP/1.1", headers=None, path="/index.html"):
        if headers is None:
            headers = {"Host": "localhost"}
        return HTTPRequest(
            method=method,
            path=path,
            version=version,
            remote_addr="127.0.0.1",
            headers=headers,
            body=b"",
        )

    def test_http11_requires_host_header(self):
        req = self._req(headers={})
        with pytest.raises(HttpError) as exc:
            vetify_request(req)
        assert exc.value.status == 400

    def test_get_head_options_are_allowed(self):
        vetify_request(self._req(method="GET"))
        vetify_request(self._req(method="HEAD"))
        vetify_request(self._req(method="OPTIONS", path="*"))

    def test_invalid_http_version_rejected(self):
        with pytest.raises(HttpError) as exc:
            vetify_request(self._req(version="HTTP/2.0"))
        assert exc.value.status == 400


class TestProtocolResponse:
    """Section 4.4: Message body length とレスポンス形式。"""

    def test_response_serialization_contains_status_headers_body(self):
        resp = HTTPResponse(
            status=200,
            body=b"hello",
            header={"Date": "Mon, 02 Mar 2026 00:00:00 GMT"},
            content_type="text/plain; charset=utf-8",
        )

        raw = resp.to_bytes()

        assert raw.startswith(b"HTTP/1.1 200")
        assert b"Content-Type: text/plain; charset=utf-8\r\n" in raw
        assert b"Content-Length: 5\r\n" in raw
        assert raw.endswith(b"\r\n\r\nhello")

    def test_response_gzip_sets_content_encoding(self):
        resp = HTTPResponse(status=200, body=b"abcdef" * 10)
        resp.set_compress("gzip")
        raw = resp.to_bytes()

        assert b"Content-Encoding: gzip\r\n" in raw


class TestRouterHelpers:
    """RFC2616関連の URI/path 処理補助。"""

    def test_normalize_request_path_origin_form(self):
        assert normalize_request_path("/a/b/?q=1#frag") == "/a/b"
        assert normalize_request_path("//index.html") == "/index.html"

    def test_normalize_request_path_absolute_uri(self):
        # RFC 2616 Section 5.1.2: absoluteURI 受理
        assert (
            normalize_request_path("http://localhost:8001/index.html?x=1")
            == "/index.html"
        )

    def test_build_server_file_path_rejects_traversal(self, tmp_path: Path):
        root = tmp_path / "web"
        root.mkdir()

        safe_path = build_server_file_path(str(root), "/index.html")
        assert safe_path == str(root / "index.html")

        with pytest.raises(PermissionError):
            build_server_file_path(str(root), "/../../etc/passwd")

    def test_get_content_type_and_encoding_priority(self):
        ctype, is_binary = get_content_type("index.html")
        assert "text/html" in ctype
        assert is_binary is False

        ctype, is_binary = get_content_type("image.jpg")
        assert ctype == "image/jpeg"
        assert is_binary is True

        selected = get_preferred_encoding("br, gzip", ["gzip", "zstd"])
        assert selected == "gzip"

    def test_find_best_route_prefers_longer_prefix(self):
        server = SimpleNamespace(
            routes=[
                SimpleNamespace(path="/"),
                SimpleNamespace(path="/site1"),
                SimpleNamespace(path="/site1/assets"),
            ]
        )

        route = find_best_route(server, "/site1/assets/app.js")
        assert route.path == "/site1/assets"

        route = find_best_route(server, "/unknown")
        assert route.path == "/"


class TestRouteAccessControl:
    """Route security.deny_all に応じたアクセス制御を確認。"""

    @staticmethod
    def _request(path: str) -> HTTPRequest:
        return HTTPRequest(
            method="GET",
            path=path,
            version="HTTP/1.1",
            remote_addr="127.0.0.1",
            headers={"Host": "localhost"},
            body=b"",
        )

    def test_static_route_blocks_non_allow_ip(self, tmp_path: Path):
        route = SimpleNamespace(
            path="/admin",
            type="static",
            methods=["GET"],
            index=[],
            security=SimpleNamespace(deny_all=True, ip_allow=["10.0.0.0/24"]),
        )
        server = SimpleNamespace(routes=[route], root=str(tmp_path))

        response = asyncio.run(resolve_route(self._request("/admin/index.html"), server))
        assert response.status == 403

    def test_proxy_route_blocks_non_allow_ip(self, tmp_path: Path):
        route = SimpleNamespace(
            path="/v1",
            type="proxy",
            methods=["GET"],
            security=SimpleNamespace(deny_all=True, ip_allow=["10.0.0.0/24"]),
            backend=None,
        )
        server = SimpleNamespace(routes=[route], root=str(tmp_path))

        response = asyncio.run(resolve_route(self._request("/v1/users"), server))
        assert response.status == 403

    def test_static_route_does_not_block_when_deny_all_false(self, tmp_path: Path):
        (tmp_path / "index.html").write_text("ok", encoding="utf-8")
        route = SimpleNamespace(
            path="/",
            type="static",
            methods=["GET"],
            index=["index.html"],
            security=SimpleNamespace(deny_all=False, ip_allow=["10.0.0.0/24"]),
        )
        server = SimpleNamespace(routes=[route], root=str(tmp_path))

        response = asyncio.run(resolve_route(self._request("/index.html"), server))
        assert response.status == 200


class TestConfiguredResponseHeaders:
    def test_server_headers_add_and_remove(self):
        response = HTTPResponse(
            status=200,
            body=b"ok",
            header={"server": "MyHTTPServer/0.1", "X-Powered-By": "legacy"},
        )
        server = SimpleNamespace(
            headers=HeadersConfig(
                add={"X-Frame-Options": "DENY"},
                remove=["Server", "X-Powered-By"],
            ),
            routes=[SimpleNamespace(path="/", headers=None)],
        )

        apply_response_headers_from_config(response, server, "/")

        assert "server" not in response.headers
        assert "X-Powered-By" not in response.headers
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_route_headers_override_server_headers(self):
        response = HTTPResponse(status=200, body=b"ok")
        server = SimpleNamespace(
            headers=HeadersConfig(add={"X-App": "server", "X-Trace": "on"}),
            routes=[
                SimpleNamespace(path="/", headers=HeadersConfig(add={"X-App": "root"})),
                SimpleNamespace(
                    path="/admin",
                    headers=HeadersConfig(
                        add={"X-App": "route", "Cache-Control": "no-store"},
                        remove=["X-Trace"],
                    ),
                ),
            ],
        )

        apply_response_headers_from_config(response, server, "/admin/panel")

        assert response.headers["X-App"] == "route"
        assert response.headers["Cache-Control"] == "no-store"
        assert "X-Trace" not in response.headers


class TestTlsMinVersion:
    def test_supports_tls12_names(self):
        assert _resolve_tls_min_version("TLS1.2") == ssl.TLSVersion.TLSv1_2
        assert _resolve_tls_min_version("tlsv1.2") == ssl.TLSVersion.TLSv1_2

    def test_supports_tls13_when_runtime_has_it(self):
        expected = getattr(ssl.TLSVersion, "TLSv1_3", ssl.TLSVersion.TLSv1_2)
        assert _resolve_tls_min_version("TLS1.3") == expected

    def test_invalid_value_falls_back_to_tls12(self, caplog):
        with caplog.at_level("WARNING"):
            resolved = _resolve_tls_min_version("TLS9.9")
        assert resolved == ssl.TLSVersion.TLSv1_2
        assert "Unknown tls.min_version" in caplog.text
