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

    ext = Path(file_path).suffix.lower()
    if ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]:
        content_type = f"image/{ext[1:]}"
        is_binary = True
    elif ext in [".html", ".txt"]:
        content_type = "text/html; charset=utf-8"
        is_binary = False
    else:
        content_type = "unknown/unknown"
        is_binary = True

    return content_type, is_binary


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
