import logging
import os
from dataclasses import asdict, is_dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pprint import pformat
from textwrap import indent

# HACK 固定値
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s"
_CONSOLE_COLOR_FORMAT = (
    "%(asctime)s [%(levelname_color)s] %(name)s:%(lineno)d %(message)s"
)
_ACCESS_LOGGER_NAME = "access"
_ACTIVE_ACCESS_LOGGER_NAME = _ACCESS_LOGGER_NAME
_ACCESS_DATEFMT = "%d/%b/%Y:%H:%M:%S %z"
_ACCESS_FORMAT = (
    '%(remote_addr)s - - [%(asctime)s] "%(method)s %(url)s %(http_version)s" '
    '%(status_code)s %(response_size)s "%(user_agent)s"'
)
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 5
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


def _sanitize_access_field(value: object, default: str = "-") -> str:
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    return s.replace("\r", " ").replace("\n", " ").replace('"', '\\"')


def _to_int(value: object, default: int, min_value: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < min_value:
        return default
    return parsed


def _ensure_access_logger(
    *,
    app_name: str,
    log_dir: str,
    access_logger_name: str,
    access_log_file: str | None,
    access_format: str,
    access_datefmt: str,
    max_bytes: int,
    backup_count: int,
) -> None:
    global _ACTIVE_ACCESS_LOGGER_NAME
    _ACTIVE_ACCESS_LOGGER_NAME = access_logger_name or _ACCESS_LOGGER_NAME
    access_logger = logging.getLogger(_ACTIVE_ACCESS_LOGGER_NAME)
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False

    for h in access_logger.handlers:
        if getattr(h, "_myhttp_access_handler", False):
            return

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    if not access_log_file:
        access_log_file = f"{app_name}.access.log"
    access_logfile_path = str(Path(log_dir) / access_log_file)

    # HACK 固定値
    ah = RotatingFileHandler(
        access_logfile_path,
        maxBytes=_to_int(max_bytes, _DEFAULT_MAX_BYTES, min_value=1),
        backupCount=_to_int(backup_count, _DEFAULT_BACKUP_COUNT, min_value=0),
        encoding="utf-8",
    )
    ah.setLevel(logging.INFO)
    ah.setFormatter(logging.Formatter(access_format, datefmt=access_datefmt))
    ah._myhttp_access_handler = True
    access_logger.addHandler(ah)


def log_access(
    *,
    remote_addr: str,
    url: str,
    status_code: int,
    response_size: int,
    user_agent: str,
    method: str = "-",
    http_version: str = "-",
    access_logger_name: str | None = None,
) -> None:
    logger_name = access_logger_name or _ACTIVE_ACCESS_LOGGER_NAME
    access_logger = logging.getLogger(logger_name)
    if not access_logger.handlers:
        return

    safe_status = _to_int(status_code, 0, min_value=0)
    safe_size = _to_int(response_size, 0, min_value=0)

    access_logger.info(
        "",
        extra={
            "remote_addr": _sanitize_access_field(remote_addr),
            "method": _sanitize_access_field(method),
            "url": _sanitize_access_field(url),
            "http_version": _sanitize_access_field(http_version),
            "status_code": safe_status,
            "response_size": safe_size,
            "user_agent": _sanitize_access_field(user_agent),
        },
    )


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
    access_log_file: str | None = None,
    access_logger_name: str = _ACCESS_LOGGER_NAME,
    access_format: str = _ACCESS_FORMAT,
    access_datefmt: str = _ACCESS_DATEFMT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
    level_name: str | None = None,
    fmt: str = _DEFAULT_FORMAT,
) -> None:

    configured_level = level_name if level_name else "INFO"
    env_level = os.getenv("LOG_LEVEL")
    level = _parse_level(env_level or configured_level, default=logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # すでにハンドラが付いているなら、二重追加を防いで終了
    if root.handlers:
        # レベルだけ更新したい場合に備えて、ここで反映しておく
        for h in root.handlers:
            h.setLevel(level)
        _ensure_access_logger(
            app_name=app_name,
            log_dir=log_dir,
            access_logger_name=access_logger_name,
            access_log_file=access_log_file,
            access_format=access_format,
            access_datefmt=access_datefmt,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
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
        log_file = f"{app_name}.error.log"
    logfile_path = str(Path(log_dir) / log_file)

    # HACK 固定値
    fh = RotatingFileHandler(
        logfile_path,
        maxBytes=_to_int(max_bytes, _DEFAULT_MAX_BYTES, min_value=1),
        backupCount=_to_int(backup_count, _DEFAULT_BACKUP_COUNT, min_value=0),
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt))

    root.addHandler(ch)
    root.addHandler(fh)
    _ensure_access_logger(
        app_name=app_name,
        log_dir=log_dir,
        access_logger_name=access_logger_name,
        access_log_file=access_log_file,
        access_format=access_format,
        access_datefmt=access_datefmt,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
