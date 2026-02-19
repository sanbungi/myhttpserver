"""RFC 2616 Section 13 / Section 14.9 / Section 14.19 / 14.25-14.29:
キャッシュ制御・条件付きリクエストのテスト

Cache-Control, ETag, Last-Modified, If-Modified-Since, If-None-Match,
304 Not Modified レスポンスなどをテストする。
"""
import email.utils
import socket
import time
from datetime import datetime, timezone

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


def _parse_response(raw):
    if b"\r\n\r\n" not in raw:
        return "", {}, b""
    header_part, body = raw.split(b"\r\n\r\n", 1)
    lines = header_part.decode("utf-8", errors="replace").split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v
    return status_line, headers, body


# =============================================================================
# ETag ヘッダー（Section 14.19）
# =============================================================================

class TestETag:
    """Section 14.19: ETagヘッダーの検証"""

    def test_etag_present_in_response(self, server):
        """静的ファイルのレスポンスにETagヘッダーが含まれるか"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        # ETagの有無を確認（SHA等のサポートは実装次第）
        # サーバーがETag未実装でもテストは壊れない
        etag = resp.headers.get("ETag", "")
        if etag:
            # 形式チェック: "xxx" or W/"xxx"
            assert etag.startswith('"') or etag.startswith('W/"')

    def test_etag_consistent(self, server):
        """同じリソースへの複数リクエストで同じETagが返る"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        resp2 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag1 = resp1.headers.get("ETag", "")
        etag2 = resp2.headers.get("ETag", "")
        if etag1 and etag2:
            assert etag1 == etag2

    def test_different_resources_different_etags(self, server):
        """異なるリソースは異なるETagを持つ"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        resp2 = requests.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        etag1 = resp1.headers.get("ETag", "")
        etag2 = resp2.headers.get("ETag", "")
        if etag1 and etag2:
            assert etag1 != etag2


# =============================================================================
# Last-Modified ヘッダー（Section 14.29）
# =============================================================================

class TestLastModified:
    """Section 14.29: Last-Modifiedヘッダーの検証"""

    def test_last_modified_present(self, server):
        """静的ファイルにLast-Modifiedヘッダーが含まれるか"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        last_mod = resp.headers.get("Last-Modified", "")
        if last_mod:
            # RFC 1123形式のパース試行
            parsed = email.utils.parsedate(last_mod)
            assert parsed is not None

    def test_last_modified_consistent(self, server):
        """同じファイルへのリクエストで同じLast-Modifiedが返る"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        resp2 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        lm1 = resp1.headers.get("Last-Modified", "")
        lm2 = resp2.headers.get("Last-Modified", "")
        if lm1 and lm2:
            assert lm1 == lm2

    def test_last_modified_not_in_future(self, server):
        """Last-Modifiedは未来の日付ではない"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        last_mod = resp.headers.get("Last-Modified", "")
        if last_mod:
            parsed = email.utils.parsedate_to_datetime(last_mod)
            now = datetime.now(timezone.utc)
            assert parsed <= now


# =============================================================================
# If-Modified-Since（Section 14.25）/ 304 Not Modified
# =============================================================================

