"""並行接続・パイプライニング・負荷テスト

RFC 2616 Section 8.1 (永続接続) に基づく並行アクセス、
HTTPパイプライニング、同時接続数テストを行う。
"""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

REQUEST_TIMEOUT = 5
HOST = "localhost"
PORT = 8001


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
# 同時接続テスト
# =============================================================================

class TestConcurrentConnections:
    """複数クライアントからの同時接続テスト"""

    def test_concurrent_get_requests(self, server):
        """10件の同時GETリクエスト"""
        results = []

        def fetch(url):
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(fetch, f"{server}/index.html")
                for _ in range(10)
            ]
            for f in as_completed(futures):
                results.append(f.result())

        assert all(code == 200 for code in results)
        assert len(results) == 10

    def test_concurrent_different_resources(self, server):
        """異なるリソースへの同時アクセス"""
        urls = [
            f"{server}/index.html",
            f"{server}/test.txt",
            f"{server}/site1/index.html",
            f"{server}/site1/style.css",
            f"{server}/site1/script.js",
        ]
        results = []

        def fetch(url):
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            return resp.status_code

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(fetch, url) for url in urls]
            for f in as_completed(futures):
                results.append(f.result())

        assert all(code == 200 for code in results)
        assert len(results) == 5

    def test_concurrent_mixed_methods(self, server):
        """GET,HEAD,OPTIONSの混在する同時リクエスト"""
        results = []

        def get_req():
            return requests.get(
                f"{server}/index.html", timeout=REQUEST_TIMEOUT
            ).status_code

        def head_req():
            return requests.head(
                f"{server}/index.html", timeout=REQUEST_TIMEOUT
            ).status_code

        def options_req():
            return requests.options(
                f"{server}/index.html", timeout=REQUEST_TIMEOUT
            ).status_code

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for _ in range(2):
                futures.append(executor.submit(get_req))
                futures.append(executor.submit(head_req))
                futures.append(executor.submit(options_req))
            for f in as_completed(futures):
                results.append(f.result())

        # 200 or 204（OPTIONS）
        assert all(code in [200, 204] for code in results)

    def test_high_concurrency(self, server):
        """50件の同時リクエスト"""
        results = []
        errors = []

        def fetch(i):
            try:
                resp = requests.get(
                    f"{server}/index.html", timeout=REQUEST_TIMEOUT
                )
                return resp.status_code
            except Exception as e:
                return str(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(fetch, i) for i in range(50)]
            for f in as_completed(futures):
                result = f.result()
                if isinstance(result, int):
                    results.append(result)
                else:
                    errors.append(result)

        # ほとんどのリクエストは成功するべき（一部タイムアウトは許容）
        success = sum(1 for r in results if r == 200)
        assert success >= 40, f"Only {success}/50 succeeded"


# =============================================================================
# HTTPパイプライニング（Section 8.1.2.2）
# =============================================================================

