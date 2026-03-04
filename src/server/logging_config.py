import logging
import os
from dataclasses import asdict, is_dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pprint import pformat
from textwrap import indent

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s"
_CONSOLE_COLOR_FORMAT = (
    "%(asctime)s [%(levelname_color)s] %(name)s:%(lineno)d %(message)s"
)
_RESET = "\x1b[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\x1b[36m",  # cyan
    logging.INFO: "\x1b[32m",  # green
    logging.WARNING: "\x1b[33m",  # yellow
    logging.ERROR: "\x1b[31m",  # red
    logging.CRITICAL: "\x1b[35;1m",  # bold magenta
}


def pretty_log(value: object) -> str:
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            value = vars(value)
        except TypeError:
            pass

    if isinstance(value, (dict, list, tuple, set)):
        return pformat(value, width=100, compact=False, sort_dicts=False)
    return str(value)


def pretty_block(value: object, prefix: str = "  ") -> str:
    return "\n" + indent(pretty_log(value), prefix)


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

    ch = logging.StreamHandler()
    ch.setLevel(level)
    use_color = hasattr(ch.stream, "isatty") and ch.stream.isatty()
    if os.getenv("NO_COLOR"):
        use_color = False

    if use_color:
        def _inject_level_color(record: logging.LogRecord) -> bool:
            color = _LEVEL_COLORS.get(record.levelno, "")
            if color:
                record.levelname_color = f"{color}{record.levelname}{_RESET}"
            else:
                record.levelname_color = record.levelname
            return True

        ch.addFilter(_inject_level_color)
        ch.setFormatter(logging.Formatter(_CONSOLE_COLOR_FORMAT))
    else:
        ch.setFormatter(logging.Formatter(fmt))

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
    fh.setFormatter(logging.Formatter(fmt))

    root.addHandler(ch)
    root.addHandler(fh)

    # うるさい外部ライブラリを落としたいとき（任意）
    # logging.getLogger("urllib3").setLevel(logging.WARNING)
    # logging.getLogger("asyncio").setLevel(logging.WARNING)
