import argparse
import ipaddress
import logging
import os
import socket
import ssl
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

import hcl
import requests
from icecream import ic

from config import load_config
from FileCache import FileCache
from labs.config_model import AppConfig
from utils import (
    HttpError,
    HTTPRequest,
    HTTPResponse,
    build_response,
    find_best_route,
    get_content_type,
    parse_request,
    receive_safe_request,
    response_any,
    vetify_request,
)

# 設定をロード
with open("labs/example.hcl", "r") as fp:
    raw_obj = hcl.load(fp)

new_config = AppConfig.load(raw_obj)
config = load_config()

# ロガーの定義
system_logger = logging.getLogger("system")
http_logger = logging.getLogger("http")

cache = FileCache()


def setup_logging():
    """ログ設定の初期化"""

    log_dir = Path(config.logging.dir)
    # logsディレクトリを作成
    log_dir.mkdir(parents=True, exist_ok=True)

    # システムログ設定
    system_logger.setLevel(config.logging.system_level.upper())
    system_log_path = log_dir / config.logging.system_log
    system_handler = RotatingFileHandler(
        system_log_path,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
    )
    system_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    system_logger.addHandler(system_handler)
    system_console = logging.StreamHandler()
    system_console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    system_logger.addHandler(system_console)

    # HTTPアクセスログ設定
    http_logger.setLevel(config.logging.access_level.upper())
    access_log_path = log_dir / config.logging.access_log
    http_handler = RotatingFileHandler(
        access_log_path,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
    )
    http_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    http_logger.addHandler(http_handler)
    http_console = logging.StreamHandler()
    http_console.setFormatter(logging.Formatter("%(asctime)s [HTTP] %(message)s"))
    http_logger.addHandler(http_console)


# ログ設定を適用
setup_logging()


def parse_args():
    parser = argparse.ArgumentParser(description="My HTTP Server")
    parser.add_argument(
        "--webroot",
        type=str,
        default=config.server.webroot,
        help=f"Web root directory (default: {config.server.webroot})",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.toml",
        help="Configuration file path (default: config.toml)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=config.server.http_port,
        help=f"HTTP port (default: {config.server.http_port})",
    )
    parser.add_argument(
        "--enable-https",
        action="store_true",
        default=config.server.use_ssl,
        help="Enable HTTPS server",
    )
    parser.add_argument(
        "--https-port",
        type=int,
        default=config.server.https_port,
        help=f"HTTPS port (default: {config.server.https_port})",
    )

    return parser.parse_args()


def route_response(request: HTTPRequest) -> HTTPResponse:

    request_path = Path(request.path)

    try:
        if request.method == "OPTIONS":
            ic("OPTONS CALLS")
            return response_any(204, header={"Allow": "GET, HEAD, OPTIONS"})

        server = new_config.servers[0]  # TODO
        route = find_best_route(server, request_path)
        ic(route)

        if not route:
            return response_any(404)

        if route.type == "static":
            if route.security:
                ip = ipaddress.ip_address(request.remote_addr)
                network = ipaddress.ip_network(route.security.ip_allow[0], strict=False)
                if ip in network:
                    ic("Access OK: {ip} in {network}")
                    pass  # →含まれているのでOK
                else:
                    # 400を返して禁止を表現
                    return response_any(400)

            if request_path == Path("/"):
                content = cache.read(f"{server.root}/{route.index[0]}", mode="r")
                return response_any(
                    200,
                    "text/html charset=utf-8",
                    str(content).encode("utf-8"),
                )

            server_file_path = Path(server.root) / request_path.relative_to("/")
            ic(server_file_path)

            # ディレクトリならその中のindex.htmlを返す
            if server_file_path.is_dir():
                return response_any(code=301, header={"Location": "index.html"})

            if not os.path.exists(server_file_path):
                return response_any(404)

            content_type, is_binary = get_content_type(server_file_path)

            if is_binary:
                content = cache.read(server_file_path, mode="rb")
            else:
                content = cache.read(server_file_path, mode="r")
                # 日本語等だとカウントがずれるので先にエンコード
                content = content.encode("utf-8")

            return response_any(200, content_type, content)
        # リバースプロキシ
        elif route.type == "proxy":
            send_header = dict(request.headers)
            send_header.pop("host", None)
            send_header.pop("Host", None)
            try:
                resp = requests.request(
                    method=request.method,
                    url=str(f"http://localhost:1234/{request_path}"),
                    headers=send_header,
                    data=request.body,  # ボディがある場合
                    timeout=10,  # タイムアウト設定は必須
                )

                ic(resp.status_code)
                ic(dict(resp.headers))
                ic(resp.text)
                return response_any(
                    resp.status_code,
                    resp.headers["Content-Type"],
                    resp.content,
                    resp.headers,
                )
            except requests.RequestException as e:
                ic(f"Upstream error: {e}")
                traceback.print_exc()
                return None

        # 固定値のレスポンスを貸す場合（Configにて指定)
        elif route.type == "raw":
            if route.respond:
                ic(route)
                return error_response(route.respond.status, route.respond.body)
            return response_any(500)

        # 301リダイレクトの指示（Configにも未実装）
        elif route.type == "redirect":
            ic("REDIRECT")
            ic(route.redirect)

            # return response_301(route.redirect.url)
            return response_any(
                code=route.redirect.code, header={"Location": route.redirect.url}
            )
        else:
            return response_any(500)

    except PermissionError as e:
        system_logger.error(f"PermissionError {e}")
        traceback.print_exc()
        return response_any(403)
    except Exception as e:
        system_logger.error(f"Failed Make Response {request} {e}")
        traceback.print_exc()
        return response_any(500)


