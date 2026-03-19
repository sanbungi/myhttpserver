import asyncio
import multiprocessing
import os
import socket


# 既存のハンドラロジックはそのまま
async def handle_client(reader, writer):
    try:
        data = await reader.read(1024)
        if not data:
            return
        request = data.decode()

        # ログにプロセスID(PID)を追加して、分散されているか確認
        pid = os.getpid()
        # print(f"[PID:{pid}] 接続あり")

        response_body = f"Hello from Process {pid}!"
        response_headers = [
            "HTTP/1.1 200 OK",
            "Content-Type: text/plain; charset=utf-8",
            f"Content-Length: {len(response_body)}",
            "Connection: close",
            "\r\n",
        ]
        response = "\r\n".join(response_headers) + response_body

        writer.write(response.encode())
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


def run_worker():
    """
    1つのワーカープロセスのメイン処理
    """

    async def server_main():
        # ソケットを手動作成して SO_REUSEPORT をセットする
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Linux/Mac等で利用可能。複数プロセスが同じポートをバインドできる
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(("127.0.0.1", 8888))

        # start_server にソケットオブジェクトを渡す
        server = await asyncio.start_server(handle_client, sock=sock)

        pid = os.getpid()
        print(f"[PID:{pid}] Async Worker Started on 127.0.0.1:8888")

        async with server:
            await server.serve_forever()

    try:
        asyncio.run(server_main())
    except KeyboardInterrupt:
        pass


def main():
    # CPUのコア数分だけプロセスを立ち上げる
    workers = []
    cpu_count = multiprocessing.cpu_count()
    print(f"Starting {cpu_count} workers...")

    for _ in range(cpu_count):
        p = multiprocessing.Process(target=run_worker)
        p.start()
        workers.append(p)

    # メインプロセスは待機
    try:
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        print("\nStopping all workers...")
        for p in workers:
            p.terminate()


if __name__ == "__main__":
    main()
