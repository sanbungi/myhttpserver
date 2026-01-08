import socket
from pprint import pprint
from pathlib import Path
from utils import HTTPRequest, parse_request


def make_response(path: str = ""):
    with open("html/index.html", "r", encoding="utf-8") as f:
        content = f.read()
        # pprint(content)

    return content, len(content)


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
                    print(type(request))
                    pprint(request.headers)

                    if "Referer" in request.headers:
                        path = request.headers["Referer"].split("/")[3:]
                        print(path)

                    content, length = make_response(path)

                    response = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        f"Content-Length: {length}\r\n"
                        "\r\n"
                        f"{content}"
                    )
                    client_sock.sendall(response.encode("utf-8"))
                    client_sock.close()

        except KeyboardInterrupt:
            print("stop server")
        finally:
            server_sock.close()


if __name__ == "__main__":
    server()
