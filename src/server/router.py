import ipaddress
import logging
import os
import re
import socket
import stat as statmod
from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

import httpx

from .autoindex_page import get_cached_autoindex_page
from .config_model import HeadersConfig, ServerConfig
from .etag_utils import weak_etag_equal
from .FileCache import FileCache
from .logging_config import pretty_block, pretty_log
from .protocol import HTTPRequest, HTTPResponse
from .range_requests import (
    build_multipart_byteranges_body,
    format_content_range,
    format_unsatisfied_content_range,
    parse_range_header,
    should_apply_range_for_if_range,
)

logger = logging.getLogger(__name__)

# 静的ファイルのルートディレクトリ
_CWD = os.getcwd()
STATIC_DIR = (_CWD[:-1] if _CWD.endswith("/") and _CWD != "/" else _CWD) + "/html"
_FILE_META_CACHE: dict[str, tuple[int, int, int, str, str]] = {}
_SSRF_ALLOWLIST_CACHE: dict[
    tuple[str, tuple[str, ...]],
    tuple["ipaddress.IPv4Network | ipaddress.IPv6Network", ...],
] = {}

cache = FileCache()

# 拡張子と（MIMEタイプ, is_binary）の対応表
MIME_MAP: dict[str, tuple[str, bool]] = {
    ".html": ("text/html; charset=utf-8", False),
    ".htm": ("text/html; charset=utf-8", False),
    ".css": ("text/css; charset=utf-8", False),
    ".js": ("application/javascript; charset=utf-8", False),
    ".mjs": ("application/javascript; charset=utf-8", False),
    ".json": ("application/json; charset=utf-8", False),
    ".map": ("application/json; charset=utf-8", False),
    ".txt": ("text/plain; charset=utf-8", False),
    ".csv": ("text/csv; charset=utf-8", False),
    ".tsv": ("text/tab-separated-values; charset=utf-8", False),
    ".xml": ("application/xml; charset=utf-8", False),
    ".md": ("text/markdown; charset=utf-8", False),
    ".png": ("image/png", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".gif": ("image/gif", True),
    ".webp": ("image/webp", True),
    ".avif": ("image/avif", True),
    ".svg": ("image/svg+xml", False),  # SVGはXMLなのでテキスト
    ".ico": ("image/x-icon", True),
    ".bmp": ("image/bmp", True),
    ".tif": ("image/tiff", True),
    ".tiff": ("image/tiff", True),
    ".mp4": ("video/mp4", True),
    ".webm": ("video/webm", True),
    ".m4v": ("video/mp4", True),
    ".mov": ("video/quicktime", True),
    ".avi": ("video/x-msvideo", True),
    ".ogv": ("video/ogg", True),
    ".mp3": ("audio/mpeg", True),
    ".wav": ("audio/wav", True),
    ".ogg": ("audio/ogg", True),
    ".oga": ("audio/ogg", True),
    ".m4a": ("audio/mp4", True),
    ".aac": ("audio/aac", True),
    ".flac": ("audio/flac", True),
    ".pdf": ("application/pdf", True),
    ".wasm": ("application/wasm", True),
    ".woff": ("font/woff", True),
    ".woff2": ("font/woff2", True),
    ".ttf": ("font/ttf", True),
    ".otf": ("font/otf", True),
    ".eot": ("application/vnd.ms-fontobject", True),
    ".zip": ("application/zip", True),
    ".gz": ("application/gzip", True),
    ".br": ("application/brotli", True),
    ".tar": ("application/x-tar", True),
    ".7z": ("application/x-7z-compressed", True),
    ".rar": ("application/vnd.rar", True),
    ".webmanifest": ("application/manifest+json; charset=utf-8", False),
}


# ルーティング層
async def resolve_route(
    request: HTTPRequest, server: ServerConfig, encoding: str = ""
) -> HTTPResponse:

    request_path = normalize_request_path(request.path)
    # プロキシ用: クエリストリングを保持
    raw_query = _extract_query_string(request.path)

    try:
        # パスから使用するべきrouteを検索
        route = find_best_route(server, request_path)
        if not route:
            return HTTPResponse(404)

        allow_methods = route.methods
        logger.debug("allow_methods=%s", pretty_log(allow_methods))

        # OPTIONSメソッドは先に確認
        if request.method == "OPTIONS":
            allowed_methods_str = ", ".join(allow_methods or ["*"])
            return HTTPResponse(204, header={"Allow": allowed_methods_str})

        # 許可しているメソッドのチェック
        if allow_methods and request.method not in allow_methods:
            allowed_methods_str = ", ".join(route.methods)

            logger.debug("allowed_methods_str=%s", allowed_methods_str)
            return HTTPResponse(
                status=405,
                body=f"405 Method Not Allowed\nAllowed: {allowed_methods_str}",
                header={"Allow": allowed_methods_str},
            )

        # アクセス制御
        if route.type in {"static", "proxy"}:
            access_control_resp = _apply_route_access_control(request, route)
            if access_control_resp is not None:
                return access_control_resp

        # 静的ファイル配信
        if route.type == "static":
            # パス正規化
            if request_path == "/":
                index_file = route.index[0] if route.index else "index.html"
                server_file_path = build_server_file_path(
                    server.root, "/" + index_file.lstrip("/")
                )
            else:
                server_file_path = build_server_file_path(server.root, request_path)

            # キャッシュ用にファイルの最終変更時などを取得
            meta = _get_file_meta(server_file_path)
            if meta is None:
                return HTTPResponse(404)

            stat_result, last_modify, base_etag = meta
            # /でパスが終わるなら、そのディレクトリのindex.htmlに転送させる。
            if statmod.S_ISDIR(stat_result.st_mode):
                if getattr(route, "autoindex", False):
                    index_meta = _find_directory_index_meta(
                        server_file_path, route.index
                    )
                    if index_meta is not None:
                        server_file_path, stat_result, last_modify, base_etag = (
                            index_meta
                        )
                    else:
                        autoindex = get_cached_autoindex_page(
                            server_file_path, request_path
                        )
                        if autoindex is None:
                            return HTTPResponse(404)

                        autoindex_content, auto_last_modified, auto_mtime, auto_etag = (
                            autoindex
                        )
                        auto_etag_header = _format_etag_header(auto_etag)

                        cache_hit = check_cache_if_none_match(request, auto_etag)
                        if cache_hit:
                            headers = {
                                "Last-Modified": auto_last_modified,
                                "Cache-Control": "max-age=3600",
                            }
                            if auto_etag_header:
                                headers["ETag"] = auto_etag_header
                            return HTTPResponse(304, header=headers)

                        cache_hit = check_cache_if_modified_since(
                            request,
                            auto_mtime,
                            if_none_match_supported=True,
                        )
                        if cache_hit:
                            headers = {
                                "Last-Modified": auto_last_modified,
                                "Cache-Control": "max-age=3600",
                            }
                            if auto_etag_header:
                                headers["ETag"] = auto_etag_header
                            return HTTPResponse(304, header=headers)

                        headers = {
                            "Last-Modified": auto_last_modified,
                            "Cache-Control": "max-age=3600",
                        }
                        if auto_etag_header:
                            headers["ETag"] = auto_etag_header
                        return HTTPResponse(
                            status=200,
                            body=autoindex_content,
                            header=headers,
                            content_type="text/html; charset=utf-8",
                        )
                else:
                    return HTTPResponse(status=301, header={"Location": "index.html"})

            # Rangeヘッダーの取得
            range_header = _get_header_case_insensitive(request.headers, "range")
            if_range_header = _get_header_case_insensitive(request.headers, "if-range")
            if range_header:
                logger.debug("range_header=%s", range_header)

            # Etagによるキャッシュ
            current_etag = _join_etag_with_encoding(base_etag, encoding)
            etag_header = _format_etag_header(current_etag)

            cache_hit = check_cache_if_none_match(request, current_etag)
            if cache_hit:
                headers = {
                    "Last-Modified": last_modify,
                    "Cache-Control": "max-age=3600",
                    "Accept-Ranges": "bytes",
                }
                if etag_header:
                    headers["ETag"] = etag_header
                return HTTPResponse(304, header=headers)

            # 2段目、if_modifiedによるキャッシュチェック
            cache_hit = check_cache_if_modified_since(
                request,
                stat_result.st_mtime,
                if_none_match_supported=True,
            )
            if cache_hit:
                headers = {
                    "Last-Modified": last_modify,
                    "Cache-Control": "max-age=3600",
                    "Accept-Ranges": "bytes",
                }
                if etag_header:
                    headers["ETag"] = etag_header
                return HTTPResponse(304, header=headers)

            # 実ファイルパスからコンテンツタイプを推測
            content_type, is_binary = get_content_type(server_file_path)

            # バイナリならそのまま読む、テキストならエンコード
            if is_binary:
                content = cache.get_cached(server_file_path, mode="rb")
                if content is cache.MISS:
                    content = await cache.read_from_disk(server_file_path, mode="rb")
            else:
                text = cache.get_cached(server_file_path, mode="r")
                if text is cache.MISS:
                    text = await cache.read_from_disk(server_file_path, mode="r")
                content = text.encode("utf-8")

            # HACK max-ageが決め打ち
            response_headers = {
                "Last-Modified": last_modify,
                "Cache-Control": "max-age=3600",
                "Accept-Ranges": "bytes",
            }

            # etagを再度つける
            if etag_header:
                response_headers["ETag"] = etag_header

            # Rangeレスポンス
            if range_header and should_apply_range_for_if_range(
                if_range_header, current_etag, last_modify
            ):
                parsed_range = parse_range_header(range_header, len(content))

                # bytes以外はRangeを無視し、通常の200を返す
                if parsed_range.unit_supported:
                    # 指定された範囲が提供できない
                    if not parsed_range.is_valid or not parsed_range.ranges:
                        headers = response_headers | {
                            "Content-Range": format_unsatisfied_content_range(
                                len(content)
                            )
                        }
                        response = HTTPResponse(416, b"", headers, content_type)
                        response.disable_compression()
                        return response

                    # 範囲は1つ指定である
                    if len(parsed_range.ranges) == 1:
                        partial_range = parsed_range.ranges[0]
                        partial_body = content[
                            partial_range.start : partial_range.end + 1
                        ]
                        headers = response_headers | {
                            "Content-Range": format_content_range(
                                partial_range, len(content)
                            )
                        }
                        response = HTTPResponse(
                            206, partial_body, headers, content_type
                        )
                        response.disable_compression()
                        return response

                    # 複数の範囲指定がある場合
                    multipart_type, multipart_body = build_multipart_byteranges_body(
                        content=content,
                        ranges=parsed_range.ranges,
                        content_type=content_type,
                        resource_size=len(content),
                    )
                    response = HTTPResponse(
                        206,
                        multipart_body,
                        response_headers,
                        multipart_type,
                    )
                    response.disable_compression()
                    return response

            return HTTPResponse(
                200,
                content,
                response_headers,
                content_type,
            )

        # リバースプロキシ
        elif route.type == "proxy":
            upstrean_url = route.backend.upstream
            proxy_path_part = _sanitize_proxy_path(
                request_path[len(route.path) :]
            )
            # クエリストリングを復元して upstream に転送
            proxy_request_path = (
                f"{proxy_path_part}?{raw_query}" if raw_query else proxy_path_part
            )

            logger.debug("proxy_request_path=%s", proxy_request_path)
            logger.debug("upstream_url=%s", upstrean_url)

            # SSRF 防止: upstream の最終 URL がプライベートネットワークでないか検証
            # 設定で明示された upstream ホストと ssrf_allow は許可する
            final_url = f"{upstrean_url}{proxy_request_path}"
            cache_key = (upstrean_url, tuple(route.backend.ssrf_allow))
            ssrf_allowlist = _SSRF_ALLOWLIST_CACHE.get(cache_key)
            if ssrf_allowlist is None:
                ssrf_allowlist = build_ssrf_allowlist(
                    upstrean_url, route.backend.ssrf_allow
                )
                _SSRF_ALLOWLIST_CACHE[cache_key] = ssrf_allowlist
            try:
                _validate_upstream_target(final_url, ssrf_allowlist)
            except ValueError as e:
                logger.warning("SSRF blocked: %s -> %s: %s", request.path, final_url, e)
                return HTTPResponse(403)

            # upstream へ送るリクエストヘッダーを構築
            send_header = dict(request.headers)
            # RFC 7230 §6.1: hop-by-hop ヘッダーを upstream に転送しない
            _strip_hop_by_hop_headers(send_header)
            # Host は httpx が upstream URL から自動設定するため除去
            _drop_header_case_insensitive(send_header, "host")
            # backend.headers 設定を適用（X-Forwarded-* 等）
            _apply_backend_headers(send_header, route.backend.headers, request)

            logger.debug("upstream request headers=%s", pretty_block(send_header))

            try:
                async with httpx.AsyncClient() as client:
                    # HACK timeoutが決め打ち
                    resp = await client.request(
                        method=request.method,
                        url=final_url,
                        headers=send_header,
                        content=request.body,
                        timeout=10.0,
                        follow_redirects=False,
                    )
            except httpx.RequestError as e:
                logger.error(
                    "Proxy upstream connection error: %s %s -> %s%s: %s",
                    request.method,
                    request.path,
                    upstrean_url,
                    proxy_request_path,
                    e,
                )
                return HTTPResponse(504)

            logger.debug("upstream status=%s", resp.status_code)
            logger.debug("upstream headers=%s", pretty_block(dict(resp.headers)))

            if resp.status_code >= 500:
                logger.warning(
                    "Proxy upstream returned %s: %s %s -> %s%s",
                    resp.status_code,
                    request.method,
                    request.path,
                    upstrean_url,
                    proxy_request_path,
                )

            try:
                # Set-Cookie は複数ある可能性があるため dict 変換前に個別取得
                raw_set_cookies = resp.headers.get_list("set-cookie")

                # レスポンスヘッダーを dict に変換し hop-by-hop 等を除去
                _content_type = resp.headers.get(
                    "content-type", "application/octet-stream"
                )
                resp_headers = dict(resp.headers)
                _drop_header_case_insensitive(resp_headers, "content-type")
                _drop_header_case_insensitive(resp_headers, "set-cookie")
                _strip_proxy_response_headers(resp_headers)

                # Set-Cookie のドメイン・SameSite 書き換え
                is_https = bool(server.tls and server.tls.enabled)
                from_host = (
                    route.backend.rewrite_url.split("://")[-1]
                    if route.backend.rewrite_url
                    else upstrean_url.split("://")[-1]
                )
                if raw_set_cookies:
                    resp_headers["Set-Cookie"] = [
                        _rewrite_set_cookie_header(c, from_host, is_https)
                        for c in raw_set_cookies
                    ]
                    logger.debug(
                        "rewritten Set-Cookie: %s", resp_headers["Set-Cookie"]
                    )

                resp_body = resp.content

                # レスポンスボディ・ヘッダー内のURLリライト
                rewrite_url = route.backend.rewrite_url
                if rewrite_url:
                    resp_body, resp_headers = _rewrite_proxy_urls(
                        resp_body,
                        _content_type,
                        resp_headers,
                        rewrite_url,
                        request,
                        server,
                    )

                return HTTPResponse(
                    status=resp.status_code,
                    body=resp_body,
                    content_type=_content_type,
                    header=resp_headers,
                )
            except Exception as e:
                logger.error(
                    "Proxy response processing error: %s %s (upstream status=%s): %s: %s",
                    request.method,
                    request.path,
                    resp.status_code,
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                return HTTPResponse(500)

        # 固定値のレスポンス
        elif route.type == "raw":
            if route.respond:
                logger.debug("route=%s", pretty_block(route))
                return HTTPResponse(route.respond.status, route.respond.body)
            return HTTPResponse(500)

        # リダイレクト
        elif route.type == "redirect":
            logger.debug("REDIRECT")
            logger.debug("route.redirect=%s", pretty_block(route.redirect))
            redirect_url = route.redirect.url
            if "$request_uri" in redirect_url:
                safe_path = _sanitize_redirect_value(request.path)
                redirect_url = redirect_url.replace("$request_uri", safe_path)
                logger.debug("Rewrite URL %s", redirect_url)

            return HTTPResponse(
                status=route.redirect.code,
                header={"Location": _sanitize_redirect_value(redirect_url)},
            )
        else:
            return HTTPResponse(500)

    except PermissionError:
        logger.warning("Permission error while resolving route", exc_info=True)
        return HTTPResponse(403)
    except Exception:
        logger.exception("Unexpected error while resolving route")
        return HTTPResponse(500)


def _strip_trailing_slash(path: str) -> str:
    if path.endswith("/") and path != "/":
        return path[:-1]
    return path


def _join_root_and_relative(root: str, relative: str) -> str:
    root_path = _strip_trailing_slash(root)
    rel_path = relative.lstrip("/")

    if root_path == "/":
        return "/" + rel_path
    if rel_path == "":
        return root_path
    return root_path + "/" + rel_path


def _extract_absolute_uri_path(path: str) -> str:
    scheme_idx = path.find("://")
    if scheme_idx == -1:
        return path

    path_idx = path.find("/", scheme_idx + 3)
    if path_idx == -1:
        return "/"
    return path[path_idx:]


def _get_file_meta(
    file_path: str, stat_result=None
) -> Optional[tuple[os.stat_result, str, str]]:
    try:
        st = stat_result if stat_result is not None else os.stat(file_path)
    except (FileNotFoundError, PermissionError):
        return None

    mtime_ns = st.st_mtime_ns
    size = st.st_size
    mode = st.st_mode

    cached = _FILE_META_CACHE.get(file_path)
    if cached and cached[0] == mtime_ns and cached[1] == size and cached[2] == mode:
        return st, cached[3], cached[4]

    last_modified = formatdate(st.st_mtime, usegmt=True)
    etag_base = f"{mtime_ns:x}-{size:x}"
    _FILE_META_CACHE[file_path] = (mtime_ns, size, mode, last_modified, etag_base)
    return st, last_modified, etag_base


def _extract_query_string(raw_path: str) -> str:
    """リクエストパスからクエリストリング部分を抽出する。なければ空文字。"""
    if "://" in raw_path:
        raw_path = _extract_absolute_uri_path(raw_path)
    query_idx = raw_path.find("?")
    if query_idx == -1:
        return ""
    qs = raw_path[query_idx + 1 :]
    # フラグメントを除去
    frag_idx = qs.find("#")
    if frag_idx != -1:
        qs = qs[:frag_idx]
    return qs


def normalize_request_path(raw_path: str) -> str:
    path = raw_path or "/"

    if "://" in path:
        path = _extract_absolute_uri_path(path)

    query_idx = path.find("?")
    if query_idx != -1:
        path = path[:query_idx]

    fragment_idx = path.find("#")
    if fragment_idx != -1:
        path = path[:fragment_idx]

    if not path.startswith("/"):
        path = "/" + path

    segments = []
    for segment in path.split("/"):
        if segment == "":
            continue
        segments.append(segment)

    if not segments:
        return "/"
    return "/" + "/".join(segments)


def build_server_file_path(server_root: str, request_path: str) -> str:
    path = request_path or "/"
    if not path.startswith("/"):
        path = "/" + path

    safe_segments = []
    for segment in path.split("/"):
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            raise PermissionError("path traversal detected")
        safe_segments.append(segment)

    return _join_root_and_relative(server_root, "/".join(safe_segments))


def _find_directory_index_meta(
    directory_path: str, route_index: Optional[list[str]]
) -> Optional[tuple[str, os.stat_result, str, str]]:
    index_candidates = route_index or ["index.html"]

    for raw_candidate in index_candidates:
        if not isinstance(raw_candidate, str):
            continue

        candidate = raw_candidate.strip().lstrip("/")
        if not candidate:
            continue

        safe_segments = []
        rejected = False
        for segment in candidate.split("/"):
            if segment in {"", "."}:
                continue
            if segment == "..":
                rejected = True
                break
            safe_segments.append(segment)
        if rejected or not safe_segments:
            continue

        index_path = os.path.join(directory_path, *safe_segments)
        meta = _get_file_meta(index_path)
        if meta is None:
            continue

        index_stat, last_modified, etag_base = meta
        if statmod.S_ISDIR(index_stat.st_mode):
            continue

        return index_path, index_stat, last_modified, etag_base

    return None


def _get_header_case_insensitive(headers: dict, name: str, default: str = "") -> str:
    if name in headers:
        return headers[name]

    lowered_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered_name:
            return value
    return default


def _drop_header_case_insensitive(headers: dict, name: str) -> None:
    lowered_name = name.lower()
    for key in list(headers.keys()):
        if key.lower() == lowered_name:
            headers.pop(key, None)


def _apply_headers_config(
    response_headers: dict, headers_config: Optional[HeadersConfig]
) -> None:
    if not headers_config:
        return

    for raw_name in headers_config.remove:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if name:
            _drop_header_case_insensitive(response_headers, name)

    for raw_key, raw_value in headers_config.add.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not key:
            continue
        _drop_header_case_insensitive(response_headers, key)
        response_headers[key] = str(raw_value)


def _join_etag_with_encoding(base_etag: Optional[str], encoding: str) -> Optional[str]:
    if not base_etag:
        return None
    if encoding:
        return f"{base_etag}-{encoding}"
    return base_etag


def _format_etag_header(etag: Optional[str]) -> Optional[str]:
    if not etag:
        return None
    return f'"{etag}"'


def _apply_route_access_control(request: HTTPRequest, route) -> Optional[HTTPResponse]:
    security = route.security
    if not security:
        return None

    deny_all = bool(getattr(security, "deny_all", False))
    if not deny_all:
        logger.debug("Access SKIP: deny_all is disabled")
        return None

    ip_allow = list(getattr(security, "ip_allow", []) or [])
    if not ip_allow:
        logger.debug("Access NG: deny_all enabled and ip_allow is empty")
        return HTTPResponse(403)

    try:
        ip = ipaddress.ip_address(request.remote_addr)
    except ValueError:
        logger.debug("Access NG: invalid remote addr=%s", request.remote_addr)
        return HTTPResponse(403)

    for allow_cidr in ip_allow:
        network = ipaddress.ip_network(allow_cidr, strict=False)
        if ip in network:
            logger.debug("Access OK: %s in %s", ip, network)
            return None

    logger.debug("Access NG: %s is not in allow list", ip)
    return HTTPResponse(403)


def apply_response_headers_from_config(
    response: HTTPResponse, server: ServerConfig, request_path: str
) -> None:
    _apply_headers_config(response.headers, server.headers)

    route = find_best_route(server, normalize_request_path(request_path))
    if route:
        _apply_headers_config(response.headers, route.headers)


# ファイルパスからContent-Typeを判定し、テキスト/バイナリを返す
def get_content_type(file_path: str) -> tuple[str, bool]:
    dot_idx = file_path.rfind(".")
    ext = file_path[dot_idx:].lower() if dot_idx != -1 else ""
    # 辞書にない場合はデフォルト値を返す (getメソッドの活用)
    return MIME_MAP.get(ext, ("application/octet-stream", True))


def get_last_modified(path, absolute_path: bool = False, stat_result=None):
    full_path = path if absolute_path else _join_root_and_relative(STATIC_DIR, path)
    meta = _get_file_meta(full_path, stat_result=stat_result)
    if meta is None:
        return None
    return meta[1]


# routeing順序を考慮し、長い順から順番にマッチさせる。
def find_best_route(server, request_path_str: str):
    best = None
    for route in server.routes:
        route_path = route.path
        matched = False
        if route_path == "/":
            matched = True
        elif request_path_str == route_path:
            matched = True
        elif request_path_str.startswith(route_path):
            next_idx = len(route_path)
            if len(request_path_str) > next_idx and request_path_str[next_idx] == "/":
                matched = True

        if matched and (best is None or len(route_path) > len(best.path)):
            best = route
    return best


def generage_file_etag(path, absolute_path: bool = False, stat_result=None):
    full_path = path if absolute_path else _join_root_and_relative(STATIC_DIR, path)
    meta = _get_file_meta(full_path, stat_result=stat_result)
    if meta is None:
        return None
    return meta[2]


# リストから優先される圧縮方式を取得
def get_preferred_encoding(
    accept_encoding: str, compression_priority: list[str]
) -> str:
    for encoding in compression_priority:
        if encoding in accept_encoding:
            return encoding
    return ""


def check_cache_if_none_match(request: HTTPRequest, current_etag: Optional[str]):
    if not current_etag:
        return False

    raw_tag = _get_header_case_insensitive(request.headers, "if-none-match")
    if not raw_tag:
        return False

    raw_tag = raw_tag.strip()
    if raw_tag == "*":
        return True

    for tag in raw_tag.split(","):
        candidate = tag.strip()
        if weak_etag_equal(candidate, current_etag):
            return True

    return False


def check_cache_if_modified_since(
    request: HTTPRequest, last_modified_ts: float, if_none_match_supported: bool = True
) -> bool:
    if request.method not in {"GET", "HEAD"}:
        return False

    # RFC 2616 14.26: If-None-Match がある場合は If-Modified-Since を無視
    if if_none_match_supported and _get_header_case_insensitive(
        request.headers, "if-none-match"
    ):
        return False

    raw_date = _get_header_case_insensitive(request.headers, "if-modified-since")
    if not raw_date:
        return False

    try:
        since_dt = parsedate_to_datetime(raw_date.strip())
    except (TypeError, ValueError):
        return False

    if since_dt is None:
        return False

    if since_dt.tzinfo is None:
        since_ts = since_dt.replace(tzinfo=timezone.utc).timestamp()
    else:
        since_ts = since_dt.timestamp()

    # Last-Modified は秒精度のため、比較も秒単位に丸める
    return int(last_modified_ts) <= int(since_ts)


# RFC 7230 Section 6.1 で定義された静的 hop-by-hop ヘッダー
_STATIC_HOP_BY_HOP_HEADERS = frozenset([
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
])


def _strip_hop_by_hop_headers(headers: dict) -> None:
    """RFC 7230 Section 6.1 準拠の hop-by-hop ヘッダー除去。

    1. Connection ヘッダーに列挙された connection-option を動的に除去
    2. Connection ヘッダー自体を除去
    3. 静的 hop-by-hop ヘッダーを除去（安全策）
    """
    # 1. Connection ヘッダーから動的 hop-by-hop を取得して除去
    connection_value = _get_header_case_insensitive(headers, "connection", "")
    for opt in connection_value.split(","):
        opt = opt.strip().lower()
        if opt:
            _drop_header_case_insensitive(headers, opt)

    # 2. Connection ヘッダー自体を除去
    _drop_header_case_insensitive(headers, "connection")

    # 3. 静的 hop-by-hop ヘッダーを除去（安全策）
    for name in _STATIC_HOP_BY_HOP_HEADERS:
        _drop_header_case_insensitive(headers, name)


def _strip_proxy_response_headers(headers: dict) -> None:
    """レスポンスヘッダーから hop-by-hop および実装方針上不要なヘッダーを除去する。"""
    _strip_hop_by_hop_headers(headers)
    # httpx が自動デコード済みのため Content-Encoding は除去
    _drop_header_case_insensitive(headers, "content-encoding")
    # Content-Length / Date / Server はプロキシ側で付与し直す
    _drop_header_case_insensitive(headers, "content-length")
    _drop_header_case_insensitive(headers, "date")
    _drop_header_case_insensitive(headers, "server")


def _apply_backend_headers(
    headers: dict, headers_config: Optional[HeadersConfig], request: HTTPRequest
) -> None:
    """backend.headers 設定をリクエストヘッダーに適用する。

    変数展開:
      $remote_addr  → クライアントの IP アドレス
    """
    if not headers_config:
        return

    variables = {
        "$remote_addr": request.remote_addr,
    }

    for name in headers_config.remove:
        name = name.strip()
        if name:
            _drop_header_case_insensitive(headers, name)

    for key, value in headers_config.add.items():
        key = key.strip()
        if not key:
            continue
        for var, val in variables.items():
            value = value.replace(var, val)
        _drop_header_case_insensitive(headers, key)
        headers[key] = value


def _rewrite_set_cookie_header(cookie: str, from_host: str, is_https: bool) -> str:
    """Set-Cookie ヘッダー値のドメインと SameSite を書き換える。

    - Domain=<upstream host> を削除（ブラウザがプロキシのホストをデフォルト使用する）
    - HTTPS でない場合: SameSite=None → SameSite=Lax、Secure 属性を除去
    """
    # Domain=<from_host> を除去（ポート付き・なし両対応）
    from_host_no_port = from_host.split(":")[0]
    cookie = re.sub(
        r";\s*[Dd]omain\s*=\s*\.?" + re.escape(from_host_no_port) + r"(?=\s*;|\s*$)",
        "",
        cookie,
    )

    if not is_https:
        # SameSite=None → SameSite=Lax
        cookie = re.sub(
            r";\s*[Ss]ame[Ss]ite\s*=\s*None",
            "; SameSite=Lax",
            cookie,
            flags=re.IGNORECASE,
        )
        # Secure 属性を除去
        cookie = re.sub(
            r";\s*[Ss]ecure(?=\s*;|\s*$)",
            "",
            cookie,
            flags=re.IGNORECASE,
        )

    return cookie


_REWRITE_TEXT_TYPES = (
    "text/",
    "application/javascript",
    "application/json",
    "application/xml",
    "application/xhtml",
)


def _rewrite_proxy_urls(
    body: bytes,
    content_type: str,
    headers: dict,
    rewrite_from: str,
    request: HTTPRequest,
    server: "ServerConfig",
) -> tuple[bytes, dict]:
    """upstreamのURLをプロキシのURLに置換する。"""
    scheme = "https" if server.tls and server.tls.enabled else "http"
    host = _get_header_case_insensitive(request.headers, "host") or f"{server.host}:{server.port}"
    rewrite_to = f"{scheme}://{host}"

    from_bytes = rewrite_from.rstrip("/").encode()
    to_bytes = rewrite_to.rstrip("/").encode()

    logger.debug("rewrite_url: %s -> %s", from_bytes, to_bytes)

    # テキスト系コンテンツのみボディを置換
    ct_lower = content_type.lower()
    if any(ct_lower.startswith(t) for t in _REWRITE_TEXT_TYPES):
        body = body.replace(from_bytes, to_bytes)

    # Locationヘッダー等も置換
    rewritten_headers = {}
    for k, v in headers.items():
        if isinstance(v, str) and rewrite_from.rstrip("/") in v:
            v = v.replace(rewrite_from.rstrip("/"), rewrite_to.rstrip("/"))
        rewritten_headers[k] = v

    return body, rewritten_headers


# ---------------------------------------------------------------------------
# ヘッダーインジェクション防止
# ---------------------------------------------------------------------------

_REDIRECT_UNSAFE_RE = re.compile(r"[\r\n\x00]")


def _sanitize_redirect_value(value: str) -> str:
    """リダイレクト先URLから改行・NUL文字を除去してヘッダーインジェクションを防止する。"""
    return _REDIRECT_UNSAFE_RE.sub("", value)


# ---------------------------------------------------------------------------
# SSRF 防止
# ---------------------------------------------------------------------------

_SSRF_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:127.0.0.0/104"),
    ipaddress.ip_network("::ffff:10.0.0.0/104"),
    ipaddress.ip_network("::ffff:172.16.0.0/108"),
    ipaddress.ip_network("::ffff:192.168.0.0/112"),
)


def _is_private_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """アドレスがプライベート/予約済みネットワークに属するか判定する。"""
    for network in _SSRF_BLOCKED_NETWORKS:
        if addr in network:
            return True
    return False


def _is_allowed_by_ssrf_allowlist(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    allowed_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    """アドレスが SSRF 許可リストに含まれるか判定する。"""
    for network in allowed_networks:
        if addr in network:
            return True
    return False


def build_ssrf_allowlist(
    upstream_url: str, extra_allow: list[str] | None = None
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """設定で明示されたupstreamホストとssrf_allowからSSRF許可ネットワーク一覧を構築する。

    - upstream URL のホスト（IP リテラル、またはDNS解決結果）を自動で許可
    - ssrf_allow に記載された IP/CIDR を追加許可
    """
    allowed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    # upstream ホストを許可リストに追加
    parsed = urlparse(upstream_url)
    hostname = parsed.hostname
    if hostname:
        try:
            addr = ipaddress.ip_address(hostname)
            # 単一IPを /32 or /128 としてネットワーク化
            allowed.append(
                ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}", strict=False)
            )
        except ValueError:
            # ドメイン名 — DNS 解決して許可
            try:
                addrinfo = socket.getaddrinfo(
                    hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                for _family, _type, _proto, _canonname, sockaddr in addrinfo:
                    try:
                        addr = ipaddress.ip_address(sockaddr[0])
                        allowed.append(
                            ipaddress.ip_network(
                                f"{addr}/{addr.max_prefixlen}", strict=False
                            )
                        )
                    except ValueError:
                        continue
            except socket.gaierror:
                pass

    # ssrf_allow の明示エントリを追加
    for entry in extra_allow or []:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                allowed.append(ipaddress.ip_network(entry, strict=False))
            else:
                addr = ipaddress.ip_address(entry)
                allowed.append(
                    ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}", strict=False)
                )
        except ValueError:
            # ドメイン名の場合はDNS解決
            try:
                addrinfo = socket.getaddrinfo(
                    entry, None, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                for _family, _type, _proto, _canonname, sockaddr in addrinfo:
                    try:
                        addr = ipaddress.ip_address(sockaddr[0])
                        allowed.append(
                            ipaddress.ip_network(
                                f"{addr}/{addr.max_prefixlen}", strict=False
                            )
                        )
                    except ValueError:
                        continue
            except socket.gaierror:
                logger.warning("ssrf_allow entry DNS resolution failed: %s", entry)

    return tuple(allowed)


def _validate_upstream_target(
    url: str,
    ssrf_allowlist: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network, ...
    ] = (),
) -> None:
    """upstream URL のホストが内部ネットワークを指していないか検証する。

    DNS 解決を行い、解決結果がプライベート IP の場合は拒否する。
    ただし ssrf_allowlist に含まれる IP は許可する。
    これにより DNS rebinding を含む SSRF 攻撃を軽減する。
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("upstream URL にホスト名がありません")

    # IP リテラルの場合は即座に判定
    try:
        addr = ipaddress.ip_address(hostname)
        if _is_private_ip(addr) and not _is_allowed_by_ssrf_allowlist(
            addr, ssrf_allowlist
        ):
            raise ValueError(
                f"upstream ホスト {hostname} はプライベートネットワークです"
            )
        return
    except ValueError:
        if "プライベート" in str(hostname):
            raise
        # ドメイン名 — DNS 解決へ進む

    # DNS 解決して全アドレスを検証
    try:
        addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"upstream ホスト {hostname} の DNS 解決に失敗: {e}") from e

    for family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_private_ip(addr) and not _is_allowed_by_ssrf_allowlist(
            addr, ssrf_allowlist
        ):
            raise ValueError(
                f"upstream ホスト {hostname} はプライベート IP {addr} に解決されました"
            )


def _sanitize_proxy_path(path: str) -> str:
    """プロキシリクエストパスから URL 操作に使われうる文字を除去する。

    - '@' はユーザー情報注入 (http://evil@internal/) に使われる
    - '\\' は一部パーサーでスキーム区切りとして扱われる
    - 連続スラッシュの正規化
    """
    # '@' と '\\' を除去
    sanitized = path.replace("@", "").replace("\\", "/")
    # 連続スラッシュを正規化
    while "//" in sanitized:
        sanitized = sanitized.replace("//", "/")
    return sanitized
