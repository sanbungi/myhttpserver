import logging
import socket
import time

from src.server.config_model import GlobalConfig
from src.server.ip_table import InMemoryIPTable

HOST = "localhost"
SOCKET_TIMEOUT = 2.0
PER_IP_LIMIT = 20


def _open_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, port))
    sock.settimeout(SOCKET_TIMEOUT)
    return sock


def _recv_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


class TestInMemoryIPTable:
    def test_try_acquire_and_release(self):
        table = InMemoryIPTable(max_connections_per_ip=2)
        ip = "127.0.0.1"

        assert table.try_acquire_connection(ip) is True
        assert table.try_acquire_connection(ip) is True
        assert table.try_acquire_connection(ip) is False
        assert table.get_active_connections(ip) == 2

        table.release_connection(ip)
        assert table.get_active_connections(ip) == 1
        table.release_connection(ip)
        assert table.get_active_connections(ip) == 0

    def test_debug_logging_enabled(self, caplog):
        caplog.set_level(logging.INFO, logger="src.server.ip_table")
        table = InMemoryIPTable(max_connections_per_ip=1, debug_enabled=True)
        ip = "127.0.0.1"

        assert table.try_acquire_connection(ip) is True
        assert table.try_acquire_connection(ip) is False
        table.release_connection(ip)

        messages = [rec.getMessage() for rec in caplog.records]
        assert any("event=acquire" in msg for msg in messages)
        assert any("event=deny" in msg for msg in messages)
        assert any("event=release" in msg for msg in messages)


class TestPerIPConnectionLimit:
    def test_limit_20_connections_per_ip(self, server_process, server_port):
        sockets = []
        blocked_socket = None
        retry_socket = None

        try:
            for _ in range(PER_IP_LIMIT):
                sockets.append(_open_socket(server_port))

            # accept後のハンドラ起動を少し待ってから21本目を試す
            time.sleep(0.2)

            blocked_socket = _open_socket(server_port)
            blocked_socket.sendall(
                b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            blocked_response = _recv_headers(blocked_socket)
            assert b"HTTP/1.1 429" in blocked_response

            sockets.pop().close()
            time.sleep(0.3)

            retry_socket = _open_socket(server_port)
            retry_socket.sendall(b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
            retry_response = _recv_headers(retry_socket)
            assert b"HTTP/1.1 200" in retry_response
        finally:
            if blocked_socket is not None:
                blocked_socket.close()
            if retry_socket is not None:
                retry_socket.close()
            for sock in sockets:
                sock.close()


class TestBanList:
    def test_ban_list_supports_ip_and_cidr(self, tmp_path):
        ban_file = tmp_path / "ban-list.txt"
        ban_file.write_text(
            "\n".join(
                [
                    "# single ip",
                    "127.0.0.1",
                    "",
                    "# cidr",
                    "10.10.0.0/24",
                    "# unsupported range syntax",
                    "192.168.0.1-192.168.0.20",
                ]
            ),
            encoding="utf-8",
        )

        table = InMemoryIPTable(
            max_connections_per_ip=20,
            ban_list_file=str(ban_file),
        )

        assert table.is_banned("127.0.0.1") is True
        assert table.is_banned("10.10.0.99") is True
        assert table.is_banned("10.10.1.1") is False
        assert table.is_banned("192.168.0.3") is False

    def test_global_config_reads_ban_list_file(self):
        cfg = GlobalConfig.from_dict({"ban_list_file": "/tmp/ban-list.txt"})
        assert cfg.ban_list_file == "/tmp/ban-list.txt"

    def test_global_config_reads_nested_ban_list_file(self):
        cfg = GlobalConfig.from_dict(
            {"global": {"ban_list_file": "/tmp/ban-list-nested.txt"}}
        )
        assert cfg.ban_list_file == "/tmp/ban-list-nested.txt"
