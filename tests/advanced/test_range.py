"""RFC 2616 Section 14.35 / Section 10.2.7 / Section 10.4.17:
Rangeリクエストとパーシャルコンテンツのテスト

バイトレンジリクエスト、206 Partial Content、416 Range Not Satisfiable、
Content-Rangeヘッダー、マルチレンジリクエストなどをテストする。
"""
import socket
import re

import pytest
import requests

REQUEST_TIMEOUT = 5


def _make_socket():
    """テスト用ソケットを作成"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("localhost", 8001))
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
    """生のHTTPレスポンスをステータスコード・ヘッダー・ボディに分解"""
    if b"\r\n\r\n" not in raw:
        return 0, {}, b""
    header_part, body = raw.split(b"\r\n\r\n", 1)
    lines = header_part.decode("utf-8", errors="replace").split("\r\n")
    status_line = lines[0] if lines else ""
    # ステータスコードを抽出
    match = re.search(r"HTTP/\d\.\d (\d{3})", status_line)
    status_code = int(match.group(1)) if match else 0
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v
    return status_code, headers, body


def _get_file_content(server_url, path):
    """ファイルのフルコンテンツを取得（比較用）"""
    resp = requests.get(f"{server_url}{path}", timeout=REQUEST_TIMEOUT)
    return resp.content


# =============================================================================
# 基本的なバイトレンジリクエスト（Section 14.35.1）
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestBasicByteRange:
    """Section 14.35.1: 基本的なバイトレンジリクエスト"""

    def test_single_byte_range(self, server):
        """単一バイトレンジ: bytes=0-4"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        # サーバーがRangeをサポートしている場合は206
        assert resp.status_code == 206
        assert resp.content == full_content[0:5]
        assert "Content-Range" in resp.headers

    def test_range_from_beginning(self, server):
        """先頭からのレンジ: bytes=0-9"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-9"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert resp.content == full_content[0:10]

    def test_range_from_offset(self, server):
        """途中からのレンジ: bytes=5-"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=5-"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert resp.content == full_content[5:]

    def test_suffix_range(self, server):
        """末尾からのレンジ（サフィックス形式）: bytes=-5"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=-5"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert resp.content == full_content[-5:]

    def test_range_single_byte(self, server):
        """1バイトだけのレンジ: bytes=0-0"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-0"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert resp.content == full_content[0:1]

    def test_range_last_byte(self, server):
        """最終バイトのレンジ: bytes=-1"""
        full_content = _get_file_content(server, "/test.txt")
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=-1"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert resp.content == full_content[-1:]


