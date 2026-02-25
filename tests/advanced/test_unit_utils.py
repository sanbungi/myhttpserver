"""utils.pyのユニットテスト

HTTPRequest/HTTPResponse、parse_request、vetify_request、
build_response、get_content_type、compress_content、
get_keep_alive、get_preferred_encoding、レスポンスヘルパーの
ユニットテストを行う。
"""

import gzip
import sys
from pathlib import Path

import pytest
import zstandard as zstd

# プロジェクトルートをPythonパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils import (
    HttpError,
    HTTPRequest,
    HTTPResponse,
    build_response,
    compress_content,
    get_content_type,
    get_http_reason_phrase,
    get_keep_alive,
    get_preferred_encoding,
    parse_request,
    response_any,
    vetify_request,
)

# =============================================================================
# HTTPRequest データクラス
# =============================================================================


class TestHTTPRequest:
    """HTTPRequestクラスのテスト"""

    def test_basic_construction(self):
        """基本的なHTTPRequestオブジェクト生成"""
        req = HTTPRequest(
            "GET", "/index.html", "HTTP/1.1", {"host": "localhost"}, b"", ""
        )
        assert req.method == "GET"
        assert req.path == "/index.html"
        assert req.version == "HTTP/1.1"
        assert req.headers["host"] == "localhost"
        assert req.body == b""

    def test_with_body(self):
        """ボディ付きリクエスト"""
        body = b"key=value"
        req = HTTPRequest(
            "POST", "/submit", "HTTP/1.1", {"host": "localhost"}, body, ""
        )
        assert req.body == b"key=value"

    def test_repr(self):
        """__repr__の出力形式"""
        req = HTTPRequest("GET", "/", "HTTP/1.1", {"host": "localhost"}, b"", "")
        repr_str = repr(req)
        assert "HTTPRequest" in repr_str
        assert "GET" in repr_str
        assert "host: localhost" in repr_str

    def test_empty_headers(self):
        """空ヘッダーのリクエスト"""
        req = HTTPRequest("GET", "/", "HTTP/1.1", {}, b"", "")
        assert req.headers == {}


# =============================================================================
# HTTPResponse データクラス
# =============================================================================


class TestHTTPResponse:
    """HTTPResponseクラスのテスト"""

    def test_basic_construction(self):
        """基本的なHTTPResponseオブジェクト生成"""
        resp = HTTPResponse(200, "text/html", "<h1>OK</h1>")
        assert resp.status_code == 200
        assert resp.content_type == "text/html"
        assert resp.content == "<h1>OK</h1>"

    def test_content_length_str(self):
        """文字列コンテンツのContent-Length"""
        resp = HTTPResponse(200, "text/plain", "Hello")
        assert resp.content_length == 5

    def test_content_length_bytes(self):
        """バイトコンテンツのContent-Length"""
        resp = HTTPResponse(200, "text/plain", b"Hello")
        assert resp.content_length == 5

    def test_content_length_utf8(self):
        """マルチバイト文字列のContent-Length"""
        resp = HTTPResponse(200, "text/plain", "日本語")
        # UTF-8で日本語は1文字3バイト
        assert resp.content_length == 9

    def test_content_length_empty(self):
        """空コンテンツのContent-Length"""
        resp = HTTPResponse(200, "text/plain", "")
        assert resp.content_length == 0

    def test_custom_headers(self):
        """カスタムヘッダー付きレスポンス"""
        resp = HTTPResponse(301, "text/plain", "", {"Location": "/new"})
        assert resp.headers["Location"] == "/new"

    def test_default_headers_empty(self):
        """デフォルトのヘッダーは空辞書"""
        resp = HTTPResponse(200, "text/plain", "")
        assert resp.headers == {}


# =============================================================================
# parse_request
# =============================================================================


