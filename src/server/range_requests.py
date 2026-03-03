import secrets
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Optional

from .etag_utils import strong_etag_equal


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class ParsedRangeRequest:
    unit_supported: bool
    is_valid: bool
    ranges: list[ByteRange]


def parse_range_header(range_header: str, resource_size: int) -> ParsedRangeRequest:
    raw = (range_header or "").strip()
    if "=" not in raw:
        return ParsedRangeRequest(unit_supported=True, is_valid=False, ranges=[])

    unit, raw_ranges = raw.split("=", 1)
    if unit.strip().lower() != "bytes":
        # bytes以外のunitは無視対象（200で全体返却）
        return ParsedRangeRequest(unit_supported=False, is_valid=False, ranges=[])

    range_specs = [spec.strip() for spec in raw_ranges.split(",")]
    if not range_specs or any(spec == "" for spec in range_specs):
        return ParsedRangeRequest(unit_supported=True, is_valid=False, ranges=[])

    ranges: list[ByteRange] = []
    for spec in range_specs:
        parsed = _parse_single_range(spec, resource_size)
        if parsed is None:
            return ParsedRangeRequest(unit_supported=True, is_valid=False, ranges=[])
        ranges.append(parsed)

    return ParsedRangeRequest(unit_supported=True, is_valid=True, ranges=ranges)


def _parse_single_range(spec: str, resource_size: int) -> Optional[ByteRange]:
    if "-" not in spec:
        return None

    first_raw, last_raw = spec.split("-", 1)
    first_raw = first_raw.strip()
    last_raw = last_raw.strip()

    if resource_size <= 0:
        return None

    # suffix-byte-range-spec: "-500"
    if first_raw == "":
        if last_raw == "":
            return None
        if not last_raw.isdigit():
            return None
        suffix_length = int(last_raw)
        if suffix_length <= 0:
            return None

        if suffix_length >= resource_size:
            return ByteRange(0, resource_size - 1)
        return ByteRange(resource_size - suffix_length, resource_size - 1)

    # byte-range-spec: "0-499", "500-"
    if not first_raw.isdigit():
        return None
    start = int(first_raw)

    if start >= resource_size:
        return None

    if last_raw == "":
        return ByteRange(start, resource_size - 1)

    if not last_raw.isdigit():
        return None

    end = int(last_raw)
    if end < start:
        return None

    if end >= resource_size:
        end = resource_size - 1
    return ByteRange(start, end)


def format_content_range(rng: ByteRange, resource_size: int, unit: str = "bytes") -> str:
    return f"{unit} {rng.start}-{rng.end}/{resource_size}"


def format_unsatisfied_content_range(resource_size: int, unit: str = "bytes") -> str:
    return f"{unit} */{resource_size}"


def build_multipart_byteranges_body(
    content: bytes,
    ranges: list[ByteRange],
    content_type: str,
    resource_size: int,
    boundary: Optional[str] = None,
) -> tuple[str, bytes]:
    safe_boundary = boundary or f"myhttpserver-{secrets.token_hex(12)}"
    chunks: list[bytes] = []

    for rng in ranges:
        chunks.append(f"--{safe_boundary}\r\n".encode("ascii"))
        chunks.append(f"Content-Type: {content_type}\r\n".encode("ascii"))
        chunks.append(
            f"Content-Range: {format_content_range(rng, resource_size)}\r\n\r\n".encode(
                "ascii"
            )
        )
        chunks.append(content[rng.start : rng.end + 1])
        chunks.append(b"\r\n")

    chunks.append(f"--{safe_boundary}--\r\n".encode("ascii"))

    return (
        f"multipart/byteranges; boundary={safe_boundary}",
        b"".join(chunks),
    )


def should_apply_range_for_if_range(
    if_range_header: str,
    current_etag: Optional[str],
    last_modified_header: str,
) -> bool:
    raw = (if_range_header or "").strip()
    if not raw:
        return True

    if current_etag and strong_etag_equal(raw, current_etag):
        return True

    if_range_dt = _parse_http_date(raw)
    last_modified_dt = _parse_http_date(last_modified_header)
    if if_range_dt is None or last_modified_dt is None:
        return False

    # If-Range が Last-Modified 以上なら「変更なし」と見なして Range 適用
    return last_modified_dt <= if_range_dt


def _parse_http_date(value: str):
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
