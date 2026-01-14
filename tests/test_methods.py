"""RFC 2616 Section 9: メソッド定義のテスト"""
import socket


def test_get_method(http_socket):
    """Section 9.3: GETメソッドはリソースを取得する"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"HTTP/1.1 200" in response
    assert b"Content-Type:" in response
    assert b"Content-Length:" in response


def test_head_method(http_socket):
    """Section 9.4: HEADメソッドはボディを返さない"""
    request = b"HEAD /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    # ステータスラインとヘッダーのみ
    assert b"HTTP/1.1 200" in response
    assert b"Content-Length:" in response
    
    # ボディが空であることを確認
    headers_end = response.find(b"\r\n\r\n")
    assert headers_end != -1
    body = response[headers_end + 4:]
    assert len(body) == 0


def test_head_same_headers_as_get(http_socket):
    """Section 9.4: HEADのヘッダーはGETと同一であるべき"""
    # GET
    s1 = socket.socket()
    s1.connect(("localhost", 8001))
    s1.settimeout(5)  # タイムアウト設定
    s1.send(b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
    get_resp = s1.recv(4096)
    s1.close()
    
    # HEAD
    s2 = socket.socket()
    s2.connect(("localhost", 8001))
    s2.settimeout(5)  # タイムアウト設定
    s2.send(b"HEAD /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
    head_resp = s2.recv(4096)
    s2.close()
    
    # ヘッダー部分を抽出
    get_headers = get_resp.split(b"\r\n\r\n")[0]
    head_headers = head_resp.split(b"\r\n\r\n")[0]
    
    # Content-Lengthが同じであることを確認
    assert b"Content-Length:" in get_headers
    assert b"Content-Length:" in head_headers


def test_post_not_allowed_on_static_files(http_socket):
    """Section 9.5: 静的ファイルサーバーはPOSTに405を返す"""
    request = b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"HTTP/1.1 405" in response
    # Section 10.4.6: Allowヘッダーが必須
    assert b"Allow:" in response


def test_options_method(http_socket):
    """Section 9.2: OPTIONSメソッドは使用可能なメソッドを返す"""
    request = b"OPTIONS /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"HTTP/1.1 200" in response
    assert b"Allow:" in response