class TestParseRequest:
    """parse_request関数のテスト"""

    def test_simple_get(self):
        """シンプルなGETリクエストのパース"""
        header = "GET /index.html HTTP/1.1\r\nHost: localhost"
        req = parse_request(header, b"", "")
        assert req.method == "GET"
        assert req.path == "/index.html"
        assert req.version == "HTTP/1.1"
        assert req.headers["host"] == "localhost"

    def test_multiple_headers(self):
        """複数ヘッダーのパース"""
        header = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Accept: text/html\r\n"
            "User-Agent: TestBot"
        )
        req = parse_request(header, b"", "")
        assert req.headers["host"] == "localhost"
        assert req.headers["accept"] == "text/html"
        assert req.headers["user-agent"] == "TestBot"

    def test_head_method(self):
        """HEADメソッドのパース"""
        header = "HEAD /index.html HTTP/1.1\r\nHost: localhost"
        req = parse_request(header, b"", "")
        assert req.method == "HEAD"

    def test_options_method(self):
        """OPTIONSメソッドのパース"""
        header = "OPTIONS * HTTP/1.1\r\nHost: localhost"
        req = parse_request(header, b"", "")
        assert req.method == "OPTIONS"
        assert req.path == "*"

    def test_with_body(self):
        """ボディ付きリクエストのパース"""
        header = "POST /submit HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5"
        req = parse_request(header, b"hello", "")
        assert req.body == b"hello"

    def test_headers_case_insensitive(self):
        """ヘッダー名は小文字化される"""
        header = "GET / HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/html"
        req = parse_request(header, b"", "")
        assert "content-type" in req.headers

    def test_http10_version(self):
        """HTTP/1.0バージョンのパース"""
        header = "GET / HTTP/1.0\r\nHost: localhost"
        req = parse_request(header, b"", "")
        assert req.version == "HTTP/1.0"

    def test_invalid_request_line_raises(self):
        """不正なリクエストライン → HttpError"""
        header = "INVALID\r\nHost: localhost"
        with pytest.raises(HttpError):
            parse_request(header, b"", "")

    def test_path_with_query_string(self):
        """クエリ文字列付きパスのパース"""
        header = "GET /search?q=hello HTTP/1.1\r\nHost: localhost"
        req = parse_request(header, b"", "")
        assert req.path == "/search?q=hello"


# =============================================================================
# vetify_request
# =============================================================================


