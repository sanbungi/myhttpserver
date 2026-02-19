"""リバースプロキシ関連ヘッダーの実装テスト

X-Forwarded-For, X-Forwarded-Proto, X-Forwarded-Host, Via,
X-Real-IPなどリバースプロキシ環境で使用されるヘッダーの
受け渡しと処理をテストする。

RFC 2616 Section 14.45 (Via), Section 14.38 (Server) 等に基づく。
"""
import socket

import requests

REQUEST_TIMEOUT = 5


# =============================================================================
# X-Forwarded-For ヘッダー
# =============================================================================

class TestXForwardedFor:
    """X-Forwarded-Forヘッダーの処理テスト"""

    def test_request_with_x_forwarded_for(self, http_socket):
        """X-Forwarded-Forヘッダー付きリクエストが正常に処理される"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 192.168.1.1\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        # X-Forwarded-Forがあってもリクエストは正常に処理される
        assert b"HTTP/1.1 200" in response

    def test_multiple_x_forwarded_for(self, http_socket):
        """複数のIPを含むX-Forwarded-Forヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1, 172.16.0.1, 192.168.1.1\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_x_forwarded_for_with_ipv6(self, http_socket):
        """IPv6アドレスを含むX-Forwarded-For"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 2001:db8::1, 192.168.1.1\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_spoofed_x_forwarded_for(self, http_socket):
        """偽装されたX-Forwarded-Forでもリクエストは処理される
        （セキュリティ制御は別レイヤー）"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 127.0.0.1\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# X-Forwarded-Proto ヘッダー
# =============================================================================

class TestXForwardedProto:
    """X-Forwarded-Protoヘッダーの処理テスト"""

    def test_x_forwarded_proto_https(self, http_socket):
        """X-Forwarded-Proto: httpsが正常に処理される"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-Proto: https\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_x_forwarded_proto_http(self, http_socket):
        """X-Forwarded-Proto: httpが正常に処理される"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-Proto: http\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# X-Forwarded-Host ヘッダー
# =============================================================================

class TestXForwardedHost:
    """X-Forwarded-Hostヘッダーの処理テスト"""

    def test_x_forwarded_host(self, http_socket):
        """X-Forwarded-Hostヘッダー付きリクエスト"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-Host: www.example.com\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_x_forwarded_host_with_port(self, http_socket):
        """ポート付きX-Forwarded-Host"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-Host: www.example.com:443\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# Via ヘッダー（RFC 2616 Section 14.45）
# =============================================================================

