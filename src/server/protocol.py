from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class HTTPRequest:
    method: str
    path: str
    version: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class HTTPResponse:
    def __init__(self, status: int = 200, body: bytes = b""):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def set_header(self, key: str, value: str):
        self.headers[key] = value

    def to_bytes(self) -> bytes:
        # ステータス行
        status_line = f"HTTP/1.1 {self.status} OK\r\n"

        # Content-Length は自動計算
        self.headers["Content-Length"] = str(len(self.body))

        # ヘッダー結合
        header_lines = ""
        for k, v in self.headers.items():
            header_lines += f"{k}: {v}\r\n"

        # 全体結合 (ヘッダーとボディの間には空行が必要)
        return f"{status_line}{header_lines}\r\n".encode() + self.body


def parse_request(data: bytes) -> Optional[HTTPRequest]:
    try:
        # ヘッダーとボディを分離（今回は簡易的にヘッダーのみ解析）
        header_part = data.decode("utf-8", errors="ignore")
        lines = header_part.split("\r\n")

        if not lines:
            return None

        # 1行目: GET / HTTP/1.1
        request_line = lines[0].split(" ")
        if len(request_line) < 3:
            return None

        method, path, version = request_line[0], request_line[1], request_line[2]

        # ヘッダー解析
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key] = value.strip()

        return HTTPRequest(method=method, path=path, version=version, headers=headers)
    except Exception:
        return None
