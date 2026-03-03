import multiprocessing
import os
import socket
import threading


def handle_client(conn, addr):
    """既存のロジック"""
    try:
        data = conn.recv(1024)
        if not data:
            return
        request = data.decode()

        pid = os.getpid()
        # print(f"[PID:{pid}] Thread:{threading.get_ident()} 接続")

        response_body = f"Hello from Process {pid}!"
        response_headers = [
            "HTTP/1.1 200 OK",
            "Content-Type: text/plain; charset=utf-8",
            f"Content-Length: {len(response_body)}",
            "Connection: close",
            "\r\n",
        ]
        response = "\r\n".join(response_headers) + response_body
        conn.sendall(response.encode())
    except Exception:
        pass
    finally:
        conn.close()


def run_worker():
    """
    1つのワーカープロセスのメイン処理
    """
    host = "127.0.0.1"
    port = 8888

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # 複数プロセスで同じポートを共有する設定
    if hasattr(socket, "SO_REUSEPORT"):
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    try:
        server_socket.bind((host, port))
        server_socket.listen(128)

        pid = os.getpid()
        print(f"[PID:{pid}] Threaded Worker Started")

        while True:
            conn, addr = server_socket.accept()
            # プロセスの中でさらにスレッドを作成
            t = threading.Thread(target=handle_client, args=(conn, addr))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        server_socket.close()


def main():
    workers = []
    cpu_count = multiprocessing.cpu_count()
    print(f"Starting {cpu_count} threaded workers...")

    for _ in range(cpu_count):
        p = multiprocessing.Process(target=run_worker)
        p.start()
        workers.append(p)

    try:
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        for p in workers:
            p.terminate()


if __name__ == "__main__":
    main()
