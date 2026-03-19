import asyncio
import logging
import re
import threading
from typing import Any, Optional, Tuple

from .config_model import ServerConfig
from .etag_utils import weak_etag_equal
from .ip_table import InMemoryIPTable
from .logging_config import log_access, pretty_block
from .protocol import HttpError, HTTPRequest, HTTPResponse, parse_request
from .router import (
    apply_response_headers_from_config,
    get_preferred_encoding,
    resolve_route,
)

MAX_HEADER_SIZE = 1024 * 1024 * 2  # 2MB
MAX_BODY_SIZE = 1024 * 1024 * 2  # 2MB
MAX_CHUNK_LINE_SIZE = 64  # チャンクサイズ行の最大長
HEADER_TIMEOUT_SECONDS = 5.0
BODY_TIMEOUT_SECONDS = 10.0
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_MAX_CONNECTIONS_PER_IP = 20
DEFAULT_MAX_CONNECTIONS_PER_WORKER = 1024
HTTP_METHODS = {
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "TRACE",
    "CONNECT",
}

logger = logging.getLogger(__name__)
_DEFAULT_IP_TABLE = InMemoryIPTable(
    max_connections_per_ip=DEFAULT_MAX_CONNECTIONS_PER_IP
)


class WorkerConnectionLimiter:
    def __init__(self, max_connections: int = DEFAULT_MAX_CONNECTIONS_PER_WORKER):
        self.max_connections = max(1, int(max_connections))
        self._active_connections = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active_connections >= self.max_connections:
                return False
            self._active_connections += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active_connections <= 0:
                return
            self._active_connections -= 1

    def get_active_connections(self) -> int:
        with self._lock:
            return self._active_connections


_DEFAULT_WORKER_CONNECTION_LIMITER = WorkerConnectionLimiter()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: ServerConfig,
    ip_table: Optional[InMemoryIPTable] = None,
    worker_limiter: Optional[WorkerConnectionLimiter] = None,
):
    peer = writer.get_extra_info("peername")
    ip = _extract_peer_ip(peer)
    table = ip_table if ip_table is not None else _DEFAULT_IP_TABLE
    limiter = (
        worker_limiter
        if worker_limiter is not None
        else _DEFAULT_WORKER_CONNECTION_LIMITER
    )

    if table.is_banned(ip):
        logger.warning("Blocked banned IP: %s", ip)
        await _send_banned_response(writer)
        await _close_writer_quietly(writer)
        return

    worker_connection_acquired = limiter.try_acquire()
    if not worker_connection_acquired:
        logger.warning("Per-worker connection limit exceeded: ip=%s", ip)
        await _send_connection_limit_response(writer)
        await _close_writer_quietly(writer)
        return

    connection_acquired = False
    request: Optional[HTTPRequest] = None

    try:
        connection_acquired = table.try_acquire_connection(ip)

        if not connection_acquired:
            logger.warning("Per-IP connection limit exceeded for %s", ip)
            await _send_connection_limit_response(writer)
            await _close_writer_quietly(writer)
            return

        while True:
            request = None
            # パケットのサイズが大きければcloseされる。
            loaded_data = await safe_load(reader, writer, ip)

            # 切断、タイムアウト、またはエラー送信済みのためループを抜けて接続を切る
            if loaded_data is None:
                break

            header_part, full_body = loaded_data

            # ヘッダーからリクエスト解析
            request = parse_request(header_part, ip)
            if not request:
                break

            request.body = full_body

            # 各種規約に準じた構造になっているか確認
            vetify_request(request)
            logger.debug("request=%s", pretty_block(request))

            # 対応している圧縮系s機を確認
            accept_encoding = _get_header_case_insensitive(
                request.headers, "accept-encoding"
            )
            encoding = get_preferred_encoding(
                accept_encoding, config.compression_methods
            )

            # ルーティング実行
            response = await resolve_route(request, config, encoding=encoding)
            if _get_header_case_insensitive(request.headers, "if-none-match"):
                response.prepare_default_error_validators()
            # 圧縮
            response.set_compress(encoding)
            # サーバー名付与（バージョンを含めない）
            response.set_header("Server", "MyHTTPServer")

            # Keep-Alive 判定
            conn_header = _get_header_case_insensitive(
                request.headers, "connection"
            ).lower()
            if request.version == "HTTP/1.0" or conn_header == "close":
                should_close = True
                response.set_header("Connection", "close")
            else:
                should_close = False
                response.set_header("Connection", "keep-alive")

            # Configに設定に沿って、返したくないヘッダーを削除
            apply_response_headers_from_config(response, config, request.path)
            _apply_if_none_match_precondition(request, response)

            # レスポンス送信
            try:
                logger.debug("response.headers=%s", pretty_block(response.headers))
                response_bytes = response.to_bytes()
                # HEADならBodyは削り取り、送らない
                if request.method == "HEAD":
                    response_bytes = _strip_body_from_http_message(response_bytes)
                writer.write(response_bytes)

                # 送信完了待ち
                await writer.drain()

                # 正常アクセスログ記録
                log_access(
                    remote_addr=ip,
                    method=request.method,
                    url=request.path,
                    http_version=request.version,
                    status_code=response.status,
                    response_size=_response_size(response),
                    user_agent=_get_header_case_insensitive(
                        request.headers, "user-agent", default="-"
                    ),
                )
            except (ConnectionResetError, BrokenPipeError):
                break

            if should_close:
                break
    except HttpError as e:
        logger.warning("send HTTPError e:%s", e)
        response = HTTPResponse(e.status)
        response_bytes = response.to_bytes()
        writer.write(response_bytes)

        await writer.drain()

        # 規定されたエラーコードを返したアクセスログ記録
        log_access(
            remote_addr=ip,
            method=request.method if request else "-",
            url=request.path if request else "-",
            http_version=request.version if request else "-",
            status_code=response.status,
            response_size=_response_size(response),
            user_agent=(
                _get_header_case_insensitive(request.headers, "user-agent", default="-")
                if request
                else "-"
            ),
        )

    except Exception as e:
        logger.exception("Unhandled error in client handler: %s", e)
    finally:
        if connection_acquired:
            table.release_connection(ip)
        limiter.release()
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            # クライアントが既に切断している場合は何もしない（正常）
            pass
        except Exception as e:
            # その他のエラーは念のためログに出す（デバッグ用）
            logger.exception("Error during close: %s", e)


