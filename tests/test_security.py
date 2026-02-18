"""RFC 2616 Section 15: セキュリティに関する考慮事項のテスト"""


def test_directory_traversal_basic(http_socket):
    """Section 15.2: ディレクトリトラバーサル攻撃の防止"""
    request = b"GET /../../../etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 400, 403, 404のいずれかを返すべき
    assert b"400" in response or b"403" in response or b"404" in response


def test_directory_traversal_relative(http_socket):
    """Section 15.2: 相対パスを使ったディレクトリトラバーサル"""
    request = b"GET /../../etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response or b"403" in response or b"404" in response


def test_directory_traversal_encoded(http_socket):
    """Section 15.2: URLエンコードされたディレクトリトラバーサル"""
    # %2e%2e = ..
    request = b"GET /%2e%2e/%2e%2e/etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response or b"403" in response or b"404" in response


def test_directory_traversal_double_encoded(http_socket):
    """Section 15.2: 二重エンコードされた攻撃"""
    # %252e = %2e (エンコードされた.)
    request = (
        b"GET /%252e%252e/%252e%252e/etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n"
    )
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response or b"403" in response or b"404" in response


def test_null_byte_injection(http_socket):
    """Section 15.2: NULLバイトインジェクション"""
    request = b"GET /index.html%00.jpg HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 400または404を返すべき
    assert b"400" in response or b"404" in response


def test_absolute_path_rejection(http_socket):
    """Section 15.2: 絶対パスの拒否"""
    request = b"GET /etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # webrootの外部のファイルにはアクセスできないはず
    assert b"403" in response or b"404" in response


def test_crlf_injection_in_uri(http_socket):
    """ヘッダーインジェクション: URIに改行文字"""
    request = (
        b"GET /index.html\r\nX-Injected: header HTTP/1.1\r\nHost: localhost\r\n\r\n"
    )

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 400を返すか、接続を拒否するべき
        assert b"400" in response or len(response) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass  # 接続拒否も正常


def test_large_request_line(http_socket):
    """DoS対策: 巨大なリクエストライン"""
    long_path = "/path" + "a" * 10000
    request = f"GET {long_path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 414または400を返すべき
        assert b"414" in response or b"400" in response
    except (BrokenPipeError, ConnectionResetError):
        pass


def test_large_header_value(http_socket):
    """DoS対策: 巨大なヘッダー値"""
    large_value = "A" * 100000
    request = f"GET /index.html HTTP/1.1\r\nHost: localhost\r\nX-Large: {large_value}\r\n\r\n".encode()

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 400または431を返すべき
        assert b"400" in response or b"431" in response or len(response) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass


def test_many_headers(http_socket):
    """DoS対策: 大量のヘッダー"""
    headers = "\r\n".join([f"X-Header-{i}: value" for i in range(1000)])
    request = (
        f"GET /index.html HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n\r\n".encode()
    )

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 400を返すか接続を拒否
        assert b"400" in response or len(response) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass


def test_invalid_http_version(http_socket):
    """不正リクエスト: 不正なHTTPバージョン"""
    request = b"GET /index.html HTTP/999.999\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response or b"505" in response


def test_invalid_method_name(http_socket):
    """不正リクエスト: 不正なメソッド名"""
    request = b"INVALID<>METHOD /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"400" in response or b"501" in response


def test_control_characters_in_header(http_socket):
    """不正リクエスト: ヘッダーに制御文字"""
    request = (
        b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nX-Bad: value\x00test\r\n\r\n"
    )

    try:
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"400" in response or len(response) == 0
    except (BrokenPipeError, ConnectionResetError):
        pass
