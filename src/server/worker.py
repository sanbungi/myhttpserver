import asyncio
import re
import traceback
from typing import Optional, Tuple

from icecream import ic

from .config_model import ServerConfig

from .protocol import HttpError, HTTPRequest, HTTPResponse, parse_request
from .router import get_preferred_encoding, resolve_route


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: ServerConfig
):
    peer = writer.get_extra_info("peername")
    ip, port = peer
    # print(f"[+] Connection from ip={ip}, port={port}")

    try:
        while True:
            loaded_data = await safe_load(reader, writer, ip)

            if loaded_data is None:
                # 切断、タイムアウト、またはエラー送信済みのためループを抜けて接続を切る
                break

            header_part, full_body = loaded_data

            # リクエスト解析
            request = parse_request(header_part, ip)
            if not request:
                break
            request.body = full_body
            vetify_request(request)

            ic(request)

            accept_encoding = _get_header_case_insensitive(
                request.headers, "accept-encoding"
            )
            encoding = get_preferred_encoding(
                accept_encoding, config.compression_methods
            )

            # ルーティング実行
            response = await resolve_route(request, config, encoding=encoding)
            response.set_compress(encoding)

            response.set_header("Server", "MyHTTPServer/0.1")

            # Keep-Alive 判定
            conn_header = _get_header_case_insensitive(request.headers, "connection")

            conn_header = conn_header.lower()
            if request.version == "HTTP/1.0" or conn_header == "close":
                should_close = True
                response.set_header("Connection", "close")
            else:
                should_close = False
                response.set_header("Connection", "keep-alive")

            # レスポンス送信
            try:
                writer.write(response.to_bytes())
                await writer.drain()  # 送信完了待ち
            except (ConnectionResetError, BrokenPipeError):
                break

            if should_close:
                break
    except HttpError as e:
        ic(f"send HTTPError e:{e}")
        response = HTTPResponse(e.status)
        writer.write(response.to_bytes())
        await writer.drain()  # 送信完了待ち

    except Exception as e:
        traceback.print_exc()
        print(f"[-] Error: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            # クライアントが既に切断している場合は何もしない（正常）
            pass
        except Exception as e:
            # その他のエラーは念のためログに出す（デバッグ用）
            traceback.print_exc()
            print(f"[-] Error during close: {e}")


MAX_HEADER_SIZE = 1024 * 1024 * 2  # 2MB
MAX_BODY_SIZE = 1024 * 1024 * 2  # 2MB
HEADER_TIMEOUT_SECONDS = 5.0
BODY_TIMEOUT_SECONDS = 10.0


def _get_header_case_insensitive(headers: dict, name: str, default: str = "") -> str:
    if name in headers:
        return headers[name]

    lowered_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered_name:
            return value
    return default


def _parse_content_length(header_part: bytes) -> int:
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
        return content_length

    return 0


async def safe_load(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_ip: str
) -> Optional[Tuple[bytes, bytes]]:
    try:
        async with asyncio.timeout(HEADER_TIMEOUT_SECONDS):
            header_block = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        print(f"[-] Error: Header too large from {peer_ip}")
        raise HttpError(431)
    except (TimeoutError, asyncio.IncompleteReadError):
        return None
    except ConnectionResetError:
        return None

    if len(header_block) > MAX_HEADER_SIZE:
        print(f"[-] Error: Header too large from {peer_ip}")
        raise HttpError(431)

    header_part = header_block[:-4]
    content_length = _parse_content_length(header_part)

    # 413 Payload Too Large
    if content_length >= MAX_BODY_SIZE:
        print(f"[-] Error: Body too large ({content_length} bytes) from {peer_ip}")
        raise HttpError(413)

    if content_length == 0:
        return header_part, b""

    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            full_body = await reader.readexactly(content_length)
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
        return None

    return header_part, full_body


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


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def contains_control_chars(s: str) -> bool:
    return _CONTROL_CHAR_RE.search(s) is not None


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


# allow_methods = {"GET", "HEAD", "OPTIONS"}
# if request.method not in allow_methods:
#   raise HttpError(405, "METHOD NOT ALLOWED")
