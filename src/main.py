import argparse
import asyncio
import logging

import uvloop

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
import multiprocessing
import os
import signal
import sys

import hcl

try:
    from src.server.config_model import AppConfig, RouteConfig, ServerConfig
    from src.server.core import HTTPServer
    from src.server.logging_config import setup_logging
except ModuleNotFoundError:
    from server.config_model import AppConfig, RouteConfig, ServerConfig
    from server.core import HTTPServer
    from server.logging_config import setup_logging

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="My HTTP Server")
    parser.add_argument(
        "--webroot",
        type=str,
        default="html/",
        help="Web root directory (default: html/)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/example.hcl",
        help="Configuration file path (default: config/example.hcl)",
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

    return parser.parse_args()


def run_worker_process(host, port, config: ServerConfig):
    setup_logging(app_name="myhttpserver")
    server = HTTPServer(host=host, port=port, config=config)

    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


def main():
    setup_logging(app_name="myhttpserver")
    args = parse_args()

    with open(args.config, "r") as fp:
        raw_obj = hcl.load(fp)

    app_config = AppConfig.load(raw_obj)

    # webrootを実態パスに
    webroot = os.path.abspath(args.webroot)
    if not os.path.isdir(webroot):
        logger.error("No such directory: %s", webroot)
        sys.exit(1)

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
        run_worker_process(args.host, port_override, compat_server)
        return

    def shutdown_handler(signum, frame):
        logger.info("Server shutdown...")

        logger.info("MOCK: Server stop")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    cpu_count = multiprocessing.cpu_count()
    server_count = len(app_config.servers)
    worker_processes = max(1, app_config.global_settings.worker_processes)

    base, remainder = divmod(worker_processes, server_count)

    workers_per_server = [
        base + (1 if i < remainder else 0) for i in range(server_count)
    ]

    workers = []

    logger.info("CPU: %s", cpu_count)
    logger.info("Servers: %s", server_count)
    logger.info("Workers per server: %s", workers_per_server)
    logger.info("Total workers: %s", sum(workers_per_server))

    # ワーカープロセスの起動
    for server, worker_count in zip(app_config.servers, workers_per_server):
        port = server.port
        logger.info("Starting %s workers on port %s...", worker_count, port)

        for _ in range(worker_count):
            p = multiprocessing.Process(
                target=run_worker_process,
                args=(args.host, port, server),
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
