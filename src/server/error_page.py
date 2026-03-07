import hashlib
from email.utils import formatdate

_ERROR_PAGE_BODY_CACHE: dict[tuple[int, str], bytes] = {}
_ERROR_PAGE_LAST_MODIFIED = formatdate(usegmt=True)
_ERROR_PAGE_ETAG_VERSION = "v1"


def build_error_page_html(status: int, reason: str) -> str:
    template = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{CODE} {REASON}</title>
  <style>
    body{{
      margin: 40px;
    }}
  </style>
</head>
<body>
  <h1>{CODE} {REASON}</h1>
  <hr>
  <address>MyHTTPServer/0.1</address>
</body>
</html>"""
    return template.format(CODE=status, REASON=reason).lstrip()


def get_cached_error_page_body(status: int, reason: str) -> bytes:
    key = (status, reason)
    cached = _ERROR_PAGE_BODY_CACHE.get(key)
    if cached is not None:
        return cached

    body = build_error_page_html(status, reason).encode("utf-8")
    _ERROR_PAGE_BODY_CACHE[key] = body
    return body


def get_error_page_etag_opaque(status: int, reason: str) -> str:
    raw = f"{_ERROR_PAGE_ETAG_VERSION}:{status}:{reason}".encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return f"err-{_ERROR_PAGE_ETAG_VERSION}-{status:x}-{digest}"


def get_error_page_last_modified() -> str:
    return _ERROR_PAGE_LAST_MODIFIED
