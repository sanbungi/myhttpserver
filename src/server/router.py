import ipaddress
import os
import traceback
from email.utils import formatdate

import httpx
from icecream import ic

from config_model import ServerConfig
from FileCache import FileCache

from .protocol import HTTPRequest, HTTPResponse

# 静的ファイルのルートディレクトリ
STATIC_DIR = os.path.join(os.getcwd(), "html")

cache = FileCache()


def normalize_request_path(raw_path: str) -> str:
    path = (raw_path or "/").split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/":
        while "//" in path:
            path = path.replace("//", "/")
        path = path.rstrip("/")
    return path or "/"


def build_server_file_path(server_root: str, request_path: str) -> str:
    root_path = os.path.abspath(server_root)
    relative_path = request_path.lstrip("/")
    candidate_path = os.path.abspath(os.path.join(root_path, relative_path))
    if os.path.commonpath([root_path, candidate_path]) != root_path:
        raise PermissionError("path traversal detected")
    return candidate_path


async def resolve_route(request: HTTPRequest, server: ServerConfig) -> HTTPResponse:

    request_path = normalize_request_path(request.path)

    try:
        if request.method == "OPTIONS":
            ic("OPTIONS CALLS")
            return HTTPResponse(204, header={"Allow": "GET, HEAD, OPTIONS"})

        route = find_best_route(server, request_path)
        # ic(route)

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

            # etag = generage_file_etag(request.path)

            cache_hit = check_cache_if_none_match(request, request_path)
            if cache_hit:
                ic(cache_hit)
                last_modify = get_last_modified(request_path)
                return HTTPResponse(
                    304,
                    header={
                        "Last-Modified": last_modify,
                        "Cache-Control": "max-age=3600",
                    },
                )

            if request_path == "/":
                index_path = os.path.join(server.root, route.index[0])
                content = cache.get_cached(index_path, mode="r")
                if content is cache.MISS:
                    content = await cache.read_from_disk(index_path, mode="r")
                return HTTPResponse(
                    200,
                    str(content).encode("utf-8"),
                )

            server_file_path = build_server_file_path(server.root, request_path)
            ic(server_file_path)

            if os.path.isdir(server_file_path):
                return HTTPResponse(status=301, header={"Location": "index.html"})

            if not os.path.exists(server_file_path):
                return HTTPResponse(404)

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

            last_modify = get_last_modified(request_path)

            return HTTPResponse(
                200,
                content,
                {"Last-Modified": last_modify, "Cache-Control": "max-age=3600"},
                content_type,
            )

        # リバースプロキシ
        elif route.type == "proxy":
            send_header = dict(request.headers)
            send_header.pop("host", None)
            send_header.pop("Host", None)

            # 【重要】requests を httpx に置き換え
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(
                        method=request.method,
                        url=f"http://localhost:1234{request_path}",
                        headers=send_header,
                        content=request.body,  # httpxでは body ではなく content (または data/json)
                        timeout=10.0,
                    )

                ic(resp.status_code)
                ic(dict(resp.headers))
                # ic(resp.text) # textアクセスは再読み込みが発生する場合があるので注意

                return HTTPResponse(
                    resp.status_code,
                    resp.content,  # bytes
                    dict(
                        resp.headers
                    ),  # httpxのheadersは辞書風オブジェクトなので変換推奨
                )
            except httpx.RequestError as e:
                ic(f"Upstream error: {e}")
                traceback.print_exc()
                return None

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

    ext = os.path.splitext(str(file_path))[1].lower()
    # 辞書にない場合はデフォルト値を返す (getメソッドの活用)
    return MIME_MAP.get(ext, ("application/octet-stream", True))


def get_last_modified(path):
    path = "html" + path
    try:
        stat = os.stat(path)
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        return last_modified

    except (FileNotFoundError, PermissionError):
        traceback.print_exc()
        return None


# routeing順序を考慮し、長い順から順番にマッチさせる。
def find_best_route(server, request_path_str: str):
    for route in server.routes:
        route_path = route.path
        if route_path == "/":
            return route
        if request_path_str == route_path:
            return route
        if request_path_str.startswith(route_path):
            next_idx = len(route_path)
            if len(request_path_str) > next_idx and request_path_str[next_idx] == "/":
                return route
    return None


def generage_file_etag(path):
    path = "html" + path
    ic(path)
    try:
        stat = os.stat(path)

        mtime = int(stat.st_mtime)
        size = stat.st_size
        ic(mtime)
        ic(size)

        mtime_hex = hex(mtime)[2:]
        size_hex = hex(size)[2:]

        return f"{mtime_hex}-{size_hex}"
    except (FileNotFoundError, PermissionError):
        traceback.print_exc()
        return None


# リストから優先される圧縮方式を取得
def get_preferred_encoding(
    accept_encoding: str, compression_priority: list[str]
) -> str:
    for encoding in compression_priority:
        if encoding in accept_encoding:
            return encoding
    return ""


def check_cache_if_none_match(request: HTTPRequest, request_path: str):
    tag = request.headers.get("if-none-match", "")
    tag = tag.replace('"', "")
    if tag == "":
        return False

    ic(tag)

    # 圧縮化を同じロジックで判定
    accept_encoding = request.headers.get("accept-encoding", "")
    encoding = get_preferred_encoding(accept_encoding, ["gzip"])

    current_etag = generage_file_etag(request_path)
    if encoding:
        current_etag = f"{current_etag}-{encoding}"

    ic(current_etag)
    ic(tag)

    if current_etag == tag:
        return True

    return False
