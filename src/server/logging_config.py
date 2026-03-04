import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s"


def _parse_level(level_str: str, default: int = logging.INFO) -> int:
    if not level_str:
        return default
    s = level_str.strip().upper()
    return getattr(logging, s, default)


def setup_logging(
    *,
    app_name: str = "app",
    log_dir: str = "logs",
    log_file: str | None = None,
    fmt: str = _DEFAULT_FORMAT,
) -> None:

    level = _parse_level(os.getenv("LOG_LEVEL"), default=logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # すでにハンドラが付いているなら、二重追加を防いで終了
    if root.handlers:
        # レベルだけ更新したい場合に備えて、ここで反映しておく
        for h in root.handlers:
            h.setLevel(level)
        return

    formatter = logging.Formatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    if log_file is None:
        log_file = f"{app_name}.log"
    logfile_path = str(Path(log_dir) / log_file)

    fh = RotatingFileHandler(
        logfile_path,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(formatter)

    root.addHandler(ch)
    root.addHandler(fh)

    # うるさい外部ライブラリを落としたいとき（任意）
    # logging.getLogger("urllib3").setLevel(logging.WARNING)
    # logging.getLogger("asyncio").setLevel(logging.WARNING)