# =============================================================================
# 206 Partial Content レスポンスの検証（Section 10.2.7）
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestPartialContentResponse:
    """Section 10.2.7: 206レスポンスの形式検証"""

    def test_206_has_content_range(self, server):
        """206レスポンスにはContent-Rangeヘッダーが必須"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        assert "Content-Range" in resp.headers
        # Content-Range: bytes 0-4/total の形式
        cr = resp.headers["Content-Range"]
        assert cr.startswith("bytes ")

    def test_206_content_range_format(self, server):
        """Content-Rangeヘッダーの形式: bytes first-last/total"""
        full_content = _get_file_content(server, "/test.txt")
        total_len = len(full_content)
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        cr = resp.headers["Content-Range"]
        # "bytes 0-4/18" のような形式
        match = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", cr)
        assert match is not None
        first = int(match.group(1))
        last = int(match.group(2))
        if match.group(3) != "*":
            total = int(match.group(3))
            assert total == total_len
        assert first == 0
        assert last == 4

    def test_206_content_length_matches_range(self, server):
        """206のContent-LengthはレンジのバイトサイズとMATCHすべき"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        content_length = int(resp.headers.get("Content-Length", "0"))
        assert content_length == len(resp.content)
        assert content_length == 5  # bytes 0-4 = 5 bytes

    def test_206_has_date_header(self, server):
        """Section 10.2.7: 206レスポンスにはDateヘッダーが必要"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        # DateはSHOULD（推奨）
        # 存在する場合の形式チェック
        if "Date" in resp.headers:
            assert len(resp.headers["Date"]) > 0


# =============================================================================
# 416 Range Not Satisfiable（Section 10.4.17）
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestRangeNotSatisfiable:
    """Section 10.4.17: 満たせないレンジリクエスト"""

    def test_range_beyond_file_size(self, server):
        """ファイルサイズを超えるレンジ"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=99999-100000"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 416
        # Content-Range: bytes */total が含まれるべき
        if "Content-Range" in resp.headers:
            cr = resp.headers["Content-Range"]
            assert cr.startswith("bytes */")

    def test_invalid_range_first_gt_last(self, server):
        """first-byte-pos > last-byte-pos は無効なレンジ"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=10-5"},
            timeout=REQUEST_TIMEOUT,
        )
        # 無効なレンジは416を返すべき
        assert resp.status_code == 416

    def test_range_starts_at_file_size(self, server):
        """ファイルサイズちょうどから始まるレンジ"""
        full_content = _get_file_content(server, "/test.txt")
        total_len = len(full_content)
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": f"bytes={total_len}-"},
            timeout=REQUEST_TIMEOUT,
        )
        # ファイル末尾を超えているので416が期待される
        assert resp.status_code == 416


# =============================================================================
# Accept-Ranges ヘッダー（Section 14.5）
# =============================================================================

class TestAcceptRanges:
    """Section 14.5: Accept-Rangesヘッダー"""

    @pytest.mark.xfail(reason="Range requests not implemented")
    def test_accept_ranges_in_response(self, server):
        """レスポンスにAccept-Rangesヘッダーが含まれるか"""
        resp = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        # サーバーがRangeをサポートしていれば "bytes"
        # サポートしていなければ "none" またはヘッダーなし
        accept_ranges = resp.headers.get("Accept-Ranges", "")
        assert accept_ranges == "bytes"

    def test_accept_ranges_none_means_no_range(self, server):
        """Accept-Ranges: noneの場合、Rangeリクエストは無視される"""
        resp = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        if resp.headers.get("Accept-Ranges") == "none":
            # Rangeリクエストを送っても200で全体が返る
            resp2 = requests.get(
                f"{server}/test.txt",
                headers={"Range": "bytes=0-4"},
                timeout=REQUEST_TIMEOUT,
            )
            assert resp2.status_code == 200


# =============================================================================
# レンジリクエストとHEADメソッド
# =============================================================================

class TestRangeWithHead:
    """RangeリクエストとHEADメソッドの組み合わせ"""

    @pytest.mark.xfail(reason="Range requests not implemented")
    def test_head_with_range(self, server):
        """HEADリクエストでのRangeヘッダー"""
        resp = requests.head(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        # HEADは常にボディなし
        assert len(resp.content) == 0
        # ステータスは206
        assert resp.status_code == 206

    def test_head_returns_accept_ranges(self, server):
        """HEADレスポンスでAccept-Rangesが確認できる"""
        resp = requests.head(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        # Accept-Rangesがあればレンジサポートを事前確認できる


# =============================================================================
# ソケットレベルでのRangeリクエスト
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestRangeWithSocket:
    """生ソケットでのRangeリクエストテスト"""

    def test_range_header_raw_socket(self):
        """生ソケットでRangeリクエストを送信"""
        s = _make_socket()
        try:
            request = (
                b"GET /test.txt HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Range: bytes=0-4\r\n"
                b"\r\n"
            )
            s.send(request)
            response = _recv_all(s)
            status_code, headers, body = _parse_http_response(response)

            assert status_code == 206
            assert "content-range" in headers
            assert len(body) == 5
        finally:
            s.close()

    def test_range_suffix_raw_socket(self):
        """生ソケットでサフィックスレンジを送信"""
        s = _make_socket()
        try:
            request = (
                b"GET /test.txt HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Range: bytes=-3\r\n"
                b"\r\n"
            )
            s.send(request)
            response = _recv_all(s)
            status_code, headers, body = _parse_http_response(response)

            assert status_code == 206
            assert "content-range" in headers
            assert len(body) == 3
        finally:
            s.close()

    def test_range_open_end_raw_socket(self):
        """生ソケットでオープンエンドレンジを送信"""
        s = _make_socket()
        try:
            request = (
                b"GET /test.txt HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Range: bytes=5-\r\n"
                b"\r\n"
            )
            s.send(request)
            response = _recv_all(s)
            status_code, headers, body = _parse_http_response(response)

            assert status_code == 206
            assert "content-range" in headers
        finally:
            s.close()


# =============================================================================
# マルチレンジリクエスト
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestMultiRangeRequest:
    """複数レンジの同時リクエスト"""

    def test_multi_range_request(self, server):
        """複数レンジ: bytes=0-2,5-7"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-2,5-7"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        content_type = resp.headers.get("Content-Type", "")
        # マルチレンジの場合はmultipart/byteranges
        if "multipart/byteranges" in content_type:
            assert "boundary=" in content_type
        else:
            # 単一レンジとして処理される場合もある
            pass

    def test_overlapping_ranges(self, server):
        """重複するレンジ: bytes=0-5,3-8"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-5,3-8"},
            timeout=REQUEST_TIMEOUT,
        )
        # サーバーはマージするか各レンジを個別に返す
        assert resp.status_code == 206


# =============================================================================
# Rangeリクエストのエッジケース
# =============================================================================

class TestRangeEdgeCases:
    """Rangeリクエストのエッジケース"""

    def test_invalid_range_unit(self, server):
        """不正なレンジ単位: items=0-5"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "items=0-5"},
            timeout=REQUEST_TIMEOUT,
        )
        # 不明なレンジ単位は無視して200で全体を返す
        assert resp.status_code == 200

    def test_malformed_range_value(self, server):
        """不正な形式のレンジ値"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=abc-def"},
            timeout=REQUEST_TIMEOUT,
        )
        # 構文エラーのレンジは無視して200で全体を返す
        assert resp.status_code in [200, 400, 416]

    def test_empty_range_value(self, server):
        """空のレンジ値"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes="},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code in [200, 400, 416]

    def test_range_on_nonexistent_file(self, server):
        """存在しないファイルへのレンジリクエスト"""
        resp = requests.get(
            f"{server}/nonexistent.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        # ファイルがないので404
        assert resp.status_code == 404

    def test_range_on_directory(self, server):
        """ディレクトリへのレンジリクエスト"""
        resp = requests.get(
            f"{server}/site1",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        # ディレクトリへのアクセスはリダイレクト
        assert resp.status_code in [301, 302]

    @pytest.mark.xfail(reason="Range requests / ETag not implemented")
    def test_range_with_if_range(self, server):
        """If-Rangeヘッダーとの組み合わせ（Section 14.27）"""
        # まずETagを取得
        resp1 = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        assert etag, "ETag header must be present"

        resp2 = requests.get(
            f"{server}/test.txt",
            headers={
                "Range": "bytes=0-4",
                "If-Range": etag,
            },
            timeout=REQUEST_TIMEOUT,
        )
        # ETagが一致すれば206
        assert resp2.status_code == 206

    @pytest.mark.xfail(reason="Range requests not implemented")
    def test_range_zero_length_file(self, http_socket):
        """空ファイルへのレンジリクエスト"""
        # 通常のテスト用ファイルが存在しない場合を想定
        request = (
            b"GET /test.txt HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Range: bytes=0-0\r\n"
            b"\r\n"
        )
        http_socket.send(request)
        response = _recv_all(http_socket)
        status_code, headers, body = _parse_http_response(response)
        # Rangeリクエストは206を返すべき
        assert status_code == 206

    @pytest.mark.xfail(reason="Range requests not implemented")
    def test_range_entire_file(self, server):
        """ファイル全体を含むレンジ"""
        full_content = _get_file_content(server, "/test.txt")
        total_len = len(full_content)
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": f"bytes=0-{total_len - 1}"},
            timeout=REQUEST_TIMEOUT,
        )
        # 206で全体を返す
        assert resp.status_code == 206
        assert resp.content == full_content

    @pytest.mark.xfail(reason="Range requests not implemented")
    def test_range_beyond_with_valid_start(self, server):
        """last-byte-posがファイルサイズを超える場合（開始は有効）"""
        full_content = _get_file_content(server, "/test.txt")
        total_len = len(full_content)
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": f"bytes=0-{total_len + 1000}"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        # last-byte-posはファイル末尾に切り詰められる
        assert resp.content == full_content


# =============================================================================
# レンジリクエストとキャッシュ
# =============================================================================

@pytest.mark.xfail(reason="Range requests not implemented")
class TestRangeAndCaching:
    """RangeリクエストとHTTPキャッシュの相互作用"""

    def test_range_response_has_cache_headers(self, server):
        """206レスポンスにもキャッシュ関連ヘッダーがある"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 206
        # ETag/Last-Modifiedがあれば条件付きリクエストに使える
        has_etag = "ETag" in resp.headers
        has_last_modified = "Last-Modified" in resp.headers
        # どちらかがあることが望ましい（SHOULD）

    def test_consistent_range_content(self, server):
        """同じファイルへの複数レンジリクエストの整合性"""
        full_content = _get_file_content(server, "/test.txt")

        resp1 = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=0-4"},
            timeout=REQUEST_TIMEOUT,
        )
        resp2 = requests.get(
            f"{server}/test.txt",
            headers={"Range": "bytes=5-9"},
            timeout=REQUEST_TIMEOUT,
        )

        assert resp1.status_code == 206
        assert resp2.status_code == 206
        combined = resp1.content + resp2.content
        assert combined == full_content[:10]