class TestVetifyRequest:
    """vetify_request関数のテスト"""

    def test_valid_get_request(self):
        """有効なGETリクエストは例外なし"""
        req = HTTPRequest(
            "GET", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        vetify_request(req)  # 例外が出なければOK

    def test_valid_head_request(self):
        """有効なHEADリクエスト"""
        req = HTTPRequest(
            "HEAD", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        vetify_request(req)

    def test_valid_options_request(self):
        """有効なOPTIONSリクエスト"""
        req = HTTPRequest(
            "OPTIONS", "*", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        vetify_request(req)

    def test_missing_host_raises_400(self):
        """Hostヘッダーがない場合は400"""
        req = HTTPRequest("GET", "/", "HTTP/1.1", {}, b"", "127.0.0.1")
        with pytest.raises(HttpError) as exc_info:
            vetify_request(req)
        assert exc_info.value.status == 400

    def test_post_method_raises_405(self):
        """POSTメソッドは405"""
        req = HTTPRequest(
            "POST", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        with pytest.raises(HttpError) as exc_info:
            vetify_request(req)
        assert exc_info.value.status == 405

    def test_put_method_raises_405(self):
        """PUTメソッドは405"""
        req = HTTPRequest(
            "PUT", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        with pytest.raises(HttpError) as exc_info:
            vetify_request(req)
        assert exc_info.value.status == 405

    def test_delete_method_raises_405(self):
        """DELETEメソッドは405"""
        req = HTTPRequest(
            "DELETE", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        with pytest.raises(HttpError) as exc_info:
            vetify_request(req)
        assert exc_info.value.status == 405

    def test_patch_method_raises_405(self):
        """PATCHメソッドは405"""
        req = HTTPRequest(
            "PATCH", "/", "HTTP/1.1", {"host": "localhost"}, b"", "127.0.0.1"
        )
        with pytest.raises(HttpError) as exc_info:
            vetify_request(req)
        assert exc_info.value.status == 405


# =============================================================================
# get_content_type
# =============================================================================


class TestGetContentType:
    """get_content_type関数のテスト"""

    def test_html_extension(self):
        """HTMLファイルのMIMEタイプ"""
        ctype, is_binary = get_content_type("index.html")
        assert "text/html" in ctype
        assert is_binary is False

    def test_htm_extension(self):
        """.htm拡張子"""
        ctype, is_binary = get_content_type("page.htm")
        assert "text/html" in ctype
        assert is_binary is False

    def test_css_extension(self):
        """CSSファイル"""
        ctype, is_binary = get_content_type("style.css")
        assert "text/css" in ctype
        assert is_binary is False

    def test_js_extension(self):
        """JavaScriptファイル"""
        ctype, is_binary = get_content_type("app.js")
        assert "javascript" in ctype
        assert is_binary is False

    def test_json_extension(self):
        """JSONファイル"""
        ctype, is_binary = get_content_type("data.json")
        assert "json" in ctype
        assert is_binary is False

    def test_txt_extension(self):
        """テキストファイル"""
        ctype, is_binary = get_content_type("readme.txt")
        assert "text/plain" in ctype
        assert is_binary is False

    def test_png_extension(self):
        """PNGファイル"""
        ctype, is_binary = get_content_type("image.png")
        assert ctype == "image/png"
        assert is_binary is True

    def test_jpg_extension(self):
        """JPGファイル"""
        ctype, is_binary = get_content_type("photo.jpg")
        assert "image/jpeg" in ctype
        assert is_binary is True

    def test_jpeg_extension(self):
        """JPEG拡張子"""
        ctype, is_binary = get_content_type("photo.jpeg")
        assert "image/jpeg" in ctype
        assert is_binary is True

    def test_gif_extension(self):
        """GIFファイル"""
        ctype, is_binary = get_content_type("anim.gif")
        assert "image/gif" in ctype
        assert is_binary is True

    def test_svg_extension(self):
        """SVGファイル（テキスト）"""
        ctype, is_binary = get_content_type("icon.svg")
        assert "svg" in ctype
        assert is_binary is False

    def test_pdf_extension(self):
        """PDFファイル"""
        ctype, is_binary = get_content_type("doc.pdf")
        assert "pdf" in ctype
        assert is_binary is True

    def test_unknown_extension(self):
        """未知の拡張子はapplication/octet-stream"""
        ctype, is_binary = get_content_type("file.xyz")
        assert ctype == "application/octet-stream"
        assert is_binary is True

    def test_no_extension(self):
        """拡張子なしファイル"""
        ctype, is_binary = get_content_type("Makefile")
        assert ctype == "application/octet-stream"
        assert is_binary is True

    def test_case_insensitive_extension(self):
        """大文字拡張子"""
        ctype, is_binary = get_content_type("PAGE.HTML")
        assert "text/html" in ctype


# =============================================================================
# get_keep_alive
# =============================================================================


class TestGetKeepAlive:
    """get_keep_alive関数のテスト"""

    def test_http11_default_keep_alive(self):
        """HTTP/1.1のデフォルトはkeep-alive"""
        req = HTTPRequest("GET", "/", "HTTP/1.1", {"host": "localhost"}, b"", "")
        assert get_keep_alive(req) is True

    def test_http11_connection_close(self):
        """HTTP/1.1 + Connection: close"""
        req = HTTPRequest(
            "GET",
            "/",
            "HTTP/1.1",
            {"host": "localhost", "connection": "close"},
            b"",
            "",
        )
        assert get_keep_alive(req) is False

    def test_http10_default_close(self):
        """HTTP/1.0のデフォルトはclose"""
        req = HTTPRequest("GET", "/", "HTTP/1.0", {"host": "localhost"}, b"", "")
        assert get_keep_alive(req) is False

    def test_http10_keep_alive_explicit(self):
        """HTTP/1.0 + Connection: keep-alive"""
        req = HTTPRequest(
            "GET",
            "/",
            "HTTP/1.0",
            {"host": "localhost", "connection": "keep-alive"},
            b"",
            "",
        )
        assert get_keep_alive(req) is True

    def test_http11_connection_keep_alive(self):
        """HTTP/1.1 + Connection: keep-alive（冗長だがtrue）"""
        req = HTTPRequest(
            "GET",
            "/",
            "HTTP/1.1",
            {"host": "localhost", "connection": "keep-alive"},
            b"",
            "",
        )
        assert get_keep_alive(req) is True

    def test_http10_connection_close(self):
        """HTTP/1.0 + Connection: close"""
        req = HTTPRequest(
            "GET",
            "/",
            "HTTP/1.0",
            {"host": "localhost", "connection": "close"},
            b"",
            "",
        )
        assert get_keep_alive(req) is False


# =============================================================================
# get_preferred_encoding
# =============================================================================


class TestGetPreferredEncoding:
    """get_preferred_encoding関数のテスト"""

    def test_gzip_supported(self):
        """gzipが優先リストに含まれる場合"""
        result = get_preferred_encoding("gzip, deflate", ["gzip"])
        assert result == "gzip"

    def test_zstd_preferred_over_gzip(self):
        """zstdがgzipより優先される場合"""
        result = get_preferred_encoding("gzip, zstd", ["zstd", "gzip"])
        assert result == "zstd"

    def test_no_matching_encoding(self):
        """一致するエンコーディングがない場合"""
        result = get_preferred_encoding("deflate, br", ["gzip", "zstd"])
        assert result == ""

    def test_empty_accept_encoding(self):
        """Accept-Encodingが空"""
        result = get_preferred_encoding("", ["gzip", "zstd"])
        assert result == ""

    def test_single_encoding(self):
        """1つだけのAccept-Encoding"""
        result = get_preferred_encoding("gzip", ["gzip"])
        assert result == "gzip"

    def test_priority_order_matters(self):
        """サーバー側の優先順位が反映される"""
        result = get_preferred_encoding("gzip, zstd", ["gzip", "zstd"])
        assert result == "gzip"  # gzipが優先リストの先頭


# =============================================================================
# compress_content
# =============================================================================


class TestCompressContent:
    """compress_content関数のテスト"""

    def test_gzip_compression(self):
        """gzip圧縮が正しく行われる"""
        original = b"Hello, World! " * 100
        compressed = compress_content(original, "gzip")
        assert compressed != original
        # 解凍して元に戻るか
        decompressed = gzip.decompress(compressed)
        assert decompressed == original

    def test_zstd_compression(self):
        """zstd圧縮が正しく行われる"""
        original = b"Hello, World! " * 100
        compressed = compress_content(original, "zstd")
        assert compressed != original
        # 解凍して元に戻るか
        dctx = zstd.ZstdDecompressor()
        decompressed = dctx.decompress(compressed)
        assert decompressed == original

    def test_unknown_encoding_passthrough(self):
        """未知のエンコーディングはそのまま返す"""
        original = b"Hello"
        result = compress_content(original, "unknown")
        assert result == original

    def test_empty_encoding_passthrough(self):
        """空エンコーディングはそのまま返す"""
        original = b"Hello"
        result = compress_content(original, "")
        assert result == original

    def test_str_input_gzip(self):
        """文字列入力のgzip圧縮"""
        original = "Hello, World!"
        compressed = compress_content(original, "gzip")
        decompressed = gzip.decompress(compressed)
        assert decompressed == original.encode("utf-8")

    def test_empty_content_gzip(self):
        """空コンテンツのgzip圧縮"""
        compressed = compress_content(b"", "gzip")
        decompressed = gzip.decompress(compressed)
        assert decompressed == b""


# =============================================================================
# get_http_reason_phrase
# =============================================================================


class TestGetHTTPReasonPhrase:
    """get_http_reason_phrase関数のテスト"""

    def test_200_ok(self):
        assert get_http_reason_phrase(200) == "OK"

    def test_301_moved(self):
        assert get_http_reason_phrase(301) == "Moved Permanently"

    def test_304_not_modified(self):
        assert get_http_reason_phrase(304) == "Not Modified"

    def test_400_bad_request(self):
        assert get_http_reason_phrase(400) == "Bad Request"

    def test_404_not_found(self):
        assert get_http_reason_phrase(404) == "Not Found"

    def test_405_method_not_allowed(self):
        assert get_http_reason_phrase(405) == "Method Not Allowed"

    def test_500_internal_server_error(self):
        assert get_http_reason_phrase(500) == "Internal Server Error"

    def test_unknown_status_code(self):
        assert get_http_reason_phrase(999) == "Unknown Status Code"

    def test_100_continue(self):
        assert get_http_reason_phrase(100) == "Continue"

    def test_204_no_content(self):
        assert get_http_reason_phrase(204) == "No Content"

    def test_413_payload_too_large(self):
        assert get_http_reason_phrase(413) == "Payload Too Large"


# =============================================================================
# レスポンスヘルパー関数
# =============================================================================


class TestResponseHelpers:
    """response_NNN ヘルパー関数のテスト"""

    def test_response_200(self):
        resp = response_any(200, contents="Hello")
        assert resp.status_code == 200
        assert resp.content == "Hello"
        assert resp.content_type == "text/plain"

    def test_response_204(self):
        resp = response_any(204, header={"Allow": "GET, HEAD, OPTIONS"})
        assert resp.status_code == 204
        assert "Allow" in resp.headers

    def test_response_301(self):
        resp = response_any(301, header={"Location": "/new-location"})
        assert resp.status_code == 301
        assert resp.headers["Location"] == "/new-location"

    def test_response_400(self):
        resp = response_any(400)
        assert resp.status_code == 400

    def test_response_403(self):
        resp = response_any(403)
        assert resp.status_code == 403

    def test_response_404(self):
        resp = response_any(404)
        assert resp.status_code == 404

    def test_response_405(self):
        resp = response_any(405, header={"Allow": "GET, HEAD, OPTIONS"})
        assert resp.status_code == 405
        assert "Allow" in resp.headers

    def test_response_413(self):
        resp = response_any(413)
        assert resp.status_code == 413

    def test_response_431(self):
        resp = response_any(431)
        assert resp.status_code == 431

    def test_response_500(self):
        resp = response_any(500)
        assert resp.status_code == 500


# =============================================================================
# error_response
# =============================================================================


class TestErrorResponse:
    """error_response関数のテスト"""

    def test_error_400(self):
        resp = response_any(400)
        assert resp.status_code == 400

    def test_error_404(self):
        resp = response_any(404)
        assert resp.status_code == 404

    def test_error_405(self):
        resp = response_any(405, header={"Allow": "GET, HEAD, OPTIONS"})
        assert resp.status_code == 405
        assert "Allow" in resp.headers

    def test_error_413(self):
        resp = response_any(413)
        assert resp.status_code == 413

    def test_error_431(self):
        resp = response_any(431)
        assert resp.status_code == 431

    def test_error_unknown_falls_to_500(self):
        """未知のステータスコードは500にフォールバック"""
        resp = response_any(999)
        assert resp.status_code == 500


# =============================================================================
# build_response
# =============================================================================


class TestBuildResponse:
    """build_response関数のテスト"""

    def _make_get_request(self, path="/", headers=None):
        if headers is None:
            headers = {"host": "localhost"}
        return HTTPRequest("GET", path, "HTTP/1.1", headers, b"", "")

    def _make_head_request(self, path="/", headers=None):
        if headers is None:
            headers = {"host": "localhost"}
        return HTTPRequest("HEAD", path, "HTTP/1.1", headers, b"", "")

    def test_basic_200_response(self):
        """200レスポンスの基本構造"""
        req = self._make_get_request()
        resp = HTTPResponse(200, "text/plain", "Hello")
        raw = build_response(resp, req)

        assert b"HTTP/1.1 200 OK" in raw
        assert b"Content-Type: text/plain" in raw
        assert b"Server: MyHTTPServer" in raw
        assert b"Hello" in raw

    def test_404_response(self):
        """404レスポンスの構造"""
        req = self._make_get_request()
        resp = response_any(404)
        raw = build_response(resp, req)
        assert b"HTTP/1.1 404 Not Found" in raw

    def test_head_no_body(self):
        """HEADリクエストではbodyが空"""
        req = self._make_head_request()
        resp = HTTPResponse(200, "text/plain", "Hello")
        raw = build_response(resp, req)

        # ヘッダーとボディを分離
        header_part, _, body = raw.partition(b"\r\n\r\n")
        assert body == b""

    def test_301_has_location(self):
        """301レスポンスにLocationヘッダー"""
        req = self._make_get_request()
        resp = response_any(300, "/new")
        raw = build_response(resp, req)
        assert b"Location: /new" in raw

    def test_204_content_length_zero(self):
        """204レスポンスのContent-Lengthは0"""
        req = self._make_get_request()
        resp = response_any(204)
        raw = build_response(resp, req)
        assert b"Content-Length: 0" in raw

    def test_response_has_crlf_line_endings(self):
        """レスポンスのヘッダーはCRLF区切り"""
        req = self._make_get_request()
        resp = HTTPResponse(200, "text/plain", "Test")
        raw = build_response(resp, req)
        header_part = raw.split(b"\r\n\r\n")[0]
        # 各行がCRLFで区切られている
        lines = header_part.split(b"\r\n")
        assert len(lines) >= 3  # ステータスライン + Content-Type + Content-Length

    def test_response_bytes_content(self):
        """バイトコンテンツが正しくレスポンスに含まれる"""
        req = self._make_get_request()
        content = b"\x89PNG\r\n\x1a\n"
        resp = HTTPResponse(200, "image/png", content)
        raw = build_response(resp, req)
        assert content in raw

    def test_custom_headers_included(self):
        """カスタムヘッダーが出力に含まれる"""
        req = self._make_get_request()
        resp = HTTPResponse(
            200, "text/plain", "OK", {"X-Custom": "test-value", "X-Another": "value2"}
        )
        raw = build_response(resp, req)
        assert b"X-Custom: test-value" in raw
        assert b"X-Another: value2" in raw

    def test_server_header_present(self):
        """Serverヘッダーが含まれる"""
        req = self._make_get_request()
        resp = HTTPResponse(200, "text/plain", "OK")
        raw = build_response(resp, req)
        assert b"Server: MyHTTPServer" in raw

    def test_connection_header_present(self):
        """Connectionヘッダーが含まれる"""
        req = self._make_get_request()
        resp = HTTPResponse(200, "text/plain", "OK")
        raw = build_response(resp, req)
        # Connectionヘッダーが存在する
        assert b"Connection:" in raw

    def test_error_responses_content_length_zero(self):
        """4xxエラーのContent-Lengthは0"""
        req = self._make_get_request()
        resp = response_any(400)
        raw = build_response(resp, req)
        assert b"Content-Length: 0" in raw
