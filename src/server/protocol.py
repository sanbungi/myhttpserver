import gzip
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, Optional

import zstandard as zstd

from src.server.error_page import (
    get_cached_error_page_body,
    get_error_page_etag_opaque,
    get_error_page_last_modified,
)

from .http_date import http_date_now
from .reason_phrase import get_http_reason_phrase

logger = logging.getLogger(__name__)


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
        header: Optional[Dict] = None,
        content_type="text/html; charset=utf-8",
    ):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type} | (header or {})
        self.__compress = ""
        self.__allow_compress = True

    def set_header(self, key: str, value: str):
        self.headers[key] = value

    def set_compress(self, compress_type):
        if not self.__allow_compress:
            return
        if any(c in compress_type for c in ["gzip", "zst"]):
            self.__compress = compress_type

    def disable_compression(self):
        self.__allow_compress = False
        self.__compress = ""

    def _resolve_reason_phrase(self) -> str:
        reason = get_http_reason_phrase(self.status)
        if reason == "-1":
            self.status = 500
            reason = get_http_reason_phrase(self.status)
        return reason

    def prepare_default_error_validators(self) -> None:
        reason = self._resolve_reason_phrase()

        # 400-5xxで明示bodyがない場合、共通HTMLとvalidatorを付与する。
        if 400 <= self.status < 600 and not self.body:
            etag_opaque = get_error_page_etag_opaque(self.status, reason)
            self.headers.setdefault("ETag", f'W/"{etag_opaque}"')
            self.headers.setdefault("Last-Modified", get_error_page_last_modified())

    def to_bytes(self) -> bytes:
        reason = self._resolve_reason_phrase()

        if self.status == 405:
            self.headers["Allow"] = "GET, HEAD"

        status_line = f"HTTP/1.1 {self.status} {reason}\r\n"

        if 400 <= self.status < 600 and not self.body:
            self.body = get_cached_error_page_body(self.status, reason)
            etag_opaque = get_error_page_etag_opaque(self.status, reason)
            self.headers.setdefault("ETag", f'W/"{etag_opaque}"')
            self.headers.setdefault("Last-Modified", get_error_page_last_modified())

        response_body = self.body
        if isinstance(response_body, str):
            response_body = response_body.encode()

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
        self.headers["Date"] = http_date_now()

        # ヘッダー結合（値がリストの場合は同名ヘッダーを複数行展開する）
        header_lines = ""
        for k, v in self.headers.items():
            if isinstance(v, list):
                for item in v:
                    header_lines += f"{k}: {item}\r\n"
            else:
                header_lines += f"{k}: {v}\r\n"

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
        logger.exception("Failed to parse request from remote_addr=%s", remote_addr)
        return None