def handle_client(client_sock, addr):
    try:
        keep_alive_timeout = config.server.keep_alive_timeout
        client_sock.settimeout(keep_alive_timeout)
        # ipdb.set_trace()

        request = None
        while True:
            try:
                header, body = receive_safe_request(client_sock, addr)
                if header is None:  # headerがNoneなら終了とみなす
                    break

                request = parse_request(header, body, addr)
                vetify_request(request)

                response = route_response(request)
                # ic(response)

            except socket.timeout:
                system_logger.debug(f"Connection with {addr} timed out.")
                client_sock.close()
                return
            except HttpError as e:
                if e.status == 405:
                    response = response_any(
                        code=e.status, header={"Allow": "GET, HEAD, OPTIONS"}
                    )  # HACK Configから参照
                    # ipdb.set_trace()
                else:
                    response = response_any(e.status)
                traceback.print_exc()

            except Exception as e:
                system_logger.error(f"Error keeping connection with {addr}: {e}")
                traceback.print_exc()
                response = response_any(500)

            final_response, keep_alive = build_response(response, request)
            client_sock.sendall(final_response)

            ic(keep_alive)
            if keep_alive:
                continue

            client_sock.close()
            return

    except ssl.SSLError as e:
        system_logger.error(f"SSL error with client {addr}: {e}")
    except Exception as e:
        system_logger.error(f"Error handling client {addr}: {e}")
        traceback.print_exc()
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def server():
    context = None
    if config.server.use_ssl:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(
                certfile=config.ssl.cert_file, keyfile=config.ssl.key_file
            )
        except Exception as e:
            system_logger.error(f"Error loading SSL certificate: {e}")
            return

    def run_server_loop(port, ssl_context=None):
        # ipv4, tcp
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            server_sock.bind(("", port))

            # 接続待ち
            server_sock.listen(5)

            protocol = "HTTPS" if ssl_context else "HTTP"
            system_logger.info(f"Start {protocol} server at port {port}")

            with ThreadPoolExecutor(
                max_workers=new_config.global_settings.worker_processes
            ) as executor:
                while True:
                    client_sock, client_ip_info = server_sock.accept()
                    address = client_ip_info[0]  # 0→アドレス 1→ポート
                    if ssl_context:
                        try:
                            client_sock = ssl_context.wrap_socket(
                                client_sock, server_side=True
                            )
                        except ssl.SSLError as e:
                            system_logger.error(
                                f"SSL handshake failed with {address}: {e}"
                            )
                            client_sock.close()
                            continue

                    executor.submit(
                        handle_client,
                        client_sock,
                        address,
                    )

    threads = []

    # TODO newconfig.servers の分だけ生成する。

    if config.server.use_ssl:
        # HTTPS Server
        t_https = threading.Thread(
            target=run_server_loop,
            args=(config.server.https_port, context),
            daemon=True,
        )
        t_https.start()
        threads.append(t_https)

    if config.server.also_http or not config.server.use_ssl:
        # HTTP Server
        t_http = threading.Thread(
            target=run_server_loop,
            args=(config.server.http_port, None),
            daemon=True,
        )
        t_http.start()
        threads.append(t_http)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        system_logger.info("Stop server")


if __name__ == "__main__":
    args = parse_args()
    config.server.webroot = args.webroot
    config.server.http_port = args.http_port
    config.server.use_ssl = args.enable_https
    config.server.https_port = args.https_port
    config_path = args.config
    if config_path != "config.toml":
        config = load_config(config_path)
    system_logger.info(f"Using webroot: {config.server.webroot}")
    server()
