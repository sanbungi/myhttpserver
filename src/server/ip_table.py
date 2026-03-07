import threading
from collections.abc import MutableMapping
from typing import Any


class InMemoryIPTable:
    """In-memory table for per-IP state (connection limits today, rate/BAN later)."""

    def __init__(
        self,
        max_connections_per_ip: int = 20,
        active_connections: MutableMapping[str, int] | None = None,
        lock: Any | None = None,
    ) -> None:
        self.max_connections_per_ip = max(1, int(max_connections_per_ip))
        self._active_connections = (
            active_connections if active_connections is not None else {}
        )
        self._lock = lock if lock is not None else threading.Lock()

    def try_acquire_connection(self, ip: str) -> bool:
        with self._lock:
            current = int(self._active_connections.get(ip, 0))
            if current >= self.max_connections_per_ip:
                return False

            self._active_connections[ip] = current + 1
            return True

    def release_connection(self, ip: str) -> None:
        with self._lock:
            current = int(self._active_connections.get(ip, 0))
            if current <= 1:
                self._active_connections.pop(ip, None)
                return
            self._active_connections[ip] = current - 1

    def get_active_connections(self, ip: str) -> int:
        with self._lock:
            return int(self._active_connections.get(ip, 0))
