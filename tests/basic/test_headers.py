"""RFC 2616 Section 14: ヘッダーフィールド定義のテスト"""

import pytest


def test_host_header_required(http_socket):
    """Section 14.23: HTTP/1.1ではHostヘッダーが必須"""
    request = b"GET /index.html HTTP/1.1\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    # Hostヘッダーがない場合は400
    assert b"400" in response


def test_host_header_with_port(http_socket):
    """Section 14.23: Hostヘッダーにポート番号を含められる"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost:8080\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"HTTP/1.1 200" in response or b"HTTP/1.1 404" in response


def test_content_type_header(http_socket):
    """Section 14.17: Content-Typeヘッダーの存在"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"Content-Type:" in response


def test_content_length_header(http_socket):
    """Section 14.13: Content-Lengthヘッダーの存在"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"Content-Length:" in response


def test_allow_header_on_405(http_socket):
    """Section 14.7: 405レスポンスにはAllowヘッダーが必須"""
    request = b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    if b"405" in response:
        assert b"Allow:" in response


def test_connection_close(http_socket):
    """Section 14.10: Connection: closeは接続を閉じる"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)
    
    assert b"HTTP/1.1" in response
    
    # 追加のリクエストを送ると失敗するはず
    try:
        http_socket.send(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response2 = http_socket.recv(4096)
        # 接続が閉じられているはず
        assert len(response2) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass  # 接続が閉じられているので正常


def test_keep_alive_connection(http_socket):
    """Section 8.1: Keep-Alive接続は複数リクエストを処理"""
    # 最初のリクエスト
    request1 = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request1)
    response1 = http_socket.recv(4096)
    
    assert b"HTTP/1.1 200" in response1 or b"HTTP/1.1 404" in response1
    
    # 同じ接続で2つ目のリクエスト
    request2 = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request2)
    response2 = http_socket.recv(4096)
    
    assert b"HTTP/1.1" in response2


def test_config_headers_add_remove(http_socket, server_runtime):
    """設定ファイルの server/route headers が反映される（config-http時のみ）"""
    if server_runtime.mode != "config-http":
        pytest.skip("requires --server-mode=config-http")

    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    header_block = response.split(b"\r\n\r\n", 1)[0]

    assert b"X-Frame-Options: DENY" in header_block
    assert b"Cache-Control: public, max-age=3600" in header_block
    assert b"\r\nServer:" not in header_block
    assert b"\r\nserver:" not in header_block
