import socket
import ssl

host = '127.0.0.1'
port = 4433

context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(certfile="server.crt", keyfile="server.key")

bind_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
bind_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
bind_socket.bind((host, port))
bind_socket.listen(5)

print(f"HTTPS Server running on https://{host}:{port}")

while True:
    newsock, fromaddr = bind_socket.accept()
    
    try:
        with context.wrap_socket(newsock, server_side=True) as connstream:
            data = connstream.recv(1024)
            if data:
                print(f"Received request from {fromaddr}")
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=UTF-8\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    "<h1>SSL Handshake Success!</h1>"
                )
                connstream.sendall(response.encode('utf-8'))
    except ssl.SSLError as e:
        print(f"SSL Error (Expected with self-signed cert): {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        newsock.close()