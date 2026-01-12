import os
import socket
from concurrent.futures import ThreadPoolExecutor
import threading
from pprint import pprint
from pathlib import Path
from utils import HTTPRequest, parse_request, get_http_reason_phrase, get_content_type


def make_response(filepath: str = "."):
    path = Path(filepath)
    print(f"path is :{path}")

    try:
        # pathがrootならindexを返す
        if path == Path("/"):
            with open(f"html/index.html", "r", encoding="utf-8") as f:
                content = f.read()
                print("index.html")
                return content, len(content), "text/html; charset=utf-8", 200

        server_file_path = Path("html") / path.relative_to("/")

        if not os.path.exists(server_file_path):
            print("404 file not found!")
            return (
                "404 Not Found",
                len("404 Not Found"),
                "text/plain; charset=utf-8",
                404,
            )

        content_type, is_binary = get_content_type(server_file_path)

        if is_binary:
            with open(server_file_path, "rb") as f:
                content = f.read()
        else:
            with open(server_file_path, "r", encoding="utf-8") as f:
                content = f.read()

        return content, len(content), content_type, 200

    except PermissionError:
        print("403 Forbidden!")
        return "403 Forbidden", len("403 Forbidden"), "text/plain; charset=utf-8", 403
    except Exception as e:
        print(f"500 Internal Server Error! {e}")
        return (
            "500 Internal Server Error",
            len("500 Internal Server Error"),
            "text/plain; charset=utf-8",
            500,
        )


def handle_client(client_sock, addr):
    try:
        with client_sock:
            print(f"Connect by {addr} Thead: {threading.current_thread().name}")

            raw_request = client_sock.recv(1024).decode("utf-8")
            if not raw_request:
                return

            request = parse_request(raw_request)
            print("----- request -----")
            pprint(request)
            print("-------------------")

            content, length, content_type, status_code = make_response(request.path)

            response = (
                f"HTTP/1.1 {status_code} {get_http_reason_phrase(status_code)}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {length}\r\n"
                "\r\n"
            )

            if isinstance(content, str):
                response += content

            if isinstance(content, bytes):
                header = response.encode("utf-8")
                client_sock.sendall(header + content)
            else:
                client_sock.sendall(response.encode("utf-8"))

            client_sock.close()

    except Exception as e:
        print(f"Error handling client {addr}: {e}")


def server():
    # ipv4, tcp
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("", 8000))

        # 接続待ち
        server_sock.listen(5)

        print("start server at port 8000")

        with ThreadPoolExecutor(max_workers=10) as executor:
            try:
                while True:
                    client_sock, addr = server_sock.accept()

                    executor.submit(handle_client, client_sock, addr)

            except KeyboardInterrupt:
                print("stop server")
            finally:
                server_sock.close()


if __name__ == "__main__":
    server()
