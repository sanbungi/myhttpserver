import asyncio
import os

from .protocol import HTTPRequest, HTTPResponse


# CPUバウンドやブロッキングI/Oを別スレッドで実行するヘルパー
async def run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    # None指定でデフォルトのThreadPoolExecutorが使われる
    return await loop.run_in_executor(None, func, *args)


def _read_file_sync(filepath):
    # 同期的なファイル読み込み関数
    if os.path.exists(filepath) and os.path.isfile(filepath):
        with open(filepath, "rb") as f:
            return f.read()
    return None


async def static_file_handler(request: HTTPRequest) -> HTTPResponse:
    # パスの正規化などは省略
    file_path = f"./test-assets/html/{request.path}"

    if request.path == "/":
        file_path = "./test-assets/html/index.html"

    # ファイル読み込みを非ブロック化して実行
    content = await run_blocking(_read_file_sync, file_path)

    if content is not None:
        return HTTPResponse(200, content)
    else:
        return HTTPResponse(404, b"Not Found")
