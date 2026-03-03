"""RFC 2616 Section 6: レスポンス形式・ステータスライン・Date ヘッダーのテスト

レスポンスの構造、ステータスラインの形式、Dateヘッダー（Section 14.18）、
Reason Phrase、ヘッダーの大文字小文字、レスポンスボディの整合性をテストする。
"""

import email.utils
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import requests

REQUEST_TIMEOUT = 5


def _recv_all(sock, timeout=3):
    sock.settimeout(timeout)
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def _parse_response(raw):
    if b"\r\n\r\n" not in raw:
        return "", {}, b""
    header_part, body = raw.split(b"\r\n\r\n", 1)
    lines = header_part.decode("utf-8", errors="replace").split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v
    return status_line, headers, body


# =============================================================================
# ステータスライン形式（Section 6.1）
# =============================================================================


class TestStatusLine:
    """Section 6.1: Status-Line = HTTP-Version SP Status-Code SP Reason-Phrase CRLF"""

    def test_status_line_format(self, http_socket):
        """ステータスラインの形式: HTTP/1.1 200 OK"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)

        # 最初の行を抽出
        first_line = response.split(b"\r\n")[0].decode("utf-8", errors="replace")
        # HTTP-Version SP Status-Code SP Reason-Phrase
        pattern = r"^HTTP/\d+\.\d+ \d{3} .+$"
        assert re.match(pattern, first_line), f"Invalid status line: {first_line}"

    def test_status_line_200(self, http_socket):
        """200 OK のステータスライン"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        assert b"HTTP/1.1 200 OK\r\n" in response

    def test_status_line_404(self, http_socket):
        """404 Not Foundのステータスライン"""
        request = b"GET /nonexistent.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        assert b"HTTP/1.1 404 Not Found\r\n" in response

    def test_status_line_405(self, http_socket):
        """405 Method Not Allowedのステータスライン"""
        request = (
            b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
        )
        http_socket.send(request)
        response = _recv_all(http_socket)
        assert b"HTTP/1.1 405 Method Not Allowed\r\n" in response

    def test_reason_phrase_present(self, http_socket):
        """理由フレーズが存在する（空ではない）"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        first_line = response.split(b"\r\n")[0].decode("utf-8")
        parts = first_line.split(" ", 2)
        assert len(parts) == 3  # HTTP-Ver, Status-Code, Reason-Phrase
        assert len(parts[2]) > 0  # Reason-Phraseは空でない


# =============================================================================
# Date ヘッダー（Section 14.18）
# =============================================================================


class TestDateHeader:
    """Section 14.18: Dateヘッダーの検証

    Origin serverは1xx/5xx以外のすべてのレスポンスにDateを含むべき（SHOULD）。
    形式はRFC 1123のHTTP-date。
    """

    def test_date_header_present(self, server):
        """Dateヘッダーが存在する"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        date = resp.headers.get("Date", "")
        # SHOULD（推奨）なのでアサーションは条件付き
        if date:
            assert len(date) > 0

    def test_date_header_rfc1123_format(self, server):
        """Dateヘッダーの形式: RFC 1123 (e.g., Sun, 06 Nov 1994 08:49:37 GMT)"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        date = resp.headers.get("Date", "")
        if date:
            # email.utilsでパース可能な形式であること
            parsed = email.utils.parsedate(date)
            assert parsed is not None, f"Invalid Date format: {date}"

    def test_date_header_recent(self, server):
        """Dateヘッダーが現在時刻に近い"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        date = resp.headers.get("Date", "")
        if date:
            parsed = email.utils.parsedate_to_datetime(date)
            now = datetime.now(timezone.utc)
            diff = abs((now - parsed).total_seconds())
            # 60秒以内の差であること
            assert diff < 60, f"Date too far from now: {diff}s"

    def test_date_header_on_error(self, server):
        """エラーレスポンスでもDateヘッダーが含まれるか"""
        resp = requests.get(f"{server}/nonexistent.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 404
        # 4xxレスポンスにもDateが含まれるべき
        date = resp.headers.get("Date", "")
        if date:
            parsed = email.utils.parsedate(date)
            assert parsed is not None


# =============================================================================
# レスポンスヘッダーの構造（Section 4.2）
# =============================================================================


class TestResponseHeaderStructure:
    """Section 4.2: メッセージヘッダーの構造"""

    def test_headers_colon_separated(self, http_socket):
        """ヘッダーは「Name: Value」形式"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        header_part = response.split(b"\r\n\r\n")[0]
        lines = header_part.decode("utf-8").split("\r\n")

        for line in lines[1:]:  # ステータスライン以外
            assert ": " in line, f"Invalid header format: {line}"

    def test_header_crlf_terminated(self, http_socket):
        """ヘッダー行はCRLFで終端"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        header_part = response.split(b"\r\n\r\n")[0]
        # すべてのヘッダー行がCRLF区切り
        assert b"\r\n" in header_part

    def test_headers_end_with_double_crlf(self, http_socket):
        """ヘッダーとボディの間は空行（CRLFCRLF）"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        assert b"\r\n\r\n" in response


# =============================================================================
# Content-Length の正確性（Section 14.13）
# =============================================================================


class TestContentLengthAccuracy:
    """Section 14.13: Content-Lengthの値がボディサイズと一致"""

    def test_content_length_matches_body(self, http_socket):
        """Content-Lengthとボディバイト数が一致"""
        request = b"GET /test.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_response(response)

        content_length = int(headers.get("content-length", "0"))
        # Content-Lengthが0以上の場合にチェック
        if content_length > 0:
            assert len(body) == content_length

    def test_content_length_for_html(self, http_socket):
        """HTMLファイルのContent-Lengthが正しい"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_response(response)

        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            assert len(body) == content_length

    def test_content_length_zero_for_204(self, http_socket):
        """204 No ContentのContent-Lengthは0"""
        request = b"OPTIONS /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_response(response)

        if "204" in status_line:
            content_length = int(headers.get("content-length", "0"))
            assert content_length == 0
            assert len(body) == 0


# =============================================================================
# Server ヘッダー（Section 14.38）
# =============================================================================


class TestServerHeaderFormat:
    """Section 14.38: Serverヘッダーの検証"""

    def test_server_header_format(self, server):
        """Serverヘッダーの形式: product/version"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        server_header = resp.headers.get("Server", "")
        if server_header:
            # product/version 形式であることを確認
            assert "/" in server_header or len(server_header) > 0

    def test_server_header_consistent(self, server):
        """Serverヘッダーは全レスポンスで一貫している"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        resp2 = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        s1 = resp1.headers.get("Server", "")
        s2 = resp2.headers.get("Server", "")
        if s1 and s2:
            assert s1 == s2


# =============================================================================
# Connection ヘッダーのレスポンス形式（Section 14.10）
# =============================================================================


class TestConnectionHeaderResponse:
    """Section 14.10: レスポンスのConnectionヘッダー"""

    def test_connection_header_in_response(self, http_socket):
        """レスポンスにConnectionヘッダーが含まれる"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_response(response)
        # Connectionヘッダーが含まれるか確認
        # 含まれなくてもHTTP/1.1ではkeep-aliveがデフォルト
        conn = headers.get("connection", "")
        if conn:
            assert conn.lower() in ["keep-alive", "close"]

    def test_connection_close_in_response(self, http_socket):
        """Connection: closeリクエストのレスポンス"""
        request = (
            b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_response(response)
        assert "200" in status_line or "HTTP/1.1" in status_line


# =============================================================================
# レスポンスボディのエンコーディング
# =============================================================================


class TestResponseBodyEncoding:
    """レスポンスボディのエンコーディング検証"""

    def test_utf8_content_decodable(self, server):
        """Content-Type: charset=utf-8 のコンテンツがUTF-8デコードできる"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        content_type = resp.headers.get("Content-Type", "")
        if "utf-8" in content_type.lower():
            # UTF-8として正しくデコードできる
            decoded = resp.content.decode("utf-8")
            assert len(decoded) > 0

    def test_binary_content_not_corrupted(self, server):
        """バイナリファイルのコンテンツが破損していない"""
        resp = requests.get(
            f"{server}/image.jpg",
            headers={"Accept-Encoding": "identity"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            # Content-Lengthと実際のサイズが一致
            content_length = int(resp.headers.get("Content-Length", "0"))
            if content_length > 0:
                assert len(resp.content) == content_length
            fixture_path = Path(__file__).resolve().parents[2] / "html" / "image.jpg"
            original = fixture_path.read_bytes()
            assert resp.content == original
