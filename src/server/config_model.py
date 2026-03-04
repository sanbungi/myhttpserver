from dataclasses import dataclass, field
from typing import Dict, List, Optional


SUPPORTED_COMPRESSION_METHODS = ("gzip", "zstd")
DEFAULT_COMPRESSION_METHODS = ["gzip", "zstd"]


def normalize_compression_methods(methods: Optional[List[str]]) -> List[str]:
    if not methods:
        return list(DEFAULT_COMPRESSION_METHODS)

    normalized: List[str] = []
    for method in methods:
        if not isinstance(method, str):
            continue
        candidate = method.strip().lower()
        if candidate in SUPPORTED_COMPRESSION_METHODS and candidate not in normalized:
            normalized.append(candidate)

    return normalized or list(DEFAULT_COMPRESSION_METHODS)


def normalize_route_path(path: str) -> str:
    if not path:
        return "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/":
        path = path.rstrip("/")
    return path or "/"


@dataclass
class HeadersConfig:
    add: Dict[str, str] = field(default_factory=dict)  # set or add
    remove: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict) -> "HeadersConfig":
        if not data:
            return cls()
        # HCLの記述ゆれ吸収 (set だったり add だったりする場合に対応)
        add_headers = data.get("add", {}) or data.get("set", {})
        return cls(add=add_headers, remove=data.get("remove", []))