async def safe_load(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_ip: str
) -> Optional[Tuple[bytes, bytes]]:
    try:
        async with asyncio.timeout(HEADER_TIMEOUT_SECONDS):
            header_block = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        logger.warning("Header too large from %s", peer_ip)
        raise HttpError(431)
    except (TimeoutError, asyncio.IncompleteReadError):
        return None
    except ConnectionResetError:
        return None

    if len(header_block) > MAX_HEADER_SIZE:
        logger.warning("Header too large from %s", peer_ip)
        raise HttpError(431)

    header_part = header_block[:-4]

    transfer_encoding = _parse_transfer_encoding(header_part)
    content_length = _parse_content_length(header_part)

    # Transfer-Encoding と Content-Length の同時存在を拒否
    if transfer_encoding is not None and content_length > 0:
        logger.warning("Both Transfer-Encoding and Content-Length from %s", peer_ip)
        raise HttpError(400)

    # chunked転送
    if transfer_encoding is not None:
        if transfer_encoding != "chunked":
            raise HttpError(501)
        full_body = await _read_chunked_body(reader, peer_ip)
        if full_body is None:
            return None
        # パース前にTransfer-Encodingヘッダーを除去（デコード済みのため不要）
        header_part = _strip_header_from_raw(header_part, b"transfer-encoding")
        return header_part, full_body

    # 413 Payload Too Large
    if content_length >= MAX_BODY_SIZE:
        logger.warning("Body too large (%s bytes) from %s", content_length, peer_ip)
        raise HttpError(413)

    if content_length == 0:
        return header_part, b""

    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            full_body = await reader.readexactly(content_length)
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
        return None

    return header_part, full_body


def contains_control_chars(s: str) -> bool:
    return _CONTROL_CHAR_RE.search(s) is not None


def _extract_peer_ip(peer: Any) -> str:
    if isinstance(peer, tuple) and peer:
        return str(peer[0])
    if peer is None:
        return "-"
    return str(peer)


async def _send_connection_limit_response(writer: asyncio.StreamWriter) -> None:
    try:
        response = HTTPResponse(429)
        response.set_header("Connection", "close")
        response.set_header("Retry-After", "1")
        writer.write(response.to_bytes())
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        return


async def _send_banned_response(writer: asyncio.StreamWriter) -> None:
    try:
        response = HTTPResponse(403)
        response.set_header("Connection", "close")
        writer.write(response.to_bytes())
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        return


