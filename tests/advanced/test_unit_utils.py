"""src/server 向け単体テスト。

RFC 2616 の主要要件を新 async 実装レイヤーで検証する:
- Section 4 / 5.1: Request-Line と Request-URI 解析
- Section 8.1: HTTP/1.1 の持続接続前提
- Section 9.2: OPTIONS
- Section 14.23: Host ヘッダー要件
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.server.protocol import HTTPRequest, HTTPResponse, HttpError, parse_request
from src.server.router import (
    build_server_file_path,
    find_best_route,
    get_content_type,
    get_preferred_encoding,
    normalize_request_path,
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
            b"GET http://localhost:8001/index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n\r\n"
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

    def test_post_is_rejected_with_405(self):
        with pytest.raises(HttpError) as exc:
            vetify_request(self._req(method="POST"))
        assert exc.value.status == 405

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
