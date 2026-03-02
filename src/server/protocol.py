import gzip
import traceback
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, Optional

import zstandard as zstd


@dataclass
class HTTPRequest:
    method: str
    path: str
    version: str
    remote_addr: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class HTTPResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        header: Dict = {},
        content_type="text/html; charset=utf-8",
    ):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type} | header
        self.__compress = ""

    def set_header(self, key: str, value: str):
        self.headers[key] = value

    def set_compress(self, compress_type):
        if any(c in compress_type for c in ["gzip", "zst"]):
            self.__compress = compress_type

    def to_bytes(self) -> bytes:
        # ステータス行
        status_line = f"HTTP/1.1 {self.status} OK\r\n"

        response_body = self.body

        if self.__compress == "gzip":
            out = BytesIO()
            with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=1) as f:
                f.write(response_body)
            response_body = out.getvalue()
            self.headers["Content-Encoding"] = "gzip"
        elif self.__compress == "zstd":
            cctx = zstd.ZstdCompressor()
            response_body = cctx.compress(response_body)
            self.headers["Content-Encoding"] = "zstd"

        # Content-Length は圧縮後のボディ長で返す
        self.headers["Content-Length"] = str(len(response_body))

        # ヘッダー結合
        header_lines = ""
        for k, v in self.headers.items():
            header_lines += f"{k}: {v}\r\n"

        # print(header_lines)

        # 全体結合 (ヘッダーとボディの間には空行が必要)
        return f"{status_line}{header_lines}\r\n".encode() + response_body


@dataclass
class HttpError(Exception):
    status: int
    message: str = ""


def parse_request(data: bytes, remote_addr: str) -> Optional[HTTPRequest]:
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

        return HTTPRequest(
            method=method,
            path=path,
            version=version,
            remote_addr=remote_addr,
            headers=headers,
        )
    except Exception:
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