class TestViaHeader:
    """Section 14.45: Viaヘッダーの処理テスト

    Viaヘッダーはプロキシ・ゲートウェイが通過情報を通知するために使用する。
    """

    def test_via_header_single_proxy(self, http_socket):
        """単一プロキシのViaヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Via: 1.1 proxy.example.com\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_via_header_multiple_proxies(self, http_socket):
        """複数プロキシのViaヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Via: 1.0 proxy1.example.com, 1.1 proxy2.example.com\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_via_header_with_comment(self, http_socket):
        """コメント付きViaヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Via: 1.1 proxy.example.com (Apache/2.4)\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# Server ヘッダー（RFC 2616 Section 14.38）
# =============================================================================

class TestServerHeader:
    """Section 14.38: Serverヘッダーのテスト"""

    def test_server_header_present(self, server):
        """レスポンスにServerヘッダーが含まれる"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "Server" in resp.headers
        assert resp.headers["Server"] != ""

    def test_server_header_on_error(self, server):
        """エラーレスポンスにもServerヘッダーが含まれる"""
        resp = requests.get(
            f"{server}/nonexistent.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 404
        assert "Server" in resp.headers


# =============================================================================
# X-Real-IP ヘッダー
# =============================================================================

class TestXRealIP:
    """X-Real-IPヘッダーの処理テスト"""

    def test_x_real_ip(self, http_socket):
        """X-Real-IPヘッダー付きリクエスト"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Real-IP: 203.0.113.50\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# プロキシヘッダーの組み合わせ
# =============================================================================

class TestProxyHeaderCombinations:
    """リバースプロキシ環境で送られる典型的なヘッダーの組み合わせ"""

    def test_nginx_reverse_proxy_headers(self, http_socket):
        """Nginxリバースプロキシが送る典型的なヘッダーセット"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Real-IP: 203.0.113.50\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"X-Forwarded-Proto: https\r\n"
            b"X-Forwarded-Host: www.example.com\r\n"
            b"X-Forwarded-Port: 443\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_aws_alb_headers(self, http_socket):
        """AWS ALBが送る典型的なヘッダーセット"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"X-Forwarded-Proto: https\r\n"
            b"X-Forwarded-Port: 443\r\n"
            b"X-Amzn-Trace-Id: Root=1-12345678-abcdef012345678901234567\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_cloudflare_headers(self, http_socket):
        """Cloudflareが送る典型的なヘッダーセット"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"CF-Connecting-IP: 203.0.113.50\r\n"
            b"CF-IPCountry: JP\r\n"
            b"CF-RAY: 1234567890abcdef-NRT\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"X-Forwarded-Proto: https\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_multiple_proxy_chain(self, http_socket):
        """複数プロキシを経由したリクエスト"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1, 172.16.0.1, 192.168.1.1\r\n"
            b"Via: 1.1 proxy1.example.com, 1.1 proxy2.example.com\r\n"
            b"X-Forwarded-Proto: https\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# Connection ヘッダーとプロキシ（RFC 2616 Section 14.10）
# =============================================================================

class TestConnectionHeaderProxy:
    """Section 14.10: Connectionヘッダーのプロキシ関連テスト

    Connectionヘッダーのhop-by-hopヘッダー指定が正しく処理されるか。
    """

    def test_connection_close_via_proxy(self, http_socket):
        """プロキシ経由でConnection: closeが正しく処理される"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 192.168.1.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_keep_alive_via_proxy(self, http_socket):
        """プロキシ経由でKeep-Aliveが正しく動作する"""
        # 1回目のリクエスト
        request1 = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 192.168.1.1\r\n"
            b"\r\n"
        )
        http_socket.send(request1)
        response1 = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response1

        # 同じ接続で2回目
        request2 = (
            b"GET /test.txt HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 192.168.1.1\r\n"
            b"\r\n"
        )
        http_socket.send(request2)
        response2 = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response2

    def test_proxy_connection_header(self, http_socket):
        """非標準のProxy-Connectionヘッダー（互換性テスト）"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Proxy-Connection: keep-alive\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 不明なヘッダーは無視されてリクエストは処理される
        assert b"HTTP/1.1 200" in response


# =============================================================================
# Forwarded ヘッダー（RFC 7239 - 標準化されたフォワードヘッダー）
# =============================================================================

class TestForwardedHeader:
    """RFC 7239: Forwardedヘッダー（X-Forwarded-*の標準化版）"""

    def test_forwarded_header_basic(self, http_socket):
        """基本的なForwardedヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Forwarded: for=192.0.2.60;proto=http;by=203.0.113.43\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_forwarded_header_multiple(self, http_socket):
        """複数エントリのForwardedヘッダー"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Forwarded: for=192.0.2.43, for=198.51.100.178\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response


# =============================================================================
# セキュリティ：ヘッダーインジェクション対策
# =============================================================================

class TestProxyHeaderSecurity:
    """プロキシヘッダー関連のセキュリティテスト"""

    def test_oversized_forwarded_for(self, http_socket):
        """巨大なX-Forwarded-Forでバッファオーバーフローしない"""
        huge_ip_list = ", ".join([f"10.0.{i % 256}.{i // 256}" for i in range(500)])
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: " + huge_ip_list.encode() + b"\r\n"
            b"\r\n"
        )
        try:
            http_socket.send(request)
            response = http_socket.recv(4096)
            # 200または400（ヘッダーサイズ制限による）
            assert b"HTTP/1.1" in response
        except (BrokenPipeError, ConnectionResetError):
            pass  # 接続拒否も許容

    def test_malformed_x_forwarded_for(self, http_socket):
        """不正な形式のX-Forwarded-For"""
        request = (
            b"GET /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: not-an-ip-address\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 不正な値でもリクエスト自体は処理される（値の検証はアプリ層）
        assert b"HTTP/1.1 200" in response
