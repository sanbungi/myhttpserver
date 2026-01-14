from dataclasses import dataclass, field
from typing import Union, Dict
import gzip
from io import BytesIO
import zstandard as zstd


class HTTPRequest:
    def __init__(self, method, path, version, headers, body):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.body = body

    def __repr__(self):
        headers_str = "\n".join(f"    {k}: {v}" for k, v in self.headers.items())
        return f"HTTPRequest(\n    method={self.method},\n    path={self.path},\n    version={self.version},\n    headers={{\n{headers_str}\n    }},\n    body={repr(self.body)}\n)"


@dataclass
class HTTPResponse:
    status_code: int
    content_type: str
    content: Union[str, bytes]
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def content_length(self) -> int:
        if isinstance(self.content, bytes):
            return len(self.content)
        return len(self.content.encode("utf-8"))


def parse_request(request_text: str) -> HTTPRequest:
    lines = request_text.split("\r\n")

    # Request Line parsing
    request_line = lines[0]
    method, path, version = request_line.split(" ")

    headers = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == "":
            i += 1
            break
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key] = value
        i += 1

    body = "\r\n".join(lines[i:])

    return HTTPRequest(method, path, version, headers, body)


# HTTPステータスコードから理由フレーズを返す
def get_http_reason_phrase(status_code):
    status_map = {
        # 1xx
        100: "Continue",
        101: "Switching Protocols",
        # 2xx
        200: "OK",
        201: "Created",
        202: "Accepted",
        204: "No Content",
        # 3xx
        301: "Moved Permanently",
        302: "Found",
        304: "Not Modified",
        307: "Temporary Redirect",
        # 4xx
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        429: "Too Many Requests",
        # 5xx
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }

    # 辞書にない場合は "Unknown" を返す
    return status_map.get(status_code, "Unknown Status Code")


# ファイルパスからContent-Typeを判定し、テキスト/バイナリを返す
def get_content_type(file_path: str) -> tuple[str, bool]:
    from pathlib import Path

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

    ext = Path(file_path).suffix.lower()
    # 辞書にない場合はデフォルト値を返す (getメソッドの活用)
    return MIME_MAP.get(ext, ("application/octet-stream", True))


# Keep-Aliveを使うかをヘッダーとHTTPバージョンから判定
def get_keep_alive(request: HTTPRequest) -> bool:
    connection_header = request.headers.get("Connection", "").lower()

    # http1.0なら明示的にkeep-alive指定がない限りclose
    if request.version == "HTTP/1.0" and connection_header != "keep-alive":
        return False
    elif request.version == "HTTP/1.1" and connection_header == "close":
        return False
    else:
        return True


def response_301(location: str) -> HTTPResponse:
    return HTTPResponse(
        301,
        "text/plain; charset=utf-8",
        "301 Moved Permanently",
        {"Location": location},
    )


def response_200(content: bytes, content_type: str) -> HTTPResponse:
    return HTTPResponse(
        200,
        content_type,
        content,
    )


def response_404() -> HTTPResponse:
    return HTTPResponse(
        404,
        "text/plain; charset=utf-8",
        "404 Not Found",
    )


def response_500() -> HTTPResponse:
    return HTTPResponse(
        500,
        "text/plain; charset=utf-8",
        "500 Internal Server Error",
    )


def response_403() -> HTTPResponse:
    return HTTPResponse(
        403,
        "text/plain; charset=utf-8",
        "403 Forbidden",
    )

# リストから優先される圧縮方式を取得
def get_preferred_encoding(accept_encoding: str, compression_priority: list[str]) -> str:
    for encoding in compression_priority:
        if encoding in accept_encoding:
            return encoding
    return ""


def compress_content(content: bytes, encoding: str) -> bytes:
    if not isinstance(content, bytes):
        content = content.encode("utf-8")
    
    if encoding == "gzip":
        out = BytesIO()
        with gzip.GzipFile(fileobj=out, mode="wb") as f:
            f.write(content)
        return out.getvalue()
    elif encoding == "zstd":
        cctx = zstd.ZstdCompressor()
        return cctx.compress(content)
    else:
        return content
