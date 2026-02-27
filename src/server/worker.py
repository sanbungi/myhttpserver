import asyncio

from .protocol import parse_request
from .router import resolve_route


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"[+] Connection from {addr}")

    try:
        while True:
            try:
                # タイムアウト付きでヘッダー終了（空行）まで読み込む
                # HTTPヘッダーの区切りは \r\n\r\n
                header_data = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), timeout=5.0
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                # タイムアウトまたは切断
                break
            except ConnectionResetError:
                break

            # リクエスト解析
            request = parse_request(header_data)
            if not request:
                break

            # ルーティング実行
            response = await resolve_route(request)

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
            print(f"[-] Error during close: {e}")
