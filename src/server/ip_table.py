import logging
import threading
from collections.abc import MutableMapping
from typing import Any

logger = logging.getLogger(__name__)


class InMemoryIPTable:
    """In-memory table for per-IP state (connection limits today, rate/BAN later)."""

    def __init__(
        self,
        max_connections_per_ip: int = 20,
        active_connections: MutableMapping[str, int] | None = None,
        lock: Any | None = None,
        debug_enabled: bool = False,
    ) -> None:
        self.max_connections_per_ip = max(1, int(max_connections_per_ip))
        self._active_connections = (
            active_connections if active_connections is not None else {}
        )
        self._lock = lock if lock is not None else threading.Lock()
        self._debug_enabled = debug_enabled

    def try_acquire_connection(self, ip: str) -> bool:
        with self._lock:
            current = int(self._active_connections.get(ip, 0))
            if current >= self.max_connections_per_ip:
                self._log(
                    "deny",
                    ip,
                    current,
                )
                return False

            new_count = current + 1
            self._active_connections[ip] = new_count
            self._log("acquire", ip, new_count)
            return True

    def release_connection(self, ip: str) -> None:
        with self._lock:
            current = int(self._active_connections.get(ip, 0))
            if current <= 1:
                self._active_connections.pop(ip, None)
                self._log("release", ip, 0)
                return
            new_count = current - 1
            self._active_connections[ip] = new_count
            self._log("release", ip, new_count)

    def get_active_connections(self, ip: str) -> int:
        with self._lock:
            return int(self._active_connections.get(ip, 0))

    def _log(self, event: str, ip: str, active: int) -> None:
        if not self._debug_enabled:
            return
        logger.info(
            "ip_table event=%s ip=%s active=%d limit=%d",
            event,
            ip,
            active,
            self.max_connections_per_ip,
        )
