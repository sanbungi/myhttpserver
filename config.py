import tomllib
from dataclasses import dataclass
from typing import List


@dataclass
class SSLConfig:
    cert_file: str
    key_file: str


@dataclass
class ServerConfig:
    http_port: int
    https_port: int
    use_ssl: bool
    also_http: bool
    keep_alive_timeout: int
    max_workers: int
    request_bytes: int


@dataclass
class LoggingConfig:
    dir: str
    system_log: str
    access_log: str
    max_bytes: int
    backup_count: int
    system_level: str
    access_level: str


@dataclass
class CompressionConfig:
    priority: List[str]


@dataclass
class Config:
    server: ServerConfig
    ssl: SSLConfig
    logging: LoggingConfig
    compression: CompressionConfig


def load_config(config_path: str = "config.toml") -> Config:
    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    return Config(
        server=ServerConfig(**data["server"]),
        ssl=SSLConfig(**data["ssl"]),
        logging=LoggingConfig(**data["logging"]),
        compression=CompressionConfig(**data["compression"]),
    )
