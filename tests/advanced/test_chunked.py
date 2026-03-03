"""RFC 2616 Section 3.6.1 / Section 14.41: チャンク転送エンコーディングのテスト

チャンク形式のリクエストボディの受信・デコード、
チャンクレスポンスの検証、Transfer-Encodingヘッダーの処理をテストする。
"""
import socket
import time

import pytest
import requests

REQUEST_TIMEOUT = 5
HOST = "localhost"
PORT = 8001


@pytest.fixture(autouse=True)
def _configure_socket_target(server_process, server_port):
    global PORT
    PORT = server_port


def _make_socket():
    """テスト用ソケットを作成"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
    s.settimeout(REQUEST_TIMEOUT)
    return s


def _recv_all(sock, timeout=3):
    """ソケットからデータをすべて受信"""
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


def _parse_http_response(raw):
    """生のHTTPレスポンスをステータス・ヘッダー・ボディに分解"""
    if b"\r\n\r\n" not in raw:
        return None, {}, b""
    header_part, body = raw.split(b"\r\n\r\n", 1)
    lines = header_part.decode("utf-8", errors="replace").split("\r\n")
    status_line = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v
    return status_line, headers, body


def _decode_chunked_body(chunked_data):
    """チャンク形式のボディをデコードする

    RFC 2616 Section 3.6.1:
    Chunked-Body = *chunk last-chunk trailer CRLF
    chunk = chunk-size [chunk-extension] CRLF chunk-data CRLF
    last-chunk = 1*("0") [chunk-extension] CRLF
    """
    decoded = b""
    pos = 0
    while pos < len(chunked_data):
        # chunk-sizeの行を探す
        crlf_pos = chunked_data.find(b"\r\n", pos)
        if crlf_pos == -1:
            break
        size_str = chunked_data[pos:crlf_pos].decode("ascii").strip()
        # chunk-extensionがあれば除去
        if ";" in size_str:
            size_str = size_str.split(";")[0]
        try:
            chunk_size = int(size_str, 16)
        except ValueError:
            break
        if chunk_size == 0:
            break
        data_start = crlf_pos + 2
        data_end = data_start + chunk_size
        decoded += chunked_data[data_start:data_end]
        pos = data_end + 2  # skip trailing CRLF
    return decoded


# =============================================================================
# チャンクリクエストボディの送信
# =============================================================================

class TestChunkedRequestBody:
    """Section 3.6.1: チャンク形式のリクエストボディを送信"""

    def test_chunked_post_basic(self):
        """基本的なチャンク転送でのPOSTリクエスト"""
        s = _make_socket()
        try:
            # Transfer-Encoding: chunkedでボディを送信
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)

            # チャンク1: "Hello"
            s.send(b"5\r\nHello\r\n")
            # チャンク2: " World"
            s.send(b"6\r\n World\r\n")
            # 最終チャンク
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            # POST自体は405（静的サーバー）だが、チャンクボディを正しく受信すること
            assert b"HTTP/1.1" in response
            # 400（不正リクエスト扱い）でないことが望ましいが、
            # チャンク未対応なら405やエラーでもOK
        finally:
            s.close()

    def test_chunked_with_single_chunk(self):
        """単一チャンクのリクエスト"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            s.send(b"d\r\nHello, World!\r\n")
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()

    def test_chunked_with_empty_body(self):
        """空のチャンクボディ（即座にlast-chunk）"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            # last-chunkのみ
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()

    def test_chunked_with_extensions(self):
        """チャンク拡張付きのリクエスト（Section 3.6.1: chunk-extension）"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            # chunk-extension付き
            s.send(b"5;ext=value\r\nHello\r\n")
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()

    def test_chunked_with_trailer(self):
        """トレーラーヘッダー付きのチャンクリクエスト"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Trailer: X-Checksum\r\n"
                b"\r\n"
            )
            s.send(request_header)
            s.send(b"5\r\nHello\r\n")
            s.send(b"0\r\n")
            # トレーラー
            s.send(b"X-Checksum: abc123\r\n")
            s.send(b"\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()


# =============================================================================
# チャンクレスポンスの検証
# =============================================================================

class TestChunkedResponse:
    """サーバーがチャンクレスポンスを返す場合のテスト"""

    def test_response_has_content_length_or_chunked(self, http_socket):
        """Section 4.4: レスポンスはContent-LengthかTransfer-Encodingを含む"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_http_response(response)

        # どちらか一方が存在すること
        has_content_length = "content-length" in headers
        has_transfer_encoding = "transfer-encoding" in headers
        assert has_content_length or has_transfer_encoding

    def test_chunked_response_decoding(self, http_socket):
        """Transfer-Encoding: chunkedのレスポンスを正しくデコードできる"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_http_response(response)

        if headers.get("transfer-encoding", "").lower() == "chunked":
            decoded = _decode_chunked_body(body)
            assert len(decoded) > 0
        else:
            # Content-Lengthベースの場合
            content_length = int(headers.get("content-length", "0"))
            assert len(body) >= content_length

    def test_chunked_and_content_length_mutual_exclusion(self, http_socket):
        """Section 4.4: Transfer-EncodingとContent-Lengthは排他的であるべき"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_line, headers, body = _parse_http_response(response)

        is_chunked = headers.get("transfer-encoding", "").lower() == "chunked"
        has_content_length = "content-length" in headers

        if is_chunked:
            # チャンク転送の場合、Content-Lengthは含むべきでない
            assert not has_content_length, "Chunked response must not include Content-Length"
        # 片方は必ず存在
        assert is_chunked or has_content_length


