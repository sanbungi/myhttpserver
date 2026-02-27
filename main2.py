import argparse
import asyncio
import multiprocessing
import os
import signal
import sys

import hcl
from icecream import ic

from config_model import AppConfig
from src.server.core import HTTPServer


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
        default=80,
        help="HTTP port (default: 80)",
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


def run_worker_process(host, port, config):
    server = HTTPServer(host=host, port=port, config=config)

    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


def main():
    args = parse_args()

    with open("config/example.hcl", "r") as fp:
        raw_obj = hcl.load(fp)

    app_config = AppConfig.load(raw_obj)

    # webrootを実態パスに
    webroot = os.path.abspath(args.webroot)
    if not os.path.isdir(webroot):
        ic(f"No such directory: {webroot}")
        sys.exit(1)

    ic("MOCK: Server(host={args.host}, port={args.port}, webroot={args.webroot}")

    def shutdown_handler(signum, frame):
        ic("Server shutdown...")

        ic("MOCK: Server stop")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    cpu_count = 8
    workers = []

    print(f"Starting server with {cpu_count} workers on port {args.port}...")

    # ワーカープロセスの起動
    for _ in range(cpu_count):
        p = multiprocessing.Process(
            target=run_worker_process, args=(args.host, args.port, app_config)
        )
        p.start()
        workers.append(p)

    # メインプロセスの待機ループ
    try:
        # 子プロセスが生きているか監視（joinだとCtrl+Cが効きにくい場合があるため）
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        print("\nStopping all workers...")
        for p in workers:
            if p.is_alive():
                p.terminate()  # 強制終了シグナルを送る
                p.join()

    print("Server stopped.")


if __name__ == "__main__":
    main()
