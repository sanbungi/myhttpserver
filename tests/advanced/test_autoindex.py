import asyncio
from types import SimpleNamespace

from src.server.autoindex_page import prime_autoindex_cache_for_server
from src.server.protocol import HTTPRequest
from src.server.router import resolve_route


def _request(path: str, headers: dict | None = None) -> HTTPRequest:
    base_headers = {"Host": "localhost"}
    if headers:
        base_headers.update(headers)
    return HTTPRequest(
        method="GET",
        path=path,
        version="HTTP/1.1",
        remote_addr="127.0.0.1",
        headers=base_headers,
        body=b"",
    )


def _server(tmp_path, index=None):
    route = SimpleNamespace(
        path="/public",
        type="static",
        methods=["GET", "HEAD", "OPTIONS"],
        index=index if index is not None else [],
        autoindex=True,
        security=None,
        headers=None,
    )
    return SimpleNamespace(root=str(tmp_path), routes=[route], headers=None)


def test_autoindex_listing_returns_html(tmp_path):
    public = tmp_path / "public"
    (public / "subdir").mkdir(parents=True)
    (public / "readme.txt").write_text("hello", encoding="utf-8")

    server = _server(tmp_path)
    prime_autoindex_cache_for_server(server)

    response = asyncio.run(resolve_route(_request("/public"), server))
    assert response.status == 200
    assert "text/html" in response.headers.get("Content-Type", "")
    body = response.body.decode("utf-8")
    assert "Index of /public" in body
    assert "readme.txt" in body
    assert "subdir/" in body


def test_autoindex_supports_if_none_match(tmp_path):
    public = tmp_path / "public"
    public.mkdir(parents=True)
    (public / "file.txt").write_text("hello", encoding="utf-8")

    server = _server(tmp_path)
    prime_autoindex_cache_for_server(server)

    first = asyncio.run(resolve_route(_request("/public"), server))
    assert first.status == 200
    etag = first.headers.get("ETag")
    assert etag

    second = asyncio.run(
        resolve_route(_request("/public", headers={"If-None-Match": etag}), server)
    )
    assert second.status == 304


def test_autoindex_uses_startup_snapshot(tmp_path):
    public = tmp_path / "public"
    public.mkdir(parents=True)
    (public / "initial.txt").write_text("hello", encoding="utf-8")

    server = _server(tmp_path)
    prime_autoindex_cache_for_server(server)
    (public / "runtime.txt").write_text("created after startup", encoding="utf-8")

    response = asyncio.run(resolve_route(_request("/public"), server))
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert "runtime.txt" not in body


def test_autoindex_prefers_directory_index_not_root_index(tmp_path):
    public = tmp_path / "public"
    public.mkdir(parents=True)
    (tmp_path / "index.html").write_text("root index", encoding="utf-8")
    (public / "index.html").write_text("public index", encoding="utf-8")

    server = _server(tmp_path)
    prime_autoindex_cache_for_server(server)

    response = asyncio.run(resolve_route(_request("/public"), server))
    assert response.status == 200
    body = response.body.decode("utf-8")
    assert "public index" in body
    assert "root index" not in body
