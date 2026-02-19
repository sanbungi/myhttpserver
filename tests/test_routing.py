"""RFC 2616 Section 5.1.2 / Section 3.2: ルーティング・URI解決の実践的テスト

ルートパス、サブディレクトリ、インデックスファイル、静的ファイル配信、
パスの正規化、クエリストリングの処理などをテストする。
"""
import socket
import urllib.parse

import requests

REQUEST_TIMEOUT = 5


# =============================================================================
# ルートパスとインデックスファイル
# =============================================================================

class TestRootAndIndex:
    """ルートパス「/」へのアクセスとインデックスファイルのルーティング"""

    def test_root_path_returns_index(self, server):
        """GET / はindex.htmlを返す"""
        resp = requests.get(f"{server}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("Content-Type", "")
        assert "index.html" in resp.text or "<html" in resp.text.lower()

    def test_root_path_without_trailing_slash(self, http_socket):
        """パスなしでもルートとして扱われる"""
        request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_explicit_index_html(self, server):
        """GET /index.html は直接アクセス可能"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("Content-Type", "")


# =============================================================================
# サブディレクトリのルーティング
# =============================================================================

class TestSubdirectoryRouting:
    """サブディレクトリへのアクセスとリダイレクト"""

    def test_subdirectory_redirect(self, server):
        """ディレクトリへのアクセスはindex.htmlへリダイレクトされる"""
        resp = requests.get(
            f"{server}/site1", timeout=REQUEST_TIMEOUT, allow_redirects=False
        )
        # 301リダイレクトまたは直接200
        assert resp.status_code in [200, 301, 302]
        if resp.status_code in [301, 302]:
            location = resp.headers.get("Location", "")
            assert "index.html" in location or "site1" in location

    def test_subdirectory_index(self, server):
        """サブディレクトリのindex.htmlにアクセスできる"""
        resp = requests.get(
            f"{server}/site1/index.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("Content-Type", "")

    def test_subdirectory_static_files(self, server):
        """サブディレクトリの静的ファイル（CSS/JS）にアクセスできる"""
        resp_css = requests.get(
            f"{server}/site1/style.css", timeout=REQUEST_TIMEOUT
        )
        assert resp_css.status_code == 200
        assert "text/css" in resp_css.headers.get("Content-Type", "")

        resp_js = requests.get(
            f"{server}/site1/script.js", timeout=REQUEST_TIMEOUT
        )
        assert resp_js.status_code == 200
        assert "javascript" in resp_js.headers.get("Content-Type", "")


# =============================================================================
# 静的ファイルのContent-Type判定
# =============================================================================

class TestContentTypeRouting:
    """拡張子ベースのContent-Type判定"""

    def test_html_content_type(self, server):
        """HTMLファイルはtext/htmlを返す"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["Content-Type"]

    def test_txt_content_type(self, server):
        """テキストファイルはtext/plainを返す"""
        resp = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["Content-Type"]

    def test_css_content_type(self, server):
        """CSSファイルはtext/cssを返す"""
        resp = requests.get(
            f"{server}/site1/style.css", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200
        assert "text/css" in resp.headers["Content-Type"]

    def test_js_content_type(self, server):
        """JSファイルはapplication/javascriptを返す"""
        resp = requests.get(
            f"{server}/site1/script.js", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 200
        assert "javascript" in resp.headers["Content-Type"]

    def test_binary_file_content_type(self, server):
        """バイナリファイル（画像）は適切なContent-Typeを返す"""
        resp = requests.get(
            f"{server}/image.jpg", timeout=REQUEST_TIMEOUT
        )
        # ファイルが存在すればimage/jpeg、なければ404
        if resp.status_code == 200:
            assert "image/jpeg" in resp.headers["Content-Type"]


# =============================================================================
# パスの正規化とエッジケース
# =============================================================================

class TestPathNormalization:
    """パスの正規化・エッジケースのテスト"""

    def test_double_slash_in_path(self, http_socket):
        """パス内の二重スラッシュの処理"""
        request = b"GET //index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 200または404（正規化の実装による）
        assert b"HTTP/1.1" in response

    def test_dot_in_path(self, http_socket):
        """パス内の「.」の処理"""
        request = b"GET /./index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 「.」を正規化して200を返すか、404
        assert b"HTTP/1.1" in response

    def test_case_sensitivity(self, server):
        """パスの大文字小文字区別（Linux環境前提）"""
        resp = requests.get(f"{server}/Index.HTML", timeout=REQUEST_TIMEOUT)
        # Linuxではファイルシステムが大文字小文字を区別するため404
        assert resp.status_code == 404

    def test_url_encoded_path(self, server):
        """URLエンコードされたパス"""
        # %69%6e%64%65%78%2e%68%74%6d%6c = index.html
        resp = requests.get(
            f"{server}/%69%6e%64%65%78%2e%68%74%6d%6c",
            timeout=REQUEST_TIMEOUT,
        )
        # デコードして正しくルーティングされるか
        assert resp.status_code in [200, 404]

    def test_space_in_url_encoded(self, http_socket):
        """URLエンコードされた空白の処理"""
        request = b"GET /test%20file.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # ファイルがないので404が正常
        assert b"HTTP/1.1 404" in response or b"HTTP/1.1 400" in response

    def test_nonexistent_deep_path(self, server):
        """存在しない深いパスは404を返す"""
        resp = requests.get(
            f"{server}/a/b/c/d/e/f.html", timeout=REQUEST_TIMEOUT
        )
        assert resp.status_code == 404

    def test_empty_path_component(self, http_socket):
        """空のパスコンポーネント"""
        request = b"GET /site1//index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1" in response


# =============================================================================
# クエリストリング
# =============================================================================

class TestQueryString:
    """クエリストリングの処理（Section 3.2）"""

    def test_query_string_ignored_for_static(self, server):
        """静的ファイルへのクエリストリングは無視される"""
        resp = requests.get(
            f"{server}/index.html?foo=bar", timeout=REQUEST_TIMEOUT
        )
        # クエリストリングを無視して正しいファイルを返す
        assert resp.status_code == 200

    def test_query_string_with_special_chars(self, server):
        """特殊文字を含むクエリストリング"""
        resp = requests.get(
            f"{server}/index.html?q=hello+world&lang=ja",
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_fragment_not_sent(self, http_socket):
        """フラグメント（#）はサーバーに送信されないはず（ブラウザ側処理）"""
        # フラグメントを含むリクエストを直接送信した場合の処理
        request = b"GET /index.html#section1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # サーバーは適切に処理する（200または404）
        assert b"HTTP/1.1" in response


# =============================================================================
# Host ヘッダーベースのルーティング（Virtual Host）
# =============================================================================

class TestHostRouting:
    """Section 14.23: Hostヘッダーに基づくルーティング"""

    def test_host_header_with_different_hosts(self, http_socket):
        """異なるHostヘッダーでリクエスト"""
        request = b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # Virtual Host未実装でも正常にレスポンスを返すべき
        assert b"HTTP/1.1" in response

    def test_host_header_with_port(self, http_socket):
        """Hostヘッダーにポート番号を含める"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost:8001\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_host_header_case_insensitive(self, http_socket):
        """Hostヘッダーの大文字小文字"""
        request = b"GET /index.html HTTP/1.1\r\nHost: LOCALHOST\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response or b"HTTP/1.1 404" in response


# =============================================================================
# 特殊なリクエストURI形式（Section 5.1.2）
# =============================================================================

class TestRequestURIForms:
    """Section 5.1.2: Request-URIの各形式"""

    def test_absolute_path_form(self, http_socket):
        """通常の絶対パス形式"""
        request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        assert b"HTTP/1.1 200" in response

    def test_asterisk_form_options(self, http_socket):
        """Section 5.1.2: OPTIONS * はサーバー全体に対する問い合わせ"""
        request = b"OPTIONS * HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 200または204が正常
        assert b"HTTP/1.1 200" in response or b"HTTP/1.1 204" in response

    def test_absolute_uri_form(self, http_socket):
        """Section 5.1.2: 絶対URI形式（プロキシ向け）"""
        request = b"GET http://localhost/index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
        http_socket.send(request)
        response = http_socket.recv(4096)
        # 絶対URI対応がなくても不正リクエスト扱いにはしない
        assert b"HTTP/1.1" in response


# =============================================================================
# 301リダイレクトの検証
# =============================================================================

class TestRedirects:
    """リダイレクト動作のテスト"""

    def test_directory_to_index_redirect(self, server):
        """ディレクトリ→index.htmlへの301リダイレクト"""
        resp = requests.get(
            f"{server}/site1",
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if resp.status_code == 301:
            location = resp.headers.get("Location", "")
            # Locationヘッダーが存在する
            assert location != ""

    def test_redirect_follow(self, server):
        """リダイレクトをフォローすると正しいコンテンツが得られる"""
        resp = requests.get(
            f"{server}/site1", timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        # 最終的に200
        assert resp.status_code == 200
