import asyncio
import os
import socket

from .worker import handle_client


class HTTPServer:
    def __init__(self, host="127.0.0.1", port=8080):
        self.host = host
        self.port = port

    def _create_socket(self):
        """
        SO_REUSEPORT (または SO_REUSEADDR) を設定したソケットを作成して返す
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # タイムアウト待ちのポートを再利用できるようにする
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # 【重要】複数のプロセスが同じポートをバインドできるようにする
        # Linux (kernel >= 3.9) や macOS で利用可能
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            # 既にバインドされている場合などのエラーハンドリング
            print(f"Bind failed: {e}")
            sock.close()
            raise e

        return sock

    async def serve_forever(self):
        """
        サーバーを起動し、永続的にリクエストを処理する
        """
        # 手動で設定したソケットを作成
        sock = self._create_socket()

        # 既存のソケットを使ってサーバーを開始
        server = await asyncio.start_server(handle_client, sock=sock)

        pid = os.getpid()
        print(f"[PID: {pid}] Serving on http://{self.host}:{self.port}")

        async with server:
            await server.serve_forever()
