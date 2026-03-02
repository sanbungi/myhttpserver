import asyncio
import traceback
from typing import Optional, Tuple

from icecream import ic

from config_model import ServerConfig
from utils import get_preferred_encoding

from .protocol import HttpError, HTTPRequest, HTTPResponse, parse_request
from .router import generage_file_etag, resolve_route


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
            vetify_request(request)

            # ルーティング実行
            response = await resolve_route(request, config)

            accept_encoding = request.headers.get("Accept-Encoding", "")
            encoding = get_preferred_encoding(accept_encoding, ["gzip"])
            response.set_compress(encoding)

            response.set_header("Server", "MyHTTPServer/0.1")
            etag = generage_file_etag(request.path)
            response.set_header("ETag", f"{etag}-{encoding}")

            # Keep-Alive 判定
            conn_header = request.headers.get("Connection", "").lower()
            if conn_header == "close":
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


async def safe_load(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_ip: str
) -> Optional[Tuple[bytes, bytes]]:
    buffer = b""
    header_data = None

    # ヘッダー確認
    try:
        while True:
            # バッファサイズチェック
            if len(buffer) > MAX_HEADER_SIZE:
                print(f"[-] Error: Header too large from {peer_ip}")
                raise HttpError(431)

            if b"\r\n\r\n" in buffer:
                header_data = buffer
                break

            # タイムアウト付きで読み込み
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            if not chunk:
                return None
            buffer += chunk

    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        return None
    except ConnectionResetError:
        return None

    # ヘッダーとボディの残りに分割
    header_part, body_start = header_data.split(b"\r\n\r\n", 1)

    # Content-Length 解析とチェック
    try:
        header_text = header_part.decode("utf-8", errors="ignore")
    except Exception:
        raise HttpError(400)

    content_length = 0
    for line in header_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":")[1].strip())
            except ValueError:
                pass
            break

    # 413 Payload Too Large
    if content_length > MAX_BODY_SIZE:
        print(f"[-] Error: Body too large ({content_length} bytes) from {peer_ip}")
        raise HttpError(413)

    # ボディ読み込み ---
    full_body = body_start
    remaining_bytes = content_length - len(body_start)

    if remaining_bytes > 0:
        try:
            body_chunk = await asyncio.wait_for(
                reader.readexactly(remaining_bytes), timeout=10.0
            )
            full_body += body_chunk
        except (
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            ConnectionResetError,
        ):
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


def contains_control_chars(s: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


def vetify_request(request: HTTPRequest):
    ic(request)

    headers = request.headers
    hosts = [headers["Host"]] if "Host" in headers else []
    if len(hosts) == 0:
        raise HttpError(400, "MISSING_HOST")
    if len(hosts) > 1:
        raise HttpError(400, "DUPLICATE_HOST")

    if len(request.path) > 255:
        raise HttpError(414, "REQUEST_URL_TOO_LONG")

    if request.version not in ("HTTP/1.1", "HTTP/1.0"):
        raise HttpError(400, "INVALID_HTTP_VERSION")

    if request.method not in HTTP_METHODS:
        raise HttpError(400, "INVALID_HTTP_METHOD")

    if any(contains_control_chars(h) for h in request.headers):
        raise HttpError(
            400,
            "DISALLOW_CONTAILS_CONTROL_CHARCTER",
        )

    ALLOW_METHOD = ["GET", "HEAD", "OPTIONS"]
    if not any(request.method in s for s in ALLOW_METHOD):
        ic("NOT ALLOW !!!")
        raise HttpError(405, "METHOD NOT ALLOWED")
