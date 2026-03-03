from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EntityTag:
    opaque: str
    weak: bool


def parse_entity_tag(value: str) -> Optional[EntityTag]:
    raw = (value or "").strip()
    if not raw:
        return None

    weak = False
    if raw[:2].lower() == "w/":
        weak = True
        raw = raw[2:].lstrip()

    # 内部表現では unquoted な値も使うため、そのまま受け入れる。
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        opaque = raw[1:-1]
    elif '"' in raw:
        return None
    else:
        opaque = raw

    return EntityTag(opaque=opaque, weak=weak)


def weak_etag_equal(left: str, right: str) -> bool:
    left_tag = parse_entity_tag(left)
    right_tag = parse_entity_tag(right)
    if left_tag is None or right_tag is None:
        return False
    return left_tag.opaque == right_tag.opaque


def strong_etag_equal(left: str, right: str) -> bool:
    left_tag = parse_entity_tag(left)
    right_tag = parse_entity_tag(right)
    if left_tag is None or right_tag is None:
        return False
    if left_tag.weak or right_tag.weak:
        return False
    return left_tag.opaque == right_tag.opaque
