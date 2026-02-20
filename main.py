import argparse
import logging
import os
import socket
import ssl
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from icecream import ic
from rich import print

from config import load_config
from FileCache import FileCache
from utils import (
    HttpError,
    HTTPRequest,
    HTTPResponse,
    build_response,
    error_response,
    get_content_type,
    get_keep_alive,
    parse_request,
    receive_safe_request,
    response_200,
    response_204,
    response_301,
    response_403,
    response_404,
    response_500,
    vetify_request,
)

# 設定をロード
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


def make_response(request: HTTPRequest) -> HTTPResponse:

    path = Path(request.path)
    system_logger.debug(cache.stats())

    try:
        if request.method == "OPTIONS":
            ic("OPTONS CALLS")
            return response_204()

        # pathがrootならindexを返す
        if path == Path("/"):
            content = cache.read(f"{config.server.webroot}/index.html", mode="r")
            # ipdb.set_trace()
            return response_200(content.encode("utf-8"), "text/html; charset=utf-8")

        server_file_path = Path(config.server.webroot) / path.relative_to("/")

        # ディレクトリならその中のindex.htmlを返す
        if server_file_path.is_dir():
            return response_301(str(path) + "/index.html")

        if not os.path.exists(server_file_path):
            return response_404()

        content_type, is_binary = get_content_type(server_file_path)

        if is_binary:
            content = cache.read(server_file_path, mode="rb")
        else:
            content = cache.read(server_file_path, mode="r")
            # 日本語等だとカウントがずれるので先にエンコード
            content = content.encode("utf-8")

        return response_200(content, content_type)

    except PermissionError as e:
        system_logger.error(f"PermissionError {e}")
        traceback.print_exc()
        return response_403()
    except Exception as e:
        system_logger.error(f"Failed Make Response {request} {e}")
        traceback.print_exc()
        return response_500()


def handle_client(client_sock, addr):
    try:
        keep_alive_timeout = config.server.keep_alive_timeout
        client_sock.settimeout(keep_alive_timeout)
        # ipdb.set_trace()

        request = None
        while True:
            try:
                header, body = receive_safe_request(client_sock)
                if header is None:  # headerがNoneなら終了とみなす
                    break

                request = parse_request(header, body)
                vetify_request(request)

                http_logger.debug(f"Received request: {request}")

                response = make_response(request)

            except socket.timeout:
                system_logger.debug(f"Connection with {addr} timed out.")
                return
            except HttpError as e:
                response = error_response(e.status, e.message)
                traceback.print_exc()
                # requestが未定義の場合（receive_safe_requestやparse_requestで
                # HttpErrorが発生した場合）はダミーのrequestで応答を構築する
                if request is None:
                    dummy_request = HTTPRequest("GET", "/", "HTTP/1.1", {}, b"")
                    client_sock.sendall(build_response(response, dummy_request))
                else:
                    client_sock.sendall(build_response(response, request))
                client_sock.close()
                return

            except Exception as e:
                system_logger.error(f"Error keeping connection with {addr}: {e}")
                traceback.print_exc()
                response = response_500()

            print(response)
            client_sock.sendall(build_response(response, request))

            keep_alive = get_keep_alive(request)
            ic(keep_alive)
            if not keep_alive:
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

            with ThreadPoolExecutor(max_workers=config.server.max_workers) as executor:
                while True:
                    client_sock, addr = server_sock.accept()

                    if ssl_context:
                        try:
                            client_sock = ssl_context.wrap_socket(
                                client_sock, server_side=True
                            )
                        except ssl.SSLError as e:
                            system_logger.error(
                                f"SSL handshake failed with {addr}: {e}"
                            )
                            client_sock.close()
                            continue

                    executor.submit(
                        handle_client,
                        client_sock,
                        addr,
                    )

    threads = []

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
