"""RFC 2616: HTTP/1.1プロトコル仕様のテスト"""

import socket


def test_http_version_in_response(http_socket):
    """レスポンスはHTTP/1.1バージョンを含む"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"HTTP/1.1" in response or b"HTTP/1.0" in response


def test_case_insensitive_headers(http_socket):
    """ヘッダー名は大文字小文字を区別しない"""
    request = b"GET /index.html HTTP/1.1\r\nhOsT: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # Hostヘッダーがあるとみなされるべき
    assert b"HTTP/1.1 200" in response or b"HTTP/1.1 404" in response


def test_multiple_spaces_in_request_line(http_socket):
    """リクエストラインの余分な空白の処理"""
    request = b"GET  /index.html  HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 寛容なサーバーは受け入れるかもしれない、厳格なサーバーは400
    assert b"HTTP/1.1" in response


def test_request_without_body(http_socket):
    """ボディなしのGETリクエスト"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"HTTP/1.1" in response


def test_request_with_content_length_zero(http_socket):
    """Content-Length: 0のPOSTリクエスト"""
    request = (
        b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
    )
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 405または他のエラー
    assert b"HTTP/1.1" in response


def test_persistent_connection_default(http_socket):
    """Section 8.1: HTTP/1.1ではデフォルトで永続的接続"""
    # 最初のリクエスト
    request1 = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request1)
    response1 = http_socket.recv(4096)

    assert b"HTTP/1.1" in response1

    # 同じソケットで2つ目のリクエスト
    request2 = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request2)
    response2 = http_socket.recv(4096)

    assert b"HTTP/1.1" in response2


def test_absolute_uri_in_request(http_socket, server):
    """Section 5.1.2: 絶対URIをサポート（プロキシ向け）"""
    # 絶対URI形式: serverフィクスチャからURLを構築
    absolute_uri = f"{server}/index.html"
    request = f"GET {absolute_uri} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    http_socket.send(request)
    response = http_socket.recv(4096)

    # RFC 2616 §5.1.2: HTTP/1.1サーバーは絶対URIを受け入れなければならない (MUST)
    assert b"HTTP/1.1 200" in response


def test_response_has_date_header(http_socket):
    """Section 14.18: レスポンスはDateヘッダーを含むべき"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # Dateヘッダーがあること (SHOULD)
    assert b"Date:" in response


def test_chunked_transfer_encoding_not_required(http_socket):
    """Transfer-Encoding: chunkedはオプション"""
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # Content-LengthまたはTransfer-Encodingのいずれか
    assert b"Content-Length:" in response or b"Transfer-Encoding:" in response


def test_100_continue_not_required(http_socket):
    """Section 8.2.3: 100 Continueはオプション"""
    request = b"POST /index.html HTTP/1.1\r\nHost: localhost\r\nExpect: 100-continue\r\nContent-Length: 10\r\n\r\n"
    http_socket.send(request)
    http_socket.settimeout(2)

    try:
        response = http_socket.recv(4096)
        # 100または417または405が返される可能性
        assert b"HTTP/1.1" in response
    except socket.timeout:
        # タイムアウトしても問題なし
        pass


def test_request_target_asterisk_form(http_socket):
    """Section 5.1.2: アスタリスク形式（OPTIONSメソッド用）"""
    request = b"OPTIONS * HTTP/1.1\r\nHost: localhost\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    # 実装していれば200、未実装なら501または400
    assert b"HTTP/1.1" in response


def test_http_1_0_request(http_socket):
    """HTTP/1.0リクエストへの互換性"""
    # HTTP/1.0にはHostヘッダーが不要
    request = b"GET /index.html HTTP/1.0\r\n\r\n"
    http_socket.send(request)
    response = http_socket.recv(4096)

    assert b"HTTP/1" in response
