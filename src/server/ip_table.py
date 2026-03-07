import ipaddress
import logging
import threading
from collections.abc import MutableMapping
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class InMemoryIPTable:
    """In-memory table for per-IP state (connection limits today, rate/BAN later)."""

    def __init__(
        self,
        max_connections_per_ip: int = 20,
        active_connections: MutableMapping[str, int] | None = None,
        lock: Any | None = None,
        ban_list_file: str | None = None,
        debug_enabled: bool = False,
    ) -> None:
        self.max_connections_per_ip = max(1, int(max_connections_per_ip))
        self._active_connections = (
            active_connections if active_connections is not None else {}
        )
        self._lock = lock if lock is not None else threading.Lock()
        self._debug_enabled = debug_enabled
        self._banned_exact_ips, self._banned_networks = _load_ban_list_file(
            ban_list_file
        )
        if self._banned_exact_ips or self._banned_networks:
            logger.info(
                "Loaded ban list: exact_ips=%d cidrs=%d source=%s",
                len(self._banned_exact_ips),
                len(self._banned_networks),
                ban_list_file,
            )

    def is_banned(self, ip: str) -> bool:
        if not self._banned_exact_ips and not self._banned_networks:
            return False

        if ip in self._banned_exact_ips:
            self._log_ban_hit(ip)
            return True

        blocked = self._is_banned_by_network_cached(ip)
        if blocked:
            self._log_ban_hit(ip)
        return blocked

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

    def _log_ban_hit(self, ip: str) -> None:
        if not self._debug_enabled:
            return
        logger.info("ip_table event=ban_hit ip=%s", ip)

    @lru_cache(maxsize=8192)
    def _is_banned_by_network_cached(self, ip: str) -> bool:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return False

        for network in self._banned_networks:
            if parsed in network:
                return True
        return False


def _load_ban_list_file(
    ban_list_file: str | None,
) -> tuple[set[str], tuple[Any, ...]]:
    if not ban_list_file:
        return set(), tuple()

    path = Path(ban_list_file)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logger.warning("ban_list_file was not found: %s", path)
        return set(), tuple()
    except OSError as e:
        logger.warning("Failed to read ban_list_file=%s: %s", path, e)
        return set(), tuple()

    banned_exact_ips: set[str] = set()
    banned_networks: list[Any] = []
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        if "-" in line:
            logger.warning(
                "Unsupported ban list entry ignored (range syntax): %s:%d %s",
                path,
                line_no,
                line,
            )
            continue

        try:
            if "/" in line:
                banned_networks.append(ipaddress.ip_network(line, strict=False))
            else:
                banned_exact_ips.add(str(ipaddress.ip_address(line)))
        except ValueError:
            logger.warning(
                "Invalid ban list entry ignored: %s:%d %s",
                path,
                line_no,
                line,
            )

    return banned_exact_ips, tuple(banned_networks)
