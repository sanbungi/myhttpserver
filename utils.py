import gzip
from dataclasses import dataclass, field
from enum import Enum, auto
from io import BytesIO
from typing import Dict, Union

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


class HttpParseErrorCode(Enum):
    BAD_REQUEST_LINE = auto()
    BAD_HEADER_SYNTAX = auto()
    MISSING_HOST = auto()
    DUPLICATE_HOST = auto()
    INVALID_HOST = auto()


@dataclass
class HttpParseError(Exception):
    code: HttpParseErrorCode
    message: str
    status: int = 400


@dataclass
class HttpError(Exception):
    status: int
    code: str
    message: str


def preparse_guard(raw_request: str):
    if len(raw_request) == 0:
        raise HttpError(400, "NO CONTENT", "NO COTENT")

    # リクエストバイト制限を超えた場合に413を返す
    if len(raw_request) > 1024 * 1024 * 100:
        raise HttpError(413, "TOO LONG", "TOO LONG")


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


def vetify_request(request: HTTPRequest):
    print(request.method)

    headers = request.headers
    hosts = [headers["Host"]] if "Host" in headers else []
    if len(hosts) == 0:
        raise HttpError(400, "MISSING_HOST", "Host Header is requeired")
    if len(hosts) > 1:
        raise HttpError(400, "DUPLICATE_HOST", "Multiple Host headers")

    ALLOW_METHOD = ["GET"]
    if not any(request.method in s for s in ALLOW_METHOD):
        raise HttpError(405, "METHOD NOT ALLOWED", "Method Not Allowed")


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


def response_400() -> HTTPResponse:
    return HTTPResponse(
        400,
        "text/plain; charset=utf-8",
        "400",
    )


def response_404() -> HTTPResponse:
    return HTTPResponse(
        404,
        "text/plain; charset=utf-8",
        "404 Not Found",
    )


def response_413() -> HTTPResponse:
    return HTTPResponse(
        413,
        "text/plain; charset=utf-8",
        "413 Payload Too Large",
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


def response_405() -> HTTPResponse:
    return HTTPResponse(
        405,
        "text/plain; charset=utf-8",
        "405 Method Not Allowed",
        {"Allow": "GET, POST, HEAD"},  # HACK Configから参照
    )


def error_response(status: int, msg: str):
    if status == 400:
        return response_400()
    elif status == 405:
        return response_405()
    elif status == 404:
        return response_404()
    else:
        return response_500()


# リストから優先される圧縮方式を取得
def get_preferred_encoding(
    accept_encoding: str, compression_priority: list[str]
) -> str:
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


# 任意の番号のヘッダーを構築してレスポンス全体を返す
def build_response(response: HTTPResponse, close_connection: bool = True) -> bytes:
    headers = [
        f"HTTP/1.1 {response.status_code} {get_http_reason_phrase(response.status_code)}",
        f"Content-Type: {response.content_type}",
        f"Content-Length: {response.content_length}",
    ]

    # レスポンスオブジェクトに含まれる追加ヘッダー
    for key, value in response.headers.items():
        headers.append(f"{key}: {value}")

    if close_connection:
        headers.append("Connection: close")

    headers.append("Server: MyHTTPServer/0.1")

    header_blob = "\r\n".join(headers) + "\r\n\r\n"

    # contentがbytesかstrかで処理を分ける
    if isinstance(response.content, bytes):
        content_bytes = response.content
    else:
        content_bytes = response.content.encode("utf-8")

    return header_blob.encode("utf-8") + content_bytes
