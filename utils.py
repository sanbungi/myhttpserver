import gzip
from dataclasses import dataclass, field
from enum import Enum, auto
from io import BytesIO
from typing import Dict, Union

import zstandard as zstd
from icecream import ic


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


MAX_HEADER_SIZE = 8192
MAX_BODY_SIZE = 1024 * 1024


# ヘッダーとボディを分けて解析
def receive_safe_request(client_sock):
    buffer = b""
    # ヘッダー
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > MAX_HEADER_SIZE:
            raise HttpError(431, "Request Header Fields Too Large", "TOO LONG")

        data = client_sock.recv(4096)
        if not data:
            break
        buffer += data

    if not buffer or b"\r\n\r\n" not in buffer:
        return None, None

    header_part, body_start = buffer.split(b"\r\n\r\n", 1)
    header_text = header_part.decode("utf-8")

    content_length = 0
    for line in header_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":")[1].strip())
            break

    if content_length > MAX_BODY_SIZE:
        raise HttpError(413, "Payload Too Large", "TOO LONG")

    # Body部分
    body = body_start
    while len(body) < content_length:
        remaining = content_length - len(body)
        # 残り必要な分か、バッファサイズの小さい方を読み込む
        data = client_sock.recv(min(remaining, 4096))
        if not data:
            break
        body += data

    print(header_text, body)

    return header_text, body


def parse_request(header: str, body: bytes) -> HTTPRequest:
    lines = header.strip().split("\r\n")

    request_line = lines[0]
    parts = request_line.split(" ")
    parts = list(filter(None, parts))
    ic(parts)

    if len(parts) != 3:
        raise HttpError(400, "", "")

    method, path, version = parts

    headers = {}
    for line in lines[1:]:
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.lower()] = value

    return HTTPRequest(method, path, version, headers, body)


def vetify_request(request: HTTPRequest):
    ic(request)

    headers = request.headers
    hosts = [headers["host"]] if "host" in headers else []
    if len(hosts) == 0:
        raise HttpError(400, "MISSING_HOST", "Host Header is requeired")
    if len(hosts) > 1:
        raise HttpError(400, "DUPLICATE_HOST", "Multiple Host headers")

    if len(request.path) > 255:
        raise HttpError(414, "REQUEST_URL_TOO_LONG", "Request url too long")

    if request.version not in ("HTTP/1.1", "HTTP/1.0"):
        raise HttpError(400, "INVALID_HTTP_VERSION", "Invalid http version")

    ALLOW_METHOD = ["GET", "HEAD", "OPTIONS"]
    if not any(request.method in s for s in ALLOW_METHOD):
        print("NOT ALLOW !!!")
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
    connection_header = request.headers.get("connection", "").lower()
    ic(connection_header)

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


def response_204() -> HTTPResponse:
    return HTTPResponse(
        204,
        "text/plain; charset=utf-8",
        "",
        {"Allow": "GET, HEAD, OPTIONS"},  # HACK Configから参照
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


def response_414() -> HTTPResponse:
    return HTTPResponse(
        414,
        "text/plain; charset=utf-8",
        "414 URL Too Long",
    )


def response_431() -> HTTPResponse:
    return HTTPResponse(
        431,
        "text/plain; charset=utf-8",
        "431 Request Header Fields Too Large",
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
        {"Allow": "GET, HEAD, OPTIONS"},  # HACK Configから参照
    )


def error_response(status: int, msg: str):
    if status == 400:
        return response_400()
    elif status == 405:
        return response_405()
    elif status == 404:
        return response_404()
    elif status == 413:
        return response_413()
    elif status == 414:
        return response_414()
    elif status == 431:
        return response_431()
    else:
        print("FALL BACK ERROR 500")
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
def build_response(
    response: HTTPResponse,
    request: HTTPRequest,
) -> bytes:

    content_length = response.content_length
    code = response.status_code
    if (100 <= code < 200) or code in {204, 304} or 400 <= code < 600:
        content_length = 0

    headers = [
        f"HTTP/1.1 {response.status_code} {get_http_reason_phrase(response.status_code)}",
        f"Content-Type: {response.content_type}",
        f"Content-Length: {content_length}",
    ]

    # accept_encoding = request.headers.get("Accept-Encoding", "")
    # レスポンスオブジェクトに含まれる追加ヘッダー
    for key, value in response.headers.items():
        headers.append(f"{key}: {value}")

    keep_alive = get_keep_alive(request)
    if keep_alive:
        headers.append("Connection: close")
    else:
        headers.append("Connection: keep-alive")
        headers.append("Keep-Alive: timeout=61")

    headers.append("Server: MyHTTPServer/0.1")

    # encoding = get_preferred_encoding(accept_encoding, ["gzip"])
    encoding = False
    if encoding:
        headers.append(f"Content-Encoding: {encoding}")
        response.content = compress_content(response.content, encoding)
        headers[2] = f"Content-Length: {len(response.content)}"

    header_blob = "\r\n".join(headers) + "\r\n\r\n"

    content_bytes = b""
    if request.method == "GET":
        # contentがbytesかstrかで処理を分ける
        if isinstance(response.content, bytes):
            content_bytes = response.content
        else:
            content_bytes = response.content.encode("utf-8")

    return header_blob.encode("utf-8") + content_bytes
