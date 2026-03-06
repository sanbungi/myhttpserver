import time

# 定数テーブルによる高速化
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MO = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# キャッシュ
_last_sec = -1
_last_str = ""


def _build_http_date(sec: int) -> str:
    """RFC 1123 format builder (GMT)."""
    t = time.gmtime(sec)
    return (
        f"{_WD[t.tm_wday]}, {t.tm_mday:02d} {_MO[t.tm_mon - 1]} "
        f"{t.tm_year:04d} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} GMT"
    )


def http_date_now() -> str:
    """
    Return current HTTP-date string (cached per second).

    Extremely fast for high-frequency calls.
    """
    global _last_sec, _last_str

    sec = int(time.time())
    if sec != _last_sec:
        _last_sec = sec
        _last_str = _build_http_date(sec)

    return _last_str
