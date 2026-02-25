from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
    output: str = "stdout"
    format: str = "text"

    @classmethod
    def from_dict(cls, data: Dict) -> "LoggingConfig":
        return cls(**data) if data else cls()


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
    index: List[str] = field(default_factory=list)
    headers: Optional[HeadersConfig] = None
    backend: Optional[BackendConfig] = None
    respond: Optional[RespondConfig] = None
    security: Optional[SecurityConfig] = None
    redirect: Optional[RedirectConfig] = None

    @classmethod
    def from_dict(cls, path: str, data: Dict) -> "RouteConfig":
        return cls(
            path=path,
            type=data.get("type", "static"),
            index=data.get("index", []),
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
    tls: TlsConfig = field(default_factory=TlsConfig)
    headers: Optional[HeadersConfig] = None
    routes: List[RouteConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: Dict) -> "ServerConfig":
        routes = []
        raw_routes = data.get("route", [])

        # pyhclの route は辞書のリストになっている
        for route_entry in raw_routes:
            for path, route_data in route_entry.items():
                routes.append(RouteConfig.from_dict(path, route_data))

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

        return cls(global_settings=g_config, servers=servers)
