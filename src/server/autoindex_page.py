import hashlib
import html
import os
from email.utils import formatdate
from urllib.parse import quote

from .config_model import ServerConfig

_AUTOINDEX_ETAG_VERSION = "v1"
_AUTOINDEX_SNAPSHOT: dict[
    str, tuple[tuple[tuple[str, bool], ...], float, str, str]
] = {}
_AUTOINDEX_PAGE_BODY_CACHE: dict[tuple[str, str], bytes] = {}
_AUTOINDEX_PRIMED_ROOTS: set[str] = set()


def _normalize_absolute_path(path: str) -> str:
    return os.path.abspath(path)


def _normalize_request_path(path: str) -> str:
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")
    return path or "/"


def _safe_join_root_and_request_path(root: str, request_path: str) -> str:
    path = request_path or "/"
    if not path.startswith("/"):
        path = "/" + path

    safe_segments: list[str] = []
    for segment in path.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise PermissionError("path traversal detected")
        safe_segments.append(segment)

    if not safe_segments:
        return root
    return os.path.join(root, *safe_segments)


def _snapshot_directory(directory_path: str) -> None:
    try:
        with os.scandir(directory_path) as entries:
            listing = [
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in entries
                if entry.name not in {".", ".."}
            ]
    except OSError:
        return

    listing.sort(key=lambda item: (not item[1], item[0].lower(), item[0]))

    try:
        st = os.stat(directory_path)
        mtime = st.st_mtime
        mtime_ns = st.st_mtime_ns
    except OSError:
        mtime = 0.0
        mtime_ns = 0

    normalized_path = _normalize_absolute_path(directory_path)
    digest_source = [f"{_AUTOINDEX_ETAG_VERSION}:{normalized_path}:{mtime_ns:x}"]
    for name, is_dir in listing:
        digest_source.append(("d:" if is_dir else "f:") + name)
    digest = hashlib.sha1("|".join(digest_source).encode("utf-8")).hexdigest()[:16]

    _AUTOINDEX_SNAPSHOT[normalized_path] = (
        tuple(listing),
        mtime,
        formatdate(mtime, usegmt=True),
        f"auto-{_AUTOINDEX_ETAG_VERSION}-{digest}",
    )


def prime_autoindex_cache_for_server(server: ServerConfig) -> None:
    if not server.root:
        return

    server_root = _normalize_absolute_path(server.root)
    if not os.path.isdir(server_root):
        return

    for route in server.routes:
        if route.type != "static" or not route.autoindex:
            continue

        try:
            scan_root = _safe_join_root_and_request_path(server_root, route.path)
        except PermissionError:
            continue

        scan_root = _normalize_absolute_path(scan_root)
        if scan_root in _AUTOINDEX_PRIMED_ROOTS:
            continue
        if not os.path.isdir(scan_root):
            continue

        for current_dir, child_dirs, _child_files in os.walk(scan_root):
            child_dirs.sort()
            _snapshot_directory(current_dir)
        _AUTOINDEX_PRIMED_ROOTS.add(scan_root)


def _build_parent_href(request_path: str) -> str:
    if request_path == "/":
        return ""

    slash_index = request_path.rfind("/")
    if slash_index <= 0:
        return "/"
    parent = request_path[:slash_index]
    return parent or "/"


def _build_child_href(request_path: str, name: str, is_dir: bool) -> str:
    encoded_name = quote(name, safe="")
    if request_path == "/":
        href = f"/{encoded_name}"
    else:
        href = f"{request_path.rstrip('/')}/{encoded_name}"

    if is_dir:
        href += "/"
    return href


def _build_autoindex_html(
    request_path: str, listing: tuple[tuple[str, bool], ...]
) -> str:
    safe_request_path = html.escape(request_path)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '  <meta charset="utf-8">',
        f"  <title>Index of {safe_request_path}</title>",
        "  <style>",
        "    body{margin:40px;}",
        "    ul{list-style:none;padding-left:0;}",
        "    li{margin:4px 0;}",
        "    a{text-decoration:none;}",
        "    a:hover{text-decoration:underline;}",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>Index of {safe_request_path}</h1>",
        "  <hr>",
        "  <ul>",
    ]

    parent_href = _build_parent_href(request_path)
    if parent_href:
        lines.append(
            f'    <li><a href="{html.escape(parent_href, quote=True)}">../</a></li>'
        )

    for name, is_dir in listing:
        display_name = f"{name}/" if is_dir else name
        href = _build_child_href(request_path, name, is_dir)
        lines.append(
            f'    <li><a href="{html.escape(href, quote=True)}">'
            f"{html.escape(display_name)}</a></li>"
        )

    lines.extend(
        [
            "  </ul>",
            "  <hr>",
            "  <address>MyHTTPServer</address>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def get_cached_autoindex_page(
    directory_path: str, request_path: str
) -> tuple[bytes, str, float, str] | None:
    normalized_directory = _normalize_absolute_path(directory_path)
    snapshot = _AUTOINDEX_SNAPSHOT.get(normalized_directory)
    if snapshot is None:
        return None

    listing, mtime, last_modified, etag = snapshot
    normalized_request = _normalize_request_path(request_path)
    body_key = (normalized_directory, normalized_request)
    cached_body = _AUTOINDEX_PAGE_BODY_CACHE.get(body_key)
    if cached_body is not None:
        return cached_body, last_modified, mtime, etag

    body = _build_autoindex_html(normalized_request, listing).encode("utf-8")
    _AUTOINDEX_PAGE_BODY_CACHE[body_key] = body
    return body, last_modified, mtime, etag
