import ipaddress
import logging
import os
import stat as statmod
from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Optional

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
            proxy_request_path = request_path[len(route.path) :]

            logger.debug("proxy_request_path=%s", proxy_request_path)
            logger.debug("upstream_url=%s", upstrean_url)

            # プロキシ先にリクエストを送る前にHostヘッダー削除
            send_header = dict(request.headers)
            for header_name in list(send_header):
                if header_name.lower() == "host":
                    send_header.pop(header_name)

            try:
                async with httpx.AsyncClient() as client:
                    # HACK timeoutが決め打ち
                    resp = await client.request(
                        method=request.method,
                        url=f"{upstrean_url}{proxy_request_path}",
                        headers=send_header,
                        content=request.body,
                        timeout=10.0,
                    )

                logger.debug("upstream status=%s", resp.status_code)
                logger.debug("upstream headers=%s", pretty_block(dict(resp.headers)))

                # 不要なヘッダー削除
                _content_type = resp.headers["content-type"]
                resp.headers.pop("content-type", None)
                resp = drop_proxy_header(resp)

                return HTTPResponse(
                    status=resp.status_code,
                    body=resp.content,
                    content_type=_content_type,
                    header=dict(resp.headers),
                )
            except httpx.RequestError as e:
                logger.exception("Upstream error: %s", e)
                return HTTPResponse(504)

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
                redirect_url = redirect_url.replace("$request_uri", request.path)
                logger.debug("Rewrite URL %s", redirect_url)

            return HTTPResponse(
                status=route.redirect.code, header={"Location": redirect_url}
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


def drop_proxy_header(resp: httpx.Response):
    # hop-by-hop header
    remove_header = [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ]
    for r in remove_header:
        resp.headers.pop(r, None)

    # 以下の削除は実装方針による
    resp.headers.pop("content-length", None)
    resp.headers.pop("date", None)
    resp.headers.pop("server", None)

    return resp
