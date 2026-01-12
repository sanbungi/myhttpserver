import os
import socket
from pprint import pprint
from pathlib import Path
from utils import HTTPRequest, parse_request, get_http_reason_phrase


def make_response(filepath: str = "."):
    path = Path(filepath)
    print(f"path is :{path}")

    # pathがrootならindexを返す
    if path == Path("/"):
        with open(f"html/index.html", "r", encoding="utf-8") as f:
            content = f.read()
            print("index.html")
            return content, len(content), "text/html; charset=utf-8", 200

    tmp = "html" + filepath
    server_file_path = Path(tmp)
    if not os.path.exists(server_file_path):
        print("404 file not found!")
        return "404 Not Found", len("404 Not Found"), "text/plain; charset=utf-8", 404

    ext = server_file_path.suffix.lower()
    if ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]:
        content_type = f"image/{ext[1:]}"  # .jpg -> image/jpg など
        is_binary = True
    elif ext in [".html", ".txt"]:
        content_type = "text/html; charset=utf-8"
        is_binary = False
    else:
        content_type = "application/octet-stream"  # デフォルトでバイナリ扱い
        is_binary = True

    if is_binary:
        with open(server_file_path, "rb") as f:  # バイナリモード
            content = f.read()
    else:
        with open(server_file_path, "r", encoding="utf-8") as f:  # テキストモード
            content = f.read()

    print(f"serving {ext} file")
    return content, len(content), content_type, 200


def server():
    # ipv4, tcp
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("", 8000))

        # 接続待ち
        server_sock.listen(1)

        print("start server at port 8000")

        try:
            while True:
                client_sock, addr = server_sock.accept()
                with client_sock:
                    print(f"Connect by {addr}")

                    raw_request = client_sock.recv(1024).decode("utf-8")
                    if not raw_request:
                        continue

                    request = parse_request(raw_request)
                    print("----- request -----")
                    pprint(request)
                    print("-------------------")

                    # referer = []
                    # if "Referer" in request.headers:
                    #   referer = request.headers["Referer"].split("/")[3:]
                    #  print(referer)

                    content, length, content_type, status_code = make_response(
                        request.path
                    )

                    response = (
                        f"HTTP/1.1 {status_code} {get_http_reason_phrase(status_code)}\r\n"
                        f"Content-Type: {content_type}\r\n"
                        f"Content-Length: {length}\r\n"
                        "\r\n"
                    )

                    if isinstance(content, str):
                        response += content
                    else:
                        pass

                    if isinstance(content, bytes):
                        header = response.encode("utf-8")
                        client_sock.sendall(header + content)
                    else:
                        client_sock.sendall(response.encode("utf-8"))

                    client_sock.close()

        except KeyboardInterrupt:
            print("stop server")
        finally:
            server_sock.close()


if __name__ == "__main__":
    server()
