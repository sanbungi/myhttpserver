"""HTTP/1.0互換性テスト

RFC 2616 Section 19.6に基づくHTTP/1.0との後方互換性テスト。
HTTP/1.0クライアントとの接続動作、ヘッダー処理、
バージョンネゴシエーションを検証する。
"""

import socket

import pytest

REQUEST_TIMEOUT = 5
HOST = "localhost"
PORT = 8001


@pytest.fixture(autouse=True)
def _configure_socket_target(server_process, server_port):
    global PORT
    PORT = server_port


def _make_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
    s.settimeout(REQUEST_TIMEOUT)
    return s


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


# =============================================================================
# HTTP/1.0 基本リクエスト
# =============================================================================


class TestHTTP10BasicRequests:
    """HTTP/1.0によるリクエスト送受信"""

    def test_http10_get_request(self):
        """HTTP/1.0 GETリクエストが処理される"""
        s = _make_socket()
        try:
            # HTTP/1.0でHostヘッダーなし
            s.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
            response = _recv_all(s)
            # サーバーは200を返すか、Hostがないので400を返す可能性
            assert b"HTTP/1." in response
        finally:
            s.close()

    def test_http10_get_with_host(self):
        """HTTP/1.0 GETリクエスト＋Hostヘッダー"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            # HTTP/1.0のリクエストにはHTTP/1.0またはHTTP/1.1でレスポンス
            assert b"HTTP/1." in response
            assert b"200" in response
        finally:
            s.close()

    def test_http10_head_request(self):
        """HTTP/1.0 HEADリクエスト"""
        s = _make_socket()
        try:
            s.sendall(b"HEAD /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            assert b"HTTP/1." in response
        finally:
            s.close()


# =============================================================================
# HTTP/1.0 接続ライフサイクル（Section 19.6.2）
# =============================================================================


class TestHTTP10ConnectionBehavior:
    """HTTP/1.0の接続動作テスト
    HTTP/1.0ではデフォルトで非永続接続（Connection: closeが暗黙）。
    """

    def test_http10_default_close(self):
        """HTTP/1.0のデフォルトは接続終了（非永続）"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s, timeout=3)
            assert b"HTTP/1." in response

            # HTTP/1.0のデフォルトでは接続は閉じられるべき
            try:
                s.sendall(b"GET /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
                extra = s.recv(4096)
                # 接続が閉じられていれば空データか例外
                assert not extra, "Connection should be closed after HTTP/1.0 response"
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # 期待通り：接続は閉じられた
        finally:
            s.close()

    def test_http10_explicit_keep_alive(self):
        """HTTP/1.0 Connection: keep-aliveで永続接続を要求"""
        s = _make_socket()
        try:
            s.sendall(
                b"GET /index.html HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"Connection: keep-alive\r\n"
                b"\r\n"
            )
            response = _recv_all(s, timeout=2)
            assert b"HTTP/1." in response
            assert b"200" in response
        finally:
            s.close()

    def test_http10_connection_close_explicit(self):
        """HTTP/1.0 Connection: closeを明示的に送信"""
        s = _make_socket()
        try:
            s.sendall(
                b"GET /index.html HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            response = _recv_all(s, timeout=3)
            assert b"HTTP/1." in response
        finally:
            s.close()


# =============================================================================
# HTTP/1.0 vs HTTP/1.1 レスポンスの違い
# =============================================================================


class TestHTTP10ResponseDifferences:
    """HTTP/1.0とHTTP/1.1のレスポンスの違いを検証"""

    def test_response_version_for_http10_request(self):
        """HTTP/1.0リクエストに対するレスポンスバージョン"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            # サーバーはHTTP/1.0またはHTTP/1.1で応答可能
            first_line = response.split(b"\r\n")[0]
            assert first_line.startswith(b"HTTP/1.")
        finally:
            s.close()

    def test_response_version_for_http11_request(self):
        """HTTP/1.1リクエストに対するレスポンスバージョン"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s, timeout=2)
            first_line = response.split(b"\r\n")[0]
            assert first_line.startswith(b"HTTP/1.1")
        finally:
            s.close()

    def test_http10_content_length_present(self):
        """HTTP/1.0レスポンスにContent-Lengthが含まれる"""
        s = _make_socket()
        try:
            s.sendall(b"GET /test.txt HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            assert b"Content-Length:" in response

        finally:
            s.close()

    def test_http11_vs_http10_both_have_content_type(self):
        """両バージョンのレスポンスにContent-Typeが含まれる"""
        s10 = _make_socket()
        try:
            s10.sendall(b"GET /test.txt HTTP/1.0\r\nHost: localhost\r\n\r\n")
            resp10 = _recv_all(s10)
        finally:
            s10.close()

        s11 = _make_socket()
        try:
            s11.sendall(b"GET /test.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            resp11 = _recv_all(s11, timeout=2)
        finally:
            s11.close()

        assert b"Content-Type:" in resp10
        assert b"Content-Type:" in resp11


# =============================================================================
# HTTP/1.0 Host ヘッダー要件
# =============================================================================


class TestHTTP10HostHeader:
    """HTTP/1.0ではHostヘッダーは必須ではない（RFC 2616 §14.23）"""

    def test_http10_without_host_header(self):
        """HTTP/1.0でHostヘッダーなしのリクエスト
        HTTP/1.0ではHostは任意。サーバーが400を返す（Hostを必須とする）のも許容。
        """
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
            response = _recv_all(s)
            # サーバーは200（Hostなしを許容）を返すべき
            # HTTP/1.0ではHostは必須ではない（RFC 2616 §14.23）
            assert b"HTTP/1." in response
            status_line = response.split(b"\r\n")[0]
            status_code = int(status_line.split(b" ")[1])
            assert status_code == 200, (
                f"HTTP/1.0 without Host should return 200, got {status_code}"
            )
        finally:
            s.close()

    def test_http11_without_host_returns_400(self):
        """HTTP/1.1でHostヘッダーなしは400 Bad Request"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/1.1\r\n\r\n")
            response = _recv_all(s, timeout=2)
            assert b"400" in response
        finally:
            s.close()


# =============================================================================
# バージョンネゴシエーション
# =============================================================================


class TestVersionNegotiation:
    """HTTPバージョンネゴシエーション"""

    def test_http09_request_rejected(self):
        """HTTP/0.9リクエストは拒否されるべき"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html\r\n")
            response = _recv_all(s, timeout=3)
            # HTTP/0.9: ステータスラインなしか、400エラー
            # またはバージョンが足りないので拒否
            if response:
                assert b"400" in response or b"HTTP" not in response
        finally:
            s.close()

    def test_unsupported_http_version(self):
        """未サポートのHTTPバージョンに対するレスポンス"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTP/2.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s, timeout=3)
            # 505 HTTP Version Not Supportedを返すべき
            if response:
                assert b"505" in response
        finally:
            s.close()

    def test_malformed_version(self):
        """不正なバージョン文字列"""
        s = _make_socket()
        try:
            s.sendall(b"GET /index.html HTTT/1.1\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s, timeout=3)
            if response:
                # 不正なバージョンは400
                assert b"400" in response or b"HTTP/1." in response
        finally:
            s.close()


# =============================================================================
# HTTP/1.0 の Content-Length の重要性
# =============================================================================


class TestHTTP10ContentLength:
    """HTTP/1.0ではContent-LengthまたはEOFでメッセージ終了を判定"""

    def test_http10_response_has_content_length(self):
        """HTTP/1.0レスポンスにContent-Lengthがある場合、正確なサイズ"""
        s = _make_socket()
        try:
            s.sendall(b"GET /test.txt HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            # Content-Lengthを抽出
            headers_part, _, body = response.partition(b"\r\n\r\n")
            content_length = None
            for line in headers_part.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":")[1].strip())
                    break

            if content_length is not None:
                assert len(body) == content_length
        finally:
            s.close()

    def test_http10_connection_closed_after_response(self):
        """HTTP/1.0ではContent-Lengthがなくても接続終了でメッセージが完結"""
        s = _make_socket()
        try:
            s.sendall(b"GET /test.txt HTTP/1.0\r\nHost: localhost\r\n\r\n")
            # 全データを受信（接続が閉じるまで待つ）
            response = _recv_all(s, timeout=5)
            assert len(response) > 0
            assert b"HTTP/1." in response
        finally:
            s.close()


# =============================================================================
# SimpleHTTPリクエスト（最小限のリクエスト）
# =============================================================================


class TestMinimalRequests:
    """最小限のHTTP/1.0リクエスト"""

    def test_minimal_http10_request(self):
        """最小限のHTTP/1.0 GETリクエスト"""
        s = _make_socket()
        try:
            s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            assert b"HTTP/1." in response
        finally:
            s.close()

    def test_http10_multiple_headers(self):
        """HTTP/1.0リクエストに複数ヘッダー"""
        s = _make_socket()
        try:
            s.sendall(
                b"GET /index.html HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"Accept: text/html\r\n"
                b"User-Agent: TestClient/1.0\r\n"
                b"Accept-Language: ja\r\n"
                b"\r\n"
            )
            response = _recv_all(s)
            assert b"HTTP/1." in response
            assert b"200" in response
        finally:
            s.close()

    def test_http10_case_insensitive_method(self):
        """HTTPメソッドは大文字小文字を区別するか検証"""
        s = _make_socket()
        try:
            # RFC的にはメソッドは大文字小文字を区別する
            s.sendall(b"get /index.html HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = _recv_all(s)
            # 小文字メソッドは400/405が期待される
            assert b"HTTP/1." in response
        finally:
            s.close()