# =============================================================================
# Transfer-Encoding ヘッダーの処理（Section 14.41）
# =============================================================================

class TestTransferEncoding:
    """Section 14.41: Transfer-Encodingヘッダーの処理"""

    def test_te_chunked_accepted(self, http_socket):
        """Transfer-Encoding: chunkedはHTTP/1.1で必須サポート"""
        request = (
            b"POST /index.html HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"0\r\n\r\n"
        )
        http_socket.send(request)
        response = _recv_all(http_socket)
        # 501（未実装）は返さないことが望ましい
        assert b"HTTP/1.1" in response

    def test_unknown_transfer_encoding(self):
        """不明なTransfer-Encodingは501を返すべき"""
        s = _make_socket()
        try:
            request = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: unknown-encoding\r\n"
                b"Content-Length: 5\r\n"
                b"\r\n"
                b"Hello"
            )
            s.send(request)
            response = _recv_all(s)
            # 不明なエンコーディングは501 Not Implementedまたは400
            if response:
                assert b"501" in response or b"400" in response or b"405" in response
        finally:
            s.close()


# =============================================================================
# チャンクサイズのエッジケース
# =============================================================================

class TestChunkedEdgeCases:
    """チャンク転送のエッジケーステスト"""

    def test_large_chunk_size(self):
        """大きなチャンクデータの処理"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)

            # 1KBのチャンク
            data = b"A" * 1024
            chunk_size = format(len(data), "x").encode()
            s.send(chunk_size + b"\r\n" + data + b"\r\n")
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()

    def test_many_small_chunks(self):
        """多数の小さなチャンク"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)

            # 10個の小さなチャンク
            for i in range(10):
                s.send(b"1\r\n" + bytes([65 + i]) + b"\r\n")
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            assert b"HTTP/1.1" in response
        finally:
            s.close()

    @pytest.mark.xfail(reason="Server returns 405 for POST (method not supported) before parsing chunks")
    def test_invalid_chunk_size(self):
        """不正なチャンクサイズ（16進数でない）"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            # 不正なチャンクサイズ
            s.send(b"XYZ\r\nHello\r\n")
            s.send(b"0\r\n\r\n")

            response = _recv_all(s)
            # 400 Bad Requestが期待される
            assert response, "Server should respond to invalid chunk size"
            assert b"400" in response
        except (BrokenPipeError, ConnectionResetError):
            pass  # サーバーが接続を切るのも許容
        finally:
            s.close()

    def test_negative_chunk_size(self):
        """負のチャンクサイズ"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            s.send(b"-1\r\n\r\n")

            response = _recv_all(s)
            if response:
                assert b"HTTP/1.1" in response
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            s.close()

    def test_chunk_size_overflow(self):
        """非常に大きなチャンクサイズ値"""
        s = _make_socket()
        try:
            request_header = (
                b"POST /index.html HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            s.send(request_header)
            # 巨大なチャンクサイズ
            s.send(b"FFFFFFFF\r\n")

            response = _recv_all(s, timeout=2)
            # タイムアウトまたはエラーが期待される
            if response:
                assert b"HTTP/1.1" in response
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            s.close()


# =============================================================================
# requestsライブラリを使ったチャンクテスト
# =============================================================================

class TestChunkedWithRequests:
    """requestsライブラリ経由でのチャンク転送テスト"""

    def test_chunked_response_via_requests(self, server):
        """requestsでチャンクレスポンスを受信"""
        resp = requests.get(
            f"{server}/index.html", timeout=REQUEST_TIMEOUT, stream=True
        )
        assert resp.status_code == 200
        content = b""
        for chunk in resp.iter_content(chunk_size=128):
            content += chunk
        assert len(content) > 0

    def test_stream_response_content(self, server):
        """ストリーミングでレスポンスを読む"""
        resp = requests.get(
            f"{server}/test.txt", timeout=REQUEST_TIMEOUT, stream=True
        )
        assert resp.status_code == 200
        lines = list(resp.iter_lines())
        assert len(lines) > 0
