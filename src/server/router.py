import ipaddress
import os
import stat as statmod
import traceback
from datetime import timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Optional

import httpx
from icecream import ic

from .config_model import ServerConfig
from .etag_utils import weak_etag_equal
from .FileCache import FileCache
from .protocol import HTTPRequest, HTTPResponse
from .range_requests import (
    build_multipart_byteranges_body,
    format_content_range,
    format_unsatisfied_content_range,
    parse_range_header,
    should_apply_range_for_if_range,
)

# 静的ファイルのルートディレクトリ
_CWD = os.getcwd()
STATIC_DIR = (_CWD[:-1] if _CWD.endswith("/") and _CWD != "/" else _CWD) + "/html"
_FILE_META_CACHE: dict[str, tuple[int, int, int, str, str]] = {}

cache = FileCache()


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


def _get_header_case_insensitive(headers: dict, name: str, default: str = "") -> str:
    if name in headers:
        return headers[name]

    lowered_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered_name:
            return value
    return default


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


async def resolve_route(
    request: HTTPRequest, server: ServerConfig, encoding: str = ""
) -> HTTPResponse:

    request_path = normalize_request_path(request.path)

    try:
        route = find_best_route(server, request_path)

        allow_methods = route.methods
        ic(allow_methods)

        # OPTIONSメソッドは先に確認
        if request.method == "OPTIONS":
            allowed_methods_str = ", ".join(allow_methods or ["*"])
            return HTTPResponse(204, header={"Allow": allowed_methods_str})

        # 許可メソッドのチェック
        if allow_methods and request.method not in allow_methods:
            allowed_methods_str = ", ".join(route.methods)

            ic(allowed_methods_str)
            return HTTPResponse(
                status=405,
                body=f"405 Method Not Allowed\nAllowed: {allowed_methods_str}",
                header={"Allow": allowed_methods_str},
            )

        if not route:
            return HTTPResponse(404)

        if route.type == "static":
            if route.security:
                ip = ipaddress.ip_address(request.remote_addr)
                network = ipaddress.ip_network(route.security.ip_allow[0], strict=False)
                if ip in network:
                    ic(f"Access OK: {ip} in {network}")
                    pass
                else:
                    return HTTPResponse(400)

            if request_path == "/":
                index_file = route.index[0] if route.index else "index.html"
                server_file_path = build_server_file_path(
                    server.root, "/" + index_file.lstrip("/")
                )
            else:
                server_file_path = build_server_file_path(server.root, request_path)

            meta = _get_file_meta(server_file_path)
            if meta is None:
                return HTTPResponse(404)

            stat_result, last_modify, base_etag = meta
            if statmod.S_ISDIR(stat_result.st_mode):
                return HTTPResponse(status=301, header={"Location": "index.html"})

            range_header = _get_header_case_insensitive(request.headers, "range")
            if_range_header = _get_header_case_insensitive(request.headers, "if-range")
            if range_header:
                ic(range_header)

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

            content_type, is_binary = get_content_type(server_file_path)

            if is_binary:
                content = cache.get_cached(server_file_path, mode="rb")
                if content is cache.MISS:
                    content = await cache.read_from_disk(server_file_path, mode="rb")
            else:
                text = cache.get_cached(server_file_path, mode="r")
                if text is cache.MISS:
                    text = await cache.read_from_disk(server_file_path, mode="r")
                content = text.encode("utf-8")

            response_headers = {
                "Last-Modified": last_modify,
                "Cache-Control": "max-age=3600",
                "Accept-Ranges": "bytes",
            }
            if etag_header:
                response_headers["ETag"] = etag_header

            if range_header and should_apply_range_for_if_range(
                if_range_header, current_etag, last_modify
            ):
                parsed_range = parse_range_header(range_header, len(content))

                # bytes以外はRangeを無視し、通常の200を返す
                if parsed_range.unit_supported:
                    if not parsed_range.is_valid or not parsed_range.ranges:
                        headers = response_headers | {
                            "Content-Range": format_unsatisfied_content_range(
                                len(content)
                            )
                        }
                        response = HTTPResponse(416, b"", headers, content_type)
                        response.disable_compression()
                        return response

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
                        if request.method == "HEAD":
                            partial_body = b""

                        response = HTTPResponse(
                            206, partial_body, headers, content_type
                        )
                        response.disable_compression()
                        return response

                    multipart_type, multipart_body = build_multipart_byteranges_body(
                        content=content,
                        ranges=parsed_range.ranges,
                        content_type=content_type,
                        resource_size=len(content),
                    )
                    if request.method == "HEAD":
                        multipart_body = b""

                    response = HTTPResponse(
                        206,
                        multipart_body,
                        response_headers,
                        multipart_type,
                    )
                    response.disable_compression()
                    return response

            if request.method == "HEAD":
                content = b""

            return HTTPResponse(
                200,
                content,
                response_headers,
                content_type,
            )

        # リバースプロキシ
        elif route.type == "proxy":
            send_header = dict(request.headers)
            upstrean_url = route.backend.upstream

            proxy_request_path = request_path[len(route.path) :]

            ic(proxy_request_path)
            ic(upstrean_url)
            for header_name in list(send_header):
                if header_name.lower() == "host":
                    send_header.pop(header_name)

            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(
                        method=request.method,
                        url=f"{upstrean_url}{proxy_request_path}",
                        headers=send_header,
                        content=request.body,
                        timeout=10.0,
                    )

                ic(resp.status_code)
                ic(dict(resp.headers))

                _content_type = resp.headers["content-type"]
                resp.headers.pop("content-type", None)
                resp = drop_proxy_header(resp)

                return HTTPResponse(
                    status=resp.status_code,
                    body=resp.content,  # bytes
                    content_type=_content_type,
                    header=dict(resp.headers),
                )
            except httpx.RequestError as e:
                ic(f"Upstream error: {e}")
                traceback.print_exc()
                return HTTPResponse(504)

        # 固定値のレスポンス
        elif route.type == "raw":
            if route.respond:
                ic(route)
                return HTTPResponse(route.respond.status, route.respond.body)
            return HTTPResponse(500)

        # リダイレクト
        elif route.type == "redirect":
            ic("REDIRECT")
            ic(route.redirect)
            redirect_url = route.redirect.url
            if "$request_uri" in redirect_url:
                redirect_url = redirect_url.replace("$request_uri", request.path)
                ic(f"Rewrite URL {redirect_url}")

            return HTTPResponse(
                status=route.redirect.code, header={"Location": redirect_url}
            )
        else:
            return HTTPResponse(500)

    except PermissionError:
        traceback.print_exc()
        return HTTPResponse(403)
    except Exception:
        traceback.print_exc()
        return HTTPResponse(500)


# ファイルパスからContent-Typeを判定し、テキスト/バイナリを返す
def get_content_type(file_path: str) -> tuple[str, bool]:
    # 拡張子と（MIMEタイプ, is_binary）の対応表
    MIME_MAP = {
        ".html": ("text/html; charset=utf-8", False),
        ".htm": ("text/html; charset=utf-8", False),
        ".css": ("text/css; charset=utf-8", False),
        ".js": ("application/javascript; charset=utf-8", False),
        ".json": ("application/json; charset=utf-8", False),
        ".txt": ("text/plain; charset=utf-8", False),
        ".png": ("image/png", True),
        ".jpg": ("image/jpeg", True),
        ".jpeg": ("image/jpeg", True),
        ".gif": ("image/gif", True),
        ".svg": ("image/svg+xml", False),  # SVGはXMLなのでテキスト
        ".pdf": ("application/pdf", True),
    }

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
