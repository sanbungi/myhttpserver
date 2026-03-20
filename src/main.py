import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import sys

import hcl
import uvloop

try:
    from src.server.autoindex_page import prime_autoindex_cache_for_server
    from src.server.config_model import (
        AppConfig,
        LoggingConfig,
        RouteConfig,
        ServerConfig,
    )
    from src.server.core import HTTPServer
    from src.server.ip_table import InMemoryIPTable
    from src.server.logging_config import setup_logging
except ModuleNotFoundError:
    from server.autoindex_page import prime_autoindex_cache_for_server
    from server.config_model import AppConfig, LoggingConfig, RouteConfig, ServerConfig
    from server.core import HTTPServer
    from server.ip_table import InMemoryIPTable
    from server.logging_config import setup_logging

logger = logging.getLogger(__name__)

# ループのエンジンに高パフォーマンスなuvloopを用いる
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def parse_args():
    parser = argparse.ArgumentParser(description="My HTTP Server")
    parser.add_argument(
        "--webroot",
        type=str,
        default="test-assets/html/",
        help="Web root directory (default: test-assets/html/)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="test-assets/config/example.hcl",
        help="Configuration file path (default: test-assets/config/example.hcl)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override HTTP port",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        dest="http_port",
        default=None,
        help="Backward-compatible alias for --port",
    )
    parser.add_argument(
        "--enable-https",
        action="store_true",
        default=False,
        help="Enable HTTPS server",
    )
    parser.add_argument(
        "--https-port",
        type=int,
        default=443,
        help="HTTPS port (default: 443)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--debug-ip-table",
        action="store_true",
        default=False,
        help="Enable realtime IP table logs (acquire/release/deny)",
    )

    return parser.parse_args()


def _build_logging_kwargs(logging_config: LoggingConfig) -> dict:
    return {
        "app_name": logging_config.app_name,
        "log_dir": logging_config.log_dir,
        "log_file": logging_config.error_log_file,
        "access_log_file": logging_config.access_log_file,
        "access_logger_name": logging_config.access_logger_name,
        "access_format": logging_config.access_format,
        "access_datefmt": logging_config.access_datefmt,
        "max_bytes": logging_config.max_bytes,
        "backup_count": logging_config.backup_count,
        "level_name": logging_config.level,
    }


def run_worker_process(
    host,
    port,
    config: ServerConfig,
    logging_config: LoggingConfig,
    max_connections_per_worker: int = 1024,
    shared_ip_connections=None,
    shared_ip_lock=None,
    ban_list_file: str | None = None,
    debug_ip_table: bool = False,
    max_connections_per_ip: int = 20,
    max_body_size: int = 1024 * 1024 * 2,
):
    setup_logging(**_build_logging_kwargs(logging_config))
    prime_autoindex_cache_for_server(config)
    ip_table = InMemoryIPTable(
        max_connections_per_ip=max_connections_per_ip,
        active_connections=shared_ip_connections,
        lock=shared_ip_lock,
        ban_list_file=ban_list_file,
        debug_enabled=debug_ip_table,
    )
    server = HTTPServer(
        host=host,
        port=port,
        config=config,
        ip_table=ip_table,
        max_connections_per_worker=max_connections_per_worker,
        max_body_size=max_body_size,
    )

    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


def main():
    args = parse_args()

    # config読み込み
    with open(args.config, "r") as fp:
        raw_obj = hcl.load(fp)

    app_config = AppConfig.load(raw_obj)
    setup_logging(**_build_logging_kwargs(app_config.global_settings.logging))

    # webrootを実態パスに
    webroot = os.path.abspath(args.webroot)
    if not os.path.isdir(webroot):
        logger.error("No such directory: %s", webroot)
        sys.exit(1)

    # Configが指定されない場合に、仮のConfigを生成して起動する（テスト用）
    port_override = args.http_port if args.http_port is not None else args.port
    if port_override is not None:
        compat_server = ServerConfig(
            name="compat",
            host=args.host,
            port=port_override,
            root=webroot,
            routes=[RouteConfig(path="/", type="static", index=["index.html"])],
        )
        logger.info(
            "Starting compatibility server on %s:%s ...", args.host, port_override
        )
        run_worker_process(
            args.host,
            port_override,
            compat_server,
            app_config.global_settings.logging,
            app_config.global_settings.max_connections,
            ban_list_file=app_config.global_settings.ban_list_file,
            debug_ip_table=args.debug_ip_table,
            max_connections_per_ip=app_config.global_settings.max_connections_per_ip,
            max_body_size=app_config.global_settings.max_body_size,
        )
        return

    def shutdown_handler(signum, frame):
        logger.info("Server shutdown...")

        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # CPUコアの割当計算、各プロセスに最低1コアは割当たるように
    cpu_count = multiprocessing.cpu_count()
    server_count = len(app_config.servers)
    worker_processes = max(1, app_config.global_settings.worker_processes)
    base, remainder = divmod(worker_processes, server_count)
    workers_per_server = [
        base + (1 if i < remainder else 0) for i in range(server_count)
    ]

    logger.info("CPU: %s", cpu_count)
    logger.info("Servers: %s", server_count)
    logger.info("Workers per server: %s", workers_per_server)
    logger.info("Total workers: %s", sum(workers_per_server))

    # ワーカープロセスの起動
    with multiprocessing.Manager() as manager:
        shared_ip_connections = manager.dict()
        shared_ip_lock = manager.Lock()

        workers = []
        for server, worker_count in zip(app_config.servers, workers_per_server):
            port = server.port
            logger.info("Starting %s workers on port %s...", worker_count, port)

            for _ in range(worker_count):
                p = multiprocessing.Process(
                    target=run_worker_process,
                    args=(
                        args.host,
                        port,
                        server,
                        app_config.global_settings.logging,
                        app_config.global_settings.max_connections,
                        shared_ip_connections,
                        shared_ip_lock,
                        app_config.global_settings.ban_list_file,
                        args.debug_ip_table,
                        app_config.global_settings.max_connections_per_ip,
                        app_config.global_settings.max_body_size,
                    ),
                )
                p.start()
                workers.append(p)

        # メインプロセスの待機ループ
        try:
            for p in workers:
                p.join()
        except KeyboardInterrupt:
            logger.info("Stopping all workers...")
            for p in workers:
                if p.is_alive():
                    p.terminate()
                    p.join()


if __name__ == "__main__":
    try:
        main()
    finally:
        logger.info("Server stopped.")
