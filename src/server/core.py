import asyncio
import os
import socket
import ssl
import traceback
from functools import partial

from icecream import ic

from .config_model import ServerConfig

from .worker import handle_client


class HTTPServer:
    def __init__(self, host="127.0.0.1", port=8080, config: ServerConfig = None):
        self.host = host
        self.port = port
        self.config = config

    def _create_socket(self):
        """
        SO_REUSEPORT (または SO_REUSEADDR) を設定したソケットを作成して返す
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # タイムアウト待ちのポートを再利用できるようにする
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # 同じポートで複数起動できるようにフラグをセット、Linux (kernel >= 3.9) や macOS で利用可能
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            # 既にバインドされている場合などのエラーハンドリング
            print(f"Bind failed: {e}")
            sock.close()
            traceback.print_exc()
            raise e

        return sock

    async def serve_forever(self):
        """
        サーバーを起動し、永続的にリクエストを処理する
        """
        # 手動で設定したソケットを作成
        sock = self._create_socket()

        use_ssl = False
        has_ssl = hasattr(self.config, "tls")
        if has_ssl:
            use_ssl = self.config.tls.enabled

        if use_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                context.load_cert_chain(certfile="server.crt", keyfile="server.key")
            except Exception as e:
                ic(f"SSL Load Error: {e}")
                traceback.print_exc()
                return

            # 既存のソケットを使ってサーバーを開始
            try:
                server = await asyncio.start_server(
                    partial(handle_client, config=self.config), sock=sock, ssl=context
                )
                async with server:
                    await server.serve_forever()

            except ssl.SSLError as e:
                ic(f"SSL handshake faild with {self.host}: {e}")
        else:
            server = await asyncio.start_server(
                partial(handle_client, config=self.config), sock=sock
            )
            async with server:
                await server.serve_forever()

        pid = os.getpid()
        print(f"[PID: {pid}] Serving on http://{self.host}:{self.port}")
