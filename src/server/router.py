import asyncio
import os

from FileCache import FileCache

from .protocol import HTTPRequest, HTTPResponse

# 静的ファイルのルートディレクトリ
STATIC_DIR = os.path.join(os.getcwd(), "html")

cache = FileCache()


# ブロッキングなファイル読み込みを別スレッドで実行する関数
def read_file_sync(filepath):
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return cache.read(filepath, mode="rb")
        with open(filepath, "rb") as f:
            return f.read()
    return None


async def handle_static(request: HTTPRequest) -> HTTPResponse:
    path = request.path
    if path == "/":
        path = "/index.html"

    # パストラバーサル対策（簡易版）
    filename = path.lstrip("/")
    filepath = os.path.join(STATIC_DIR, filename)

    # イベントループを取得して、ファイル読み込みをスレッドプールに投げる
    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, read_file_sync, filepath)

    if content:
        return HTTPResponse(200, content)
    else:
        return HTTPResponse(404, b"<h1>404 Not Found</h1>")


async def handle_api(request: HTTPRequest) -> HTTPResponse:
    # 擬似的なAPI処理
    import json

    data = {"message": "Hello from Async API", "method": request.method}
    body = json.dumps(data).encode()
    res = HTTPResponse(200, body)
    res.set_header("Content-Type", "application/json")
    return res


async def resolve_route(request: HTTPRequest) -> HTTPResponse:
    if request.path.startswith("/api"):
        return await handle_api(request)
    else:
        return await handle_static(request)
