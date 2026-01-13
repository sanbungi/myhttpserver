import os
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
import threading
from pprint import pprint
from pathlib import Path


from utils import (
    HTTPRequest,
    HTTPResponse,
    parse_request,
    get_http_reason_phrase,
    get_content_type,
    get_keep_alive,
)


def make_response(filepath: str = ".") -> HTTPResponse:
    path = Path(filepath)
    print(f"path is :{path}")

    try:
        # pathがrootならindexを返す
        if path == Path("/"):
            with open(f"html/index.html", "r", encoding="utf-8") as f:
                content = f.read()
                print("index.html")
                return HTTPResponse(200, "text/html; charset=utf-8", content)

        server_file_path = Path("html") / path.relative_to("/")

        # ディレクトリならその中のindex.htmlを返す
        if server_file_path.is_dir():
            server_file_path = server_file_path / "index.html"
            # 301リダイレクト
            return HTTPResponse(
                301,
                "text/plain; charset=utf-8",
                "301 Moved Permanently",
                {"Location": str(path) + "/index.html"},
            )

        if not os.path.exists(server_file_path):
            print("404 file not found!")
            return HTTPResponse(404, "text/plain; charset=utf-8", "404 Not Found")

        content_type, is_binary = get_content_type(server_file_path)

        if is_binary:
            with open(server_file_path, "rb") as f:
                content = f.read()
        else:
            with open(server_file_path, "r", encoding="utf-8") as f:
                text_content = f.read()
                # 日本語等だとカウントがずれるので先にエンコード
                content = text_content.encode("utf-8")

        return HTTPResponse(200, content_type, content)

    except PermissionError:
        print("403 Forbidden!")
        return HTTPResponse(403, "text/plain; charset=utf-8", "403 Forbidden")
    except Exception as e:
        print(f"500 Internal Server Error! {e}")
        return HTTPResponse(
            500, "text/plain; charset=utf-8", "500 Internal Server Error"
        )


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
                print("----- request -----")
                pprint(request)
                print("-------------------")

                response = make_response(request.path)

                use_keep_alive = get_keep_alive(request)

                print(f"Use keep-alive: {use_keep_alive}")

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

                # ヘッダーとコンテンツを送信、バイナリならそのまま送信
                header_blob = "\r\n".join(headers) + "\r\n\r\n"
                if isinstance(response.content, bytes):
                    client_sock.sendall(
                        header_blob.encode("utf-8") + response.content
                    )
                else:
                    client_sock.sendall(
                        header_blob.encode("utf-8")
                        + response.content.encode("utf-8")
                    )

                if not use_keep_alive:
                    print(f"Closing connection with {addr}")
                    return

            except socket.timeout:
                print(f"Connection with {addr} timed out.")
            except Exception as e:
                print(f"Error keeping connection with {addr}: {e}")
    except ssl.SSLError as e:
        print(f"SSL error with client {addr}: {e}")
    except Exception as e:
        print(f"Error handling client {addr}: {e}")


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
            print(f"Error loading SSL certificate: {e}")
            return

    def run_server_loop(port, ssl_context=None):
        # ipv4, tcp
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("", port))

            # 接続待ち
            server_sock.listen(5)

            print(f"start server at port {port}")

            with ThreadPoolExecutor(max_workers=10) as executor:
                while True:
                    client_sock, addr = server_sock.accept()

                    if ssl_context:
                        try:
                            client_sock = ssl_context.wrap_socket(
                                client_sock, server_side=True
                            )
                        except ssl.SSLError as e:
                            print(f"SSL handshake failed with {addr}: {e}")
                            client_sock.close()
                            continue

                    executor.submit(handle_client, client_sock, addr,)

    threads = []
    
    if USE_SSL:
        # HTTPS Server
        t_https = threading.Thread(target=run_server_loop, args=(HTTPS_PORT, context), daemon=True)
        t_https.start()
        threads.append(t_https)

    if ALSO_HTTP or not USE_SSL:
        # HTTP Server
        t_http = threading.Thread(target=run_server_loop, args=(HTTP_PORT, None), daemon=True)
        t_http.start()
        threads.append(t_http)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("stop server")


if __name__ == "__main__":
    server()
