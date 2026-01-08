import socket
from pprint import pprint

def make_response(path:str=""):
    with open('html/index.html','r',encoding='utf-8') as f:
        content = f.read()
        pprint(content)

    return content,len(content)


def server():
    # ipv4, tcp
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('',8000))
        
        # 接続待ち
        server_sock.listen(1)

        print("start server at port 8000")
        
        try:
            while True:
                client_sock, addr = server_sock.accept()
                with client_sock:
                    print(f'Connect by {addr}')

                    request = client_sock.recv(1024).decode('utf-8')
                    pprint(request)

                    content, length = make_response()

                    response = (
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: text/html; charset=utf-8\r\n"
                            f"Content-Length: {length}\r\n"
                            "\r\n"
                            f"{content}"
                    )
                    client_sock.sendall(response.encode('utf-8'))
                    client_sock.close()

        except KeyboardInterrupt:
            print("stop server")
        finally:
            server_sock.close()

if __name__ == '__main__':
    server()
