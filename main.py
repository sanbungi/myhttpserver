import os
import socket
from pprint import pprint
from pathlib import Path
from utils import HTTPRequest, parse_request


def make_response(filepath: str = "."):
    path = Path(filepath)
    print(f'path is :{path}')

    # pathがrootならindexを返す
    if path == Path("/"):
        with open(f"html/index.html", "r", encoding="utf-8") as f:
            content = f.read()
            print("index.html")
            return content, len(content)
    
    tmp = 'html' + filepath
    server_file_path = Path(tmp)
    if not os.path.exists(server_file_path):
        print("404 file not found!")
        return "",0 
    
    # pathが動的で、それがファイルなら読み込んで返す。
    if os.path.isfile(server_file_path):
        with open(f"{server_file_path}", "r", encoding="utf-8") as f:
            content = f.read()
            print("dynamic")
            return content, len(content)
 
    print("return 0")
    return "",0


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
                    #if "Referer" in request.headers:
                     #   referer = request.headers["Referer"].split("/")[3:]
                      #  print(referer)

                    content, length = make_response(request.path)

                    response = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        f"Content-Length: {length}\r\n"
                        "\r\n"
                        f"{content}"
                    )

                    #print("real res")
                    #pprint(response)

                    client_sock.sendall(response.encode("utf-8"))
                    client_sock.close()

        except KeyboardInterrupt:
            print("stop server")
        finally:
            server_sock.close()


if __name__ == "__main__":
    server()
