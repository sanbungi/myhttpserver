import asyncio
import logging
import os
import socket
import ssl
from functools import partial

from .config_model import ServerConfig
from .ip_table import InMemoryIPTable
from .worker import WorkerConnectionLimiter, handle_client

logger = logging.getLogger(__name__)

_TLS_MIN_VERSION_BY_NAME = {
    "TLS1": ssl.TLSVersion.TLSv1,
    "TLS1.0": ssl.TLSVersion.TLSv1,
    "TLSV1": ssl.TLSVersion.TLSv1,
    "TLSV1.0": ssl.TLSVersion.TLSv1,
    "TLS1.1": ssl.TLSVersion.TLSv1_1,
    "TLSV1.1": ssl.TLSVersion.TLSv1_1,
    "TLS1.2": ssl.TLSVersion.TLSv1_2,
    "TLSV1.2": ssl.TLSVersion.TLSv1_2,
}
if hasattr(ssl.TLSVersion, "TLSv1_3"):
    _TLS_MIN_VERSION_BY_NAME["TLS1.3"] = ssl.TLSVersion.TLSv1_3
    _TLS_MIN_VERSION_BY_NAME["TLSV1.3"] = ssl.TLSVersion.TLSv1_3


def _resolve_tls_min_version(min_version: str) -> ssl.TLSVersion:
    candidate = str(min_version).strip().upper().replace("_", ".").replace("-", ".")
    resolved = _TLS_MIN_VERSION_BY_NAME.get(candidate)
    if resolved is not None:
        return resolved

    logger.warning("Unknown tls.min_version=%r. Falling back to TLS1.2.", min_version)
    return ssl.TLSVersion.TLSv1_2


class HTTPServer:
    def __init__(
        self,
        host="127.0.0.1",
        port=8080,
        config: ServerConfig = None,
        ip_table: InMemoryIPTable | None = None,
        max_connections_per_worker: int = 1024,
    ):
        self.host = host
        self.port = port
        self.config = config
        self.ip_table = ip_table
        self.worker_limiter = WorkerConnectionLimiter(max_connections_per_worker)

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
            logger.exception("Bind failed: %s", e)
            sock.close()
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
            _tls = self.config.tls
            context.minimum_version = _resolve_tls_min_version(_tls.min_version)
            try:
                context.load_cert_chain(certfile=_tls.cert, keyfile=_tls.key)
            except Exception as e:
                logger.exception(
                    "SSL load error cert=%s key=%s: %s", _tls.cert, _tls.key, e
                )
                return

            # 既存のソケットを使ってサーバーを開始
            try:
                server = await asyncio.start_server(
                    partial(
                        handle_client,
                        config=self.config,
                        ip_table=self.ip_table,
                        worker_limiter=self.worker_limiter,
                    ),
                    sock=sock,
                    ssl=context,
                )
                async with server:
                    await server.serve_forever()

            except ssl.SSLError as e:
                logger.warning("SSL handshake failed with %s: %s", self.host, e)
        else:
            server = await asyncio.start_server(
                partial(
                    handle_client,
                    config=self.config,
                    ip_table=self.ip_table,
                    worker_limiter=self.worker_limiter,
                ),
                sock=sock,
            )
            async with server:
                await server.serve_forever()

        pid = os.getpid()
        logger.info("[PID: %s] Serving on http://%s:%s", pid, self.host, self.port)
