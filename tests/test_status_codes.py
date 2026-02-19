"""RFC 2616 Section 10: ステータスコード定義のテスト"""


def test_200_ok(http_socket):
    """Section 10.2.1: 200 OKは成功を示す"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"HTTP/1.1 200" in response


def test_404_not_found(http_socket):
    """Section 10.4.5: 404は存在しないリソースを示す"""
    request = b"GET /nonexistent.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"HTTP/1.1 404" in response


def test_400_bad_request_malformed_syntax(http_socket):
    """Section 10.4.1: 400は不正な構文を示す"""
    # 不正なHTTPバージョン
    request = b"GET /index.html HTTP/999\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response


def test_400_missing_host_header(http_socket):
    """Section 14.23: HTTP/1.1ではHostヘッダーが必須、ない場合は400"""
    request = b"GET /index.html HTTP/1.1\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response


def test_405_method_not_allowed(http_socket):
    """Section 10.4.6: 405は許可されていないメソッドを示す"""
    request = (
        b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
    )
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"405" in response
    # Allowヘッダーが必須
    assert b"Allow:" in response
    assert b"GET" in response or b"HEAD" in response


def test_408_request_timeout(http_socket):
    import socket

    """Section 10.4.9: 408はタイムアウトを示す（オプション）"""
    # サーバーがタイムアウトを実装している場合のテスト
    # 何も送らずに待機
    http_socket.settimeout(10)
    try:
        response = http_socket.recv(4096)
        if response:
            assert b"408" in response
    except socket.timeout:
        pass  # タイムアウトしても正常


def test_413_request_entity_too_large(http_socket):
    """Section 10.4.14: 413は大きすぎるリクエストを示す"""
    declared_size = 2 * 1024 * 1024  # 2MB
    request = f"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: {declared_size}\r\n\r\n".encode()
    http_socket.send(request)

    try:
        # 一部を送信
        http_socket.send(b"A" * 10000)
        response = http_socket.recv(4096)
        # 413または接続が閉じられる
        assert b"413" in response or len(response) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass  # サーバーが接続を閉じるのも正常


def test_414_request_uri_too_long(http_socket):
    """Section 10.4.15: 414は長すぎるURIを示す"""
    long_uri = "/path" + "a" * 10000
    request = f"GET {long_uri} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"414" in response or b"400" in response
    except (BrokenPipeError, ConnectionResetError):
        pass  # サーバーが拒否するのも正常


def test_500_internal_server_error(http_socket):
    """Section 10.5.1: 500は内部エラーを示す（実装依存）"""
    # サーバーエラーを引き起こす特殊なリクエスト
    # 実装によって異なるため、このテストは環境依存
    pass


def test_501_not_implemented(http_socket):
    """Section 10.5.2: 501は未実装のメソッドを示す"""
    # 標準だが実装されていないメソッド
    request = b"TRACE / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 501または405が返される
    assert b"501" in response or b"405" in response