async def _close_writer_quietly(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        return


def vetify_request(request: HTTPRequest):
    headers = request.headers

    if request.version == "HTTP/1.1" and not _get_header_case_insensitive(
        headers, "host"
    ):
        raise HttpError(400, "MISSING_HOST")

    if len(request.path) > 255:
        raise HttpError(414, "REQUEST_URL_TOO_LONG")

    if len(request.headers) > 255:
        raise HttpError(400, "TOO_MANY_HEADERS")

    if request.version not in ("HTTP/1.1", "HTTP/1.0"):
        raise HttpError(400, "INVALID_HTTP_VERSION")

    if request.method not in HTTP_METHODS:
        raise HttpError(400, "INVALID_HTTP_METHOD")

    for header_name, header_value in request.headers.items():
        if contains_control_chars(header_name) or contains_control_chars(header_value):
            raise HttpError(400, "DISALLOW_CONTAILS_CONTROL_CHARCTER")


def _get_header_case_insensitive(headers: dict, name: str, default: str = "") -> str:
    if name in headers:
        return headers[name]

    lowered_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered_name:
            return value
    return default


def _response_size(response: HTTPResponse) -> int:
    content_length = response.headers.get("Content-Length", "0")
    try:
        return max(0, int(content_length))
    except (TypeError, ValueError):
        return 0


def _strip_body_from_http_message(raw_response: bytes) -> bytes:
    header_end = raw_response.find(b"\r\n\r\n")
    if header_end == -1:
        return raw_response
    return raw_response[: header_end + 4]


def _apply_if_none_match_precondition(
    request: HTTPRequest, response: HTTPResponse
) -> None:
    if response.status in {304, 412}:
        return

    current_etag = _get_header_case_insensitive(response.headers, "etag")
    if not current_etag:
        return

    raw_tag = _get_header_case_insensitive(request.headers, "if-none-match")
    if not raw_tag:
        return

    raw_tag = raw_tag.strip()
    matched = raw_tag == "*"
    if not matched:
        for tag in raw_tag.split(","):
            if weak_etag_equal(tag.strip(), current_etag):
                matched = True
                break

    if not matched:
        return

    if request.method in {"GET", "HEAD"}:
        response.status = 304
    else:
        response.status = 412
    response.body = b""
    response.disable_compression()


def _parse_content_length(header_part: bytes) -> int:
    found_value = None
    for line in header_part.split(b"\r\n"):
        if line[:15].lower() != b"content-length:":
            continue

        raw_value = line[15:].strip()
        if not raw_value:
            raise HttpError(400)

        try:
            content_length = int(raw_value)
        except ValueError as e:
            raise HttpError(400) from e

        if content_length < 0:
            raise HttpError(400)

        # 異なる値を持つ複数Content-Lengthを拒否
        if found_value is not None and found_value != content_length:
            raise HttpError(400)
        found_value = content_length

    return found_value if found_value is not None else 0


def _parse_transfer_encoding(header_part: bytes) -> Optional[str]:
    for line in header_part.split(b"\r\n"):
        if line[:18].lower() != b"transfer-encoding:":
            continue
        return line[18:].strip().decode("ascii", errors="ignore").lower()
    return None


async def _read_chunked_body(
    reader: asyncio.StreamReader, peer_ip: str
) -> Optional[bytes]:
    buf = bytearray()
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            while True:
                # チャンクサイズ行を読む
                try:
                    size_line = await reader.readuntil(b"\r\n")
                except asyncio.LimitOverrunError:
                    raise HttpError(400)

                if len(size_line) > MAX_CHUNK_LINE_SIZE:
                    raise HttpError(400)

                size_str = size_line[:-2].decode("ascii", errors="ignore").strip()

                # chunk-extension を除去 (";ext=value" 等)
                semicolon = size_str.find(";")
                if semicolon != -1:
                    size_str = size_str[:semicolon].strip()

                try:
                    chunk_size = int(size_str, 16)
                except ValueError:
                    raise HttpError(400)

                if chunk_size < 0:
                    raise HttpError(400)

                # last-chunk (size == 0)
                if chunk_size == 0:
                    # トレーラーヘッダーを読み飛ばす（空行まで）
                    while True:
                        trailer_line = await reader.readuntil(b"\r\n")
                        if trailer_line == b"\r\n":
                            break
                    break

                # ボディサイズ上限チェック
                if len(buf) + chunk_size > MAX_BODY_SIZE:
                    logger.warning(
                        "Chunked body too large (%d + %d bytes) from %s",
                        len(buf),
                        chunk_size,
                        peer_ip,
                    )
                    raise HttpError(413)

                # チャンクデータ本体を読む
                chunk_data = await reader.readexactly(chunk_size)
                buf.extend(chunk_data)

                # チャンクデータ末尾の CRLF を消費
                await reader.readexactly(2)

    except HttpError:
        raise
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
        return None

    return bytes(buf)


def _strip_header_from_raw(header_part: bytes, header_name_lower: bytes) -> bytes:
    lines = header_part.split(b"\r\n")
    filtered = []
    for line in lines:
        colon_idx = line.find(b":")
        if colon_idx > 0 and line[:colon_idx].strip().lower() == header_name_lower:
            continue
        filtered.append(line)
    return b"\r\n".join(filtered)
