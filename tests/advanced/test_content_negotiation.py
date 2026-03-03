"""RFC 2616 Section 12 / Section 14.1-14.4: コンテントネゴシエーションのテスト

Accept, Accept-Charset, Accept-Encoding, Accept-Language ヘッダーの
処理とネゴシエーション動作をテストする。
"""

import socket

import requests

REQUEST_TIMEOUT = 5


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
# Accept ヘッダー（Section 14.1）
# =============================================================================


class TestAcceptHeader:
    """Section 14.1: Acceptヘッダーによるメディアタイプネゴシエーション"""

    def test_accept_text_html(self, server):
        """Accept: text/html でHTMLファイルを取得"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept": "text/html"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("Content-Type", "")

    def test_accept_wildcard(self, server):
        """Accept: */* はすべてのContent-Typeを受け入れる"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept": "*/*"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_text_plain(self, server):
        """Accept: text/plain でテキストファイルを取得"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={"Accept": "text/plain"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")

    def test_accept_with_quality(self, server):
        """Accept: text/html;q=0.9, text/plain;q=0.8（品質値付き）"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept": "text/html;q=0.9, text/plain;q=0.8"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_multiple_types(self, server):
        """複数のAcceptタイプ"""
        resp = requests.get(
            f"{server}/index.html",
            headers={
                "Accept": "text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8"
            },
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_incompatible_type(self, server):
        """サーバーが提供できないContent-Type

        静的サーバーはAcceptヘッダーを無視してファイルをそのまま返すのが一般的。
        RFC準拠では406 Not Acceptableを返す。
        """
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        # RFC準拠: 提供不能なContent-Typeのみ指定された場合は406
        assert resp.status_code == 406

    def test_accept_absent(self, server):
        """Acceptヘッダーなし（すべてのタイプを受け入れる）"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200


# =============================================================================
# Accept-Charset ヘッダー（Section 14.2）
# =============================================================================


class TestAcceptCharset:
    """Section 14.2: Accept-Charsetヘッダーによる文字セットネゴシエーション"""

    def test_accept_charset_utf8(self, server):
        """Accept-Charset: utf-8"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Charset": "utf-8"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
        content_type = resp.headers.get("Content-Type", "")
        # charsetがutf-8であることを確認
        if "charset" in content_type.lower():
            assert "utf-8" in content_type.lower()

    def test_accept_charset_wildcard(self, server):
        """Accept-Charset: * はすべての文字セットを受け入れる"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Charset": "*"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_charset_iso_8859(self, server):
        """Accept-Charset: iso-8859-1（サーバーがutf-8のみの場合）"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Charset": "iso-8859-1"},
            timeout=REQUEST_TIMEOUT,
        )
        # RFC準拠: 提供不能な文字セットのみ指定された場合は406
        assert resp.status_code == 406

    def test_accept_charset_with_quality(self, server):
        """品質値付きAccept-Charset"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Charset": "utf-8;q=1.0, iso-8859-1;q=0.5"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200


# =============================================================================
# Accept-Encoding ヘッダー（Section 14.3）
# =============================================================================


class TestAcceptEncoding:
    """Section 14.3: Accept-Encodingヘッダーによるエンコーディングネゴシエーション"""

    def test_accept_encoding_gzip(self, server):
        """Accept-Encoding: gzip"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "gzip"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
        encoding = resp.headers.get("Content-Encoding", "")
        # gzip対応していればContent-Encoding: gzip
        if encoding == "gzip":
            # requestsが自動展開するので内容確認
            assert len(resp.text) > 0

    def test_accept_encoding_identity(self, server):
        """Accept-Encoding: identity は無圧縮を意味する"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "identity"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
        # identityの場合Content-Encodingは不要
        encoding = resp.headers.get("Content-Encoding", "")
        assert encoding in ["", "identity"]

    def test_accept_encoding_wildcard(self, server):
        """Accept-Encoding: * はすべてのエンコーディングを受け入れる"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "*"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_encoding_multiple(self, server):
        """複数のAccept-Encoding: gzip, deflate, br"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "gzip, deflate, br"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_encoding_zstd(self, server):
        """Accept-Encoding: zstd（サーバー実装にzstdサポートあり）"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "zstd"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_encoding_with_quality(self, server):
        """品質値付きAccept-Encoding"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "gzip;q=1.0, identity;q=0.5"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_no_accept_encoding(self, server):
        """Accept-Encodingヘッダーなし（無圧縮で返す）"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": ""},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_encoding_unsupported(self, server):
        """サポートしないエンコーディングのみを指定"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Encoding": "compress"},
            timeout=REQUEST_TIMEOUT,
        )
        # RFC準拠: サポートしないエンコーディングのみの場合は406
        assert resp.status_code == 406


# =============================================================================
# Accept-Language ヘッダー（Section 14.4）
# =============================================================================


class TestAcceptLanguage:
    """Section 14.4: Accept-Languageヘッダーによる言語ネゴシエーション"""

    def test_accept_language_en(self, server):
        """Accept-Language: en"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Language": "en"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_language_ja(self, server):
        """Accept-Language: ja"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Language": "ja"},
            timeout=REQUEST_TIMEOUT,
        )
        # 静的サーバーは言語ネゴシエーション未実装が一般的
        assert resp.status_code == 200

    def test_accept_language_with_quality(self, server):
        """品質値付きの複数言語"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Accept-Language": "ja;q=1.0, en;q=0.8, *;q=0.5"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_accept_language_absent(self, server):
        """Accept-Languageなし（すべての言語を受け入れる）"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200


# =============================================================================
# ブラウザが送信する典型的なAcceptヘッダーセット
# =============================================================================


class TestTypicalBrowserHeaders:
    """実際のブラウザが送信する典型的なネゴシエーションヘッダー"""

    def test_chrome_headers(self, server):
        """Chrome風の一般的なリクエストヘッダー"""
        resp = requests.get(
            f"{server}/index.html",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            },
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_firefox_headers(self, server):
        """Firefox風のリクエストヘッダー"""
        resp = requests.get(
            f"{server}/index.html",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
            },
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_curl_headers(self, server):
        """curl風のリクエストヘッダー"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={
                "Accept": "*/*",
                "User-Agent": "curl/8.0",
            },
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_api_client_headers(self, server):
        """APIクライアント風のリクエストヘッダー"""
        resp = requests.get(
            f"{server}/test.txt",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200