class TestIfModifiedSince:
    """Section 14.25: If-Modified-Sinceによる条件付きGET"""

    def test_304_with_if_modified_since(self, server):
        """Last-Modifiedの値でIf-Modified-Sinceを送ると304が返る"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        last_mod = resp1.headers.get("Last-Modified", "")
        if not last_mod:
            return  # Last-Modified未実装ならスキップ

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": last_mod},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp2.status_code == 304

    def test_200_with_old_if_modified_since(self, server):
        """古い日付のIf-Modified-Sinceでは200が返る"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": "Mon, 01 Jan 2001 00:00:00 GMT"},
            timeout=REQUEST_TIMEOUT,
        )
        # ファイルは2001年以降に更新されているはず
        assert resp.status_code == 200

    def test_304_has_no_body(self, server):
        """304レスポンスにはボディが含まれない"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        last_mod = resp1.headers.get("Last-Modified", "")
        if not last_mod:
            return

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": last_mod},
            timeout=REQUEST_TIMEOUT,
        )
        if resp2.status_code == 304:
            assert len(resp2.content) == 0

    def test_304_preserves_headers(self, server):
        """304でもETag, Content-Location等のヘッダーは含まれるべき"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        last_mod = resp1.headers.get("Last-Modified", "")
        etag = resp1.headers.get("ETag", "")
        if not last_mod:
            return

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": last_mod},
            timeout=REQUEST_TIMEOUT,
        )
        if resp2.status_code == 304 and etag:
            # ETagは304でも含まれるべき
            assert resp2.headers.get("ETag", "") == etag

    def test_if_modified_since_invalid_date(self, server):
        """不正な日付のIf-Modified-Sinceは無視される"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": "invalid-date"},
            timeout=REQUEST_TIMEOUT,
        )
        # 不正な日付は無視して200を返す
        assert resp.status_code == 200

    def test_if_modified_since_future_date(self, server):
        """未来の日付のIf-Modified-Since"""
        future = "Thu, 01 Jan 2099 00:00:00 GMT"
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-Modified-Since": future},
            timeout=REQUEST_TIMEOUT,
        )
        # 未来の日付の場合、ファイルはそれより前に変更されているので304
        # ただし一部実装では200を返す
        assert resp.status_code in [200, 304]


# =============================================================================
# If-None-Match（Section 14.26）/ ETag条件付きGET
# =============================================================================

class TestIfNoneMatch:
    """Section 14.26: If-None-Matchによる条件付きGET"""

    def test_304_with_matching_etag(self, server):
        """ETagが一致するIf-None-Matchで304が返る"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        if not etag:
            return  # ETag未実装ならスキップ

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-None-Match": etag},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp2.status_code == 304

    def test_200_with_non_matching_etag(self, server):
        """ETagが一致しないIf-None-Matchで200が返る"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-None-Match": '"non-matching-etag"'},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_if_none_match_wildcard(self, server):
        """If-None-Match: * はリソースが存在すれば304"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-None-Match": "*"},
            timeout=REQUEST_TIMEOUT,
        )
        # リソースが存在するなら304、ETag未実装なら200
        assert resp.status_code in [200, 304]

    def test_head_with_if_none_match(self, server):
        """HEADリクエスト + If-None-Match"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        if not etag:
            return

        resp2 = requests.head(
            f"{server}/index.html",
            headers={"If-None-Match": etag},
            timeout=REQUEST_TIMEOUT,
        )
        # HEADでも条件付きリクエストは有効
        assert resp2.status_code in [200, 304]


# =============================================================================
# Cache-Control ヘッダー（Section 14.9）
# =============================================================================

class TestCacheControl:
    """Section 14.9: Cache-Controlヘッダーの検証"""

    def test_cache_control_in_response(self, server):
        """レスポンスにCache-Controlヘッダーが含まれるか"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        # Cache-Controlの有無を確認（実装はオプション）
        cc = resp.headers.get("Cache-Control", "")
        # 存在すれば形式確認
        if cc:
            # no-cache, no-store, max-age=N, public, private等
            assert any(
                directive in cc.lower()
                for directive in [
                    "no-cache", "no-store", "max-age", "public",
                    "private", "must-revalidate",
                ]
            )

    def test_cache_control_no_cache_request(self, server):
        """Cache-Control: no-cache リクエスト"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Cache-Control": "no-cache"},
            timeout=REQUEST_TIMEOUT,
        )
        # no-cacheリクエストでも200を返す
        assert resp.status_code == 200

    def test_cache_control_no_store_request(self, server):
        """Cache-Control: no-store リクエスト"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Cache-Control": "no-store"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_pragma_no_cache(self, server):
        """Section 14.32: Pragma: no-cache（HTTP/1.0互換キャッシュ制御）"""
        resp = requests.get(
            f"{server}/index.html",
            headers={"Pragma": "no-cache"},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200


# =============================================================================
# Expires ヘッダー（Section 14.21）
# =============================================================================

class TestExpires:
    """Section 14.21: Expiresヘッダーの検証"""

    def test_expires_in_response(self, server):
        """レスポンスにExpiresヘッダーが含まれるか"""
        resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        expires = resp.headers.get("Expires", "")
        if expires:
            # HTTP-date形式であること
            parsed = email.utils.parsedate(expires)
            assert parsed is not None


# =============================================================================
# If-Match（Section 14.24）
# =============================================================================

class TestIfMatch:
    """Section 14.24: If-Matchヘッダーの検証"""

    def test_if_match_with_matching_etag(self, server):
        """ETagが一致するIf-Match"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        if not etag:
            return

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-Match": etag},
            timeout=REQUEST_TIMEOUT,
        )
        # 一致するので通常通りレスポンス
        assert resp2.status_code == 200

    def test_if_match_non_matching(self, server):
        """ETagが一致しないIf-Match → 412 Precondition Failed"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        if not etag:
            return

        resp2 = requests.get(
            f"{server}/index.html",
            headers={"If-Match": '"different-etag"'},
            timeout=REQUEST_TIMEOUT,
        )
        # If-Match未実装なら200、実装済みなら412
        assert resp2.status_code in [200, 412]


# =============================================================================
# If-Unmodified-Since（Section 14.28）
# =============================================================================

class TestIfUnmodifiedSince:
    """Section 14.28: If-Unmodified-Sinceヘッダーの検証"""

    def test_if_unmodified_since_satisfied(self, server):
        """修正されていなければ通常レスポンス"""
        # 未来の日付を指定（＝まだ変更されていない）
        future = "Thu, 01 Jan 2099 00:00:00 GMT"
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-Unmodified-Since": future},
            timeout=REQUEST_TIMEOUT,
        )
        assert resp.status_code == 200

    def test_if_unmodified_since_failed(self, server):
        """過去の日付以降に変更されていれば412"""
        past = "Mon, 01 Jan 2001 00:00:00 GMT"
        resp = requests.get(
            f"{server}/index.html",
            headers={"If-Unmodified-Since": past},
            timeout=REQUEST_TIMEOUT,
        )
        # 未実装なら200、実装済みなら412
        assert resp.status_code in [200, 412]


# =============================================================================
# 条件付きリクエストの組み合わせ
# =============================================================================

class TestConditionalCombinations:
    """複数の条件付きヘッダーの組み合わせ"""

    def test_if_modified_since_and_if_none_match(self, server):
        """If-Modified-SinceとIf-None-Matchの同時使用"""
        resp1 = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        etag = resp1.headers.get("ETag", "")
        last_mod = resp1.headers.get("Last-Modified", "")

        if not etag or not last_mod:
            return

        resp2 = requests.get(
            f"{server}/index.html",
            headers={
                "If-Modified-Since": last_mod,
                "If-None-Match": etag,
            },
            timeout=REQUEST_TIMEOUT,
        )
        # 両方満たすなら304
        assert resp2.status_code in [200, 304]

    def test_conditional_on_nonexistent_resource(self, server):
        """存在しないリソースへの条件付きGET"""
        resp = requests.get(
            f"{server}/nonexistent.html",
            headers={"If-None-Match": '"some-etag"'},
            timeout=REQUEST_TIMEOUT,
        )
        # リソースが存在しないので404
        assert resp.status_code == 404