class TestHTTPPipelining:
    """Section 8.1.2.2: HTTPパイプライニング（同一接続で複数リクエストを連続送信）"""

    def test_pipelining_two_requests(self):
        """2つのリクエストをパイプラインで送信"""
        s = _make_socket()
        try:
            # 2つのリクエストを連続送信（レスポンスを待たずに）
            s.sendall(
                b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
                b"GET /test.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            response = _recv_all(s, timeout=5)

            # 2つのHTTPレスポンスが含まれるべき
            count = response.count(b"HTTP/1.1")
            assert count >= 2, f"Expected 2 responses, got {count}"
        finally:
            s.close()

    def test_pipelining_three_requests(self):
        """3つのリクエストをパイプラインで送信"""
        s = _make_socket()
        try:
            s.sendall(
                b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
                b"GET /test.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
                b"HEAD /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            response = _recv_all(s, timeout=5)

            count = response.count(b"HTTP/1.1")
            assert count >= 3, f"Expected 3 responses, got {count}"
        finally:
            s.close()

    def test_pipelining_order_preserved(self):
        """パイプラインのレスポンス順序が保持される"""
        s = _make_socket()
        try:
            s.sendall(
                b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
                b"GET /test.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            response = _recv_all(s, timeout=5)

            # レスポンスを分割（"HTTP/1.1"で区切り）
            parts = response.split(b"HTTP/1.1")
            # 最初レスポンスはtext/html、2番目はtext/plain
            if len(parts) >= 3:
                assert b"text/html" in parts[1]
                assert b"text/plain" in parts[2]
        finally:
            s.close()


# =============================================================================
# Keep-Alive接続の耐久テスト
# =============================================================================

class TestKeepAliveDurability:
    """Keep-Alive接続の耐久テスト"""

    def test_many_requests_same_connection(self):
        """同一接続で10回連続リクエスト"""
        s = _make_socket()
        try:
            for i in range(10):
                s.sendall(
                    b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
                )
                response = _recv_all(s, timeout=2)
                assert b"HTTP/1.1 200" in response, f"Request {i+1} failed"
        finally:
            s.close()

    def test_session_multiple_requests(self, server):
        """requests.Sessionで連続リクエスト"""
        with requests.Session() as session:
            for i in range(10):
                resp = session.get(
                    f"{server}/index.html", timeout=REQUEST_TIMEOUT
                )
                assert resp.status_code == 200

    def test_keep_alive_then_close(self):
        """Keep-Aliveで何度か通信し、最後にConnection: close"""
        s = _make_socket()
        try:
            # Keep-Aliveリクエスト × 3
            for _ in range(3):
                s.sendall(
                    b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
                )
                resp = _recv_all(s, timeout=2)
                assert b"HTTP/1.1" in resp

            # Connection: closeで接続終了
            s.sendall(
                b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            resp = _recv_all(s, timeout=2)
            assert b"HTTP/1.1" in resp

            # 接続は閉じられているはず
            try:
                s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                extra = s.recv(4096)
                assert len(extra) == 0
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # 接続が閉じられている
        finally:
            s.close()


# =============================================================================
# 同時接続でのリソース競合
# =============================================================================

class TestResourceContention:
    """同一リソースへの同時アクセスでのデータ整合性"""

    def test_same_file_concurrent_reads(self, server):
        """同一ファイルへの同時読み取りでコンテンツが一貫している"""
        contents = []

        def fetch():
            resp = requests.get(
                f"{server}/test.txt", timeout=REQUEST_TIMEOUT
            )
            return resp.text

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch) for _ in range(10)]
            for f in as_completed(futures):
                contents.append(f.result())

        # すべて同じ内容
        assert len(set(contents)) == 1

    def test_different_files_no_cross_contamination(self, server):
        """異なるファイルのレスポンスが混在しない"""
        results = {}

        def fetch(path):
            resp = requests.get(f"{server}{path}", timeout=REQUEST_TIMEOUT)
            return path, resp.text

        paths = ["/index.html", "/test.txt"]
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for _ in range(5):
                for path in paths:
                    futures.append(executor.submit(fetch, path))
            for f in as_completed(futures):
                path, text = f.result()
                if path not in results:
                    results[path] = text
                else:
                    assert results[path] == text, f"Content mismatch for {path}"


# =============================================================================
# ソケットの即時切断
# =============================================================================

class TestAbruptDisconnection:
    """クライアントの突然の切断にサーバーが耐える"""

    def test_connect_and_close_immediately(self, server_process):
        """接続直後に切断"""
        for _ in range(5):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((HOST, PORT))
            s.close()
        # サーバーがクラッシュしないことを確認
        time.sleep(0.5)
        resp = requests.get(
            f"http://{HOST}:{PORT}/index.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200

    def test_send_partial_request_then_close(self, server_process):
        """リクエストの途中で切断"""
        for _ in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((HOST, PORT))
            s.send(b"GET /index.html HTT")
            s.close()
        time.sleep(0.5)
        resp = requests.get(
            f"http://{HOST}:{PORT}/index.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200

    def test_send_headers_then_close(self, server_process):
        """ヘッダー送信後にボディなしで切断"""
        for _ in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((HOST, PORT))
            s.send(b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n")
            time.sleep(0.1)
            s.close()
        time.sleep(0.5)
        resp = requests.get(
            f"http://{HOST}:{PORT}/index.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200
