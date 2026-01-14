import os
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
import threading
from pprint import pprint
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import gzip
from io import BytesIO

from utils import (
    HTTPRequest,
    HTTPResponse,
    parse_request,
    get_http_reason_phrase,
    get_content_type,
    get_keep_alive,
    response_200,
    response_301,
    response_403,
    response_404,
    response_500,
)

# logsディレクトリを作成
os.makedirs("logs", exist_ok=True)


# システムログ設定
system_logger = logging.getLogger("system")
system_logger.setLevel(logging.INFO)
system_handler = RotatingFileHandler(
    "logs/system.log", maxBytes=10 * 1024 * 1024, backupCount=5
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
http_logger = logging.getLogger("http")
http_logger.setLevel(logging.DEBUG)
http_handler = RotatingFileHandler(
    "logs/access.log", maxBytes=10 * 1024 * 1024, backupCount=5
)
http_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
http_logger.addHandler(http_handler)
http_console = logging.StreamHandler()
http_console.setFormatter(logging.Formatter("%(asctime)s [HTTP] %(message)s"))
http_logger.addHandler(http_console)


def make_response(filepath: str = ".") -> HTTPResponse:
    path = Path(filepath)

    try:
        # pathがrootならindexを返す
        if path == Path("/"):
            with open(f"html/index.html", "r", encoding="utf-8") as f:
                content = f.read()
                return response_200(content.encode("utf-8"), "text/html; charset=utf-8")

        server_file_path = Path("html") / path.relative_to("/")

        # ディレクトリならその中のindex.htmlを返す
        if server_file_path.is_dir():
            return response_301(str(path) + "/index.html")

        if not os.path.exists(server_file_path):
            return response_404()

        content_type, is_binary = get_content_type(server_file_path)

        if is_binary:
            with open(server_file_path, "rb") as f:
                content = f.read()
        else:
            with open(server_file_path, "r", encoding="utf-8") as f:
                text_content = f.read()
                # 日本語等だとカウントがずれるので先にエンコード
                content = text_content.encode("utf-8")

        return response_200(content, content_type)

    except PermissionError:
        return response_403()
    except Exception as e:
        return response_500()


def handle_client(client_sock, addr):
    try:
        keep_alive_timeout = 75
        client_sock.settimeout(keep_alive_timeout)

        while True:
            try:
                raw_request = client_sock.recv(1024).decode("utf-8")

                if not raw_request:
                    return

                request = parse_request(raw_request)
                http_logger.debug(f"Received request: {request}")

                response = make_response(request.path)

                use_keep_alive = get_keep_alive(request)

                # HTTPアクセスログ
                http_logger.info(
                    f"{addr[0]} - {request.method} {request.path} - {response.status_code}"
                )
                accept_encoding = request.headers.get("Accept-Encoding", "")
                http_logger.debug(f"Accept-Encoding: {accept_encoding}")

                # ヘッダーをリスト形式で組み立て、その後にCRLFで結合する
                headers = [
                    f"HTTP/1.1 {response.status_code} {get_http_reason_phrase(response.status_code)}",
                    f"Content-Type: {response.content_type}",
                    f"Content-Length: {response.content_length}",
                ]

                for key, value in response.headers.items():
                    headers.append(f"{key}: {value}")

                if use_keep_alive:
                    headers.append("Connection: keep-alive")
                    headers.append(f"Keep-Alive: timeout={keep_alive_timeout}")
                else:
                    headers.append("Connection: close")

                # サーバ名を追加
                headers.append("Server: MyHTTPServer/0.1")

                # gzip要求があれば圧縮
                if "gzip" in accept_encoding:
                    headers.append("Content-Encoding: gzip")
                    out = BytesIO()
                    with gzip.GzipFile(fileobj=out, mode="wb") as f:
                        f.write(response.content)
                    response.content = out.getvalue()
                    headers[2] = f"Content-Length: {len(response.content)}"

                # ヘッダーとコンテンツを送信、バイナリならそのまま送信
                header_blob = "\r\n".join(headers) + "\r\n\r\n"
                if isinstance(response.content, bytes):
                    client_sock.sendall(header_blob.encode("utf-8") + response.content)
                else:
                    client_sock.sendall(
                        header_blob.encode("utf-8") + response.content.encode("utf-8")
                    )

                if not use_keep_alive:
                    system_logger.debug(f"Closing connection with {addr}")
                    return

            except socket.timeout:
                system_logger.debug(f"Connection with {addr} timed out.")
            except Exception as e:
                system_logger.error(f"Error keeping connection with {addr}: {e}")
    except ssl.SSLError as e:
        system_logger.error(f"SSL error with client {addr}: {e}")
    except Exception as e:
        system_logger.error(f"Error handling client {addr}: {e}")


def server():
    USE_SSL = True
    ALSO_HTTP = True
    HTTP_PORT = 8000
    HTTPS_PORT = 8443

    context = None
    if USE_SSL:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certfile="server.crt", keyfile="server.key")
        except Exception as e:
            system_logger.error(f"Error loading SSL certificate: {e}")
            return

    def run_server_loop(port, ssl_context=None):
        # ipv4, tcp
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("", port))

            # 接続待ち
            server_sock.listen(5)

            protocol = "HTTPS" if ssl_context else "HTTP"
            system_logger.info(f"Start {protocol} server at port {port}")

            with ThreadPoolExecutor(max_workers=10) as executor:
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

    if USE_SSL:
        # HTTPS Server
        t_https = threading.Thread(
            target=run_server_loop, args=(HTTPS_PORT, context), daemon=True
        )
        t_https.start()
        threads.append(t_https)

    if ALSO_HTTP or not USE_SSL:
        # HTTP Server
        t_http = threading.Thread(
            target=run_server_loop, args=(HTTP_PORT, None), daemon=True
        )
        t_http.start()
        threads.append(t_http)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        system_logger.info("Stop server")


if __name__ == "__main__":
    server()