@dataclass
class LoggingConfig:
    level: str = "info"
    app_name: str = "myhttpserver"
    log_dir: str = "logs"
    error_log_file: Optional[str] = None
    access_log_file: Optional[str] = None
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5
    access_format: str = (
        '%(remote_addr)s - - [%(asctime)s] "%(method)s %(url)s %(http_version)s" '
        '%(status_code)s %(response_size)s "%(user_agent)s"'
    )
    access_datefmt: str = "%d/%b/%Y:%H:%M:%S %z"
    access_logger_name: str = "access"
    output: str = "stdout"  # backward-compat
    format: str = "text"  # backward-compat

    @staticmethod
    def _to_int(value, default: int, min_value: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < min_value:
            return default
        return parsed

    @classmethod
    def from_dict(cls, data: Dict) -> "LoggingConfig":
        if not data:
            return cls()
        return cls(
            level=str(data.get("level", "info")),
            app_name=str(data.get("app_name", "myhttpserver")),
            log_dir=str(data.get("log_dir", "logs")),
            error_log_file=data.get("error_log_file"),
            access_log_file=data.get("access_log_file"),
            max_bytes=cls._to_int(data.get("max_bytes"), 5 * 1024 * 1024, min_value=1),
            backup_count=cls._to_int(data.get("backup_count"), 5, min_value=0),
            access_format=str(
                data.get(
                    "access_format",
                    '%(remote_addr)s - - [%(asctime)s] "%(method)s %(url)s %(http_version)s" '
                    '%(status_code)s %(response_size)s "%(user_agent)s"',
                )
            ),
            access_datefmt=str(data.get("access_datefmt", "%d/%b/%Y:%H:%M:%S %z")),
            access_logger_name=str(data.get("access_logger_name", "access")),
            output=str(data.get("output", "stdout")),
            format=str(data.get("format", "text")),
        )


@dataclass
class TlsConfig:
    enabled: bool = False
    cert: Optional[str] = None
    key: Optional[str] = None
    min_version: str = "TLS1.2"

    @classmethod
    def from_dict(cls, data: Dict) -> "TlsConfig":
        return cls(**data) if data else cls()


@dataclass
class BackendConfig:
    upstream: str
    timeout: str = "30s"
    headers: Optional[HeadersConfig] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "BackendConfig":
        if not data:
            return None
        return cls(
            upstream=data.get("upstream", ""),
            timeout=data.get("timeout", "30s"),
            headers=HeadersConfig.from_dict(data.get("headers", {})),
        )


@dataclass
class RespondConfig:
    status: int
    body: str

    @classmethod
    def from_dict(cls, data: Dict) -> "RespondConfig":
        if not data:
            return None
        return cls(**data)


@dataclass
class SecurityConfig:
    deny_all: bool = False
    ip_allow: List[str] = field(default_factory=list)
    # HACK Basic認証なども追加する

    @classmethod
    def from_dict(cls, data: Dict) -> "SecurityConfig":
        if not data:
            return None
        return cls(**data)


@dataclass
class RedirectConfig:
    url: str
    code: int

    @classmethod
    def from_dict(cls, data: Dict) -> "RedirectConfig":
        if not data:
            return None
        return cls(**data)


@dataclass
class RouteConfig:
    path: str
    type: str
    methods: Optional[List[str]] = None
    index: List[str] = field(default_factory=list)
    headers: Optional[HeadersConfig] = None
    backend: Optional[BackendConfig] = None
    respond: Optional[RespondConfig] = None
    security: Optional[SecurityConfig] = None
    redirect: Optional[RedirectConfig] = None

    @classmethod
    def from_dict(cls, path: str, data: Dict) -> "RouteConfig":
        return cls(
            path=normalize_route_path(path),
            type=data.get("type", "static"),
            index=data.get("index", []),
            methods=data.get("methods", {}),
            headers=HeadersConfig.from_dict(data.get("headers", {})),
            backend=BackendConfig.from_dict(data.get("backend", {})),
            respond=RespondConfig.from_dict(data.get("respond", {})),
            security=SecurityConfig.from_dict(data.get("security", {})),
            redirect=RedirectConfig.from_dict(data.get("redirect", {})),
        )


# ==========================================
# Server & Global
# ==========================================


@dataclass
class ServerConfig:
    name: str
    host: str
    port: int
    root: Optional[str] = None
    compression_methods: List[str] = field(
        default_factory=lambda: list(DEFAULT_COMPRESSION_METHODS)
    )
    tls: TlsConfig = field(default_factory=TlsConfig)
    headers: Optional[HeadersConfig] = None
    routes: List[RouteConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: Dict) -> "ServerConfig":
        routes = []
        raw_routes = data.get("route", [])

        if isinstance(raw_routes, dict):
            raw_routes = [raw_routes]

        # pyhclの route は辞書のリストになっている
        for route_entry in raw_routes:
            for path, route_data in route_entry.items():
                routes.append(RouteConfig.from_dict(path, route_data))
        routes.sort(key=lambda r: len(r.path), reverse=True)

        return cls(
            name=name,
            host=data.get("host", "localhost"),
            port=data.get("port", 80),
            root=data.get("root"),
            tls=TlsConfig.from_dict(data.get("tls", {})),
            headers=HeadersConfig.from_dict(data.get("headers", {})),
            routes=routes,
        )


@dataclass
class GlobalConfig:
    worker_processes: int = 1
    max_connections: int = 1024
    timeout: str = "30s"
    timeout_keepalive: str = "65s"
    compression_methods: List[str] = field(
        default_factory=lambda: list(DEFAULT_COMPRESSION_METHODS)
    )
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_dict(cls, data: Dict) -> "GlobalConfig":
        if not data:
            return cls()
        return cls(
            worker_processes=data.get("worker_processes", 1),
            max_connections=data.get("max_connections", 1024),
            timeout=data.get("timeout", "30s"),
            timeout_keepalive=data.get("timeout_keepalive", "65s"),
            compression_methods=normalize_compression_methods(
                data.get("compression_methods")
            ),
            logging=LoggingConfig.from_dict(data.get("logging", {})),
        )


@dataclass
class AppConfig:
    global_settings: GlobalConfig
    servers: List[ServerConfig]

    @classmethod
    def load(cls, raw_hcl: Dict) -> "AppConfig":
        g_config = GlobalConfig.from_dict(raw_hcl.get("global", {}))

        # Serversセクションの読み込み
        servers = []
        raw_servers = raw_hcl.get("server", {})
        for name, server_data in raw_servers.items():
            servers.append(ServerConfig.from_dict(name, server_data))
        for server in servers:
            server.compression_methods = list(g_config.compression_methods)

        return cls(global_settings=g_config, servers=servers)
