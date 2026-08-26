from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class ModelSlot:
    r: str
    h: int


@dataclass(frozen=True)
class ParsedModelOutput:
    status: str
    slots: tuple[ModelSlot, ...] = ()
    error: str | None = None

    def json_items(self) -> list[dict]:
        return [asdict(slot) for slot in self.slots]


def _payload(raw: str) -> str:
    value = (raw or "").strip()
    value = re.sub(r"^<think>[\s\S]*?</think>\s*", "", value, count=1)
    value = re.sub(r"^```(?:json)?\s*", "", value, count=1, flags=re.I)
    value = re.sub(r"\s*```$", "", value, count=1)
    start, end = value.find("["), value.rfind("]")
    return value[start : end + 1] if start >= 0 and end >= start else value


def parse_model_slots(raw: str) -> ParsedModelOutput:
    if not isinstance(raw, str) or not raw.strip():
        return ParsedModelOutput("empty", error="empty model output")
    try:
        value = json.loads(_payload(raw))
    except (TypeError, json.JSONDecodeError) as exc:
        return ParsedModelOutput("invalid_json", error=str(exc)[:240])
    if not isinstance(value, list) or len(value) != 5:
        return ParsedModelOutput("invalid_shape", error="expected exactly five items")
    slots = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict) or not isinstance(item.get("r"), str):
            return ParsedModelOutput("invalid_item", error=f"slot {index}: invalid r")
        h = item.get("h")
        if type(h) is not int or h not in (0, 1):
            return ParsedModelOutput("invalid_item", error=f"slot {index}: invalid h")
        slots.append(ModelSlot(item["r"], h))
    return ParsedModelOutput("ok", tuple(slots))


def safe_image_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty image reference")
    parsed = urlsplit(value.strip())
    if parsed.query or parsed.fragment:
        raise ValueError("image reference contains query or fragment")
    if parsed.scheme not in ("http", "https", ""):
        raise ValueError("unsupported image reference scheme")
    if parsed.scheme and not parsed.netloc:
        raise ValueError("invalid image reference")
    return urlunsplit(parsed)
