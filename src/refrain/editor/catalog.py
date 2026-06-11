from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CATALOG_PATH = Path(__file__).with_name("catalog.json")


@dataclass(frozen=True)
class Catalog:
    version: str
    _by_id: dict[str, dict]

    def block(self, block_id: str) -> dict:
        return self._by_id[block_id]

    def has(self, block_id: str) -> bool:
        return block_id in self._by_id


def load_catalog(path: Path | None = None) -> Catalog:
    data = json.loads((path or _CATALOG_PATH).read_text())
    return Catalog(version=data["catalog_version"], _by_id={b["id"]: b for b in data["blocks"]})


def _fmt_num(v) -> str:
    """Format a number so it re-parses to the same value (no precision loss).

    Integers (and integral floats) render without a decimal point; other
    floats use `repr`, the shortest string that round-trips exactly.
    """
    f = float(v)
    if f.is_integer():
        return str(int(f))
    return repr(f)


def _fmt_duration(ms: float) -> str:
    """Format a millisecond value with the most natural unit (min > s > ms)."""
    if ms >= 60_000 and ms % 60_000 == 0:
        return f"{_fmt_num(ms / 60_000)} min"
    if ms >= 1_000 and ms % 1_000 == 0:
        return f"{_fmt_num(ms / 1_000)} s"
    return f"{_fmt_num(ms)} ms"


def render_slot(value, slot_type: str) -> str:
    """Format one slot value for insertion into a .refrain template."""
    if isinstance(value, dict) and "bind" in value:
        return value["bind"]                       # control binding -> bare name
    if slot_type == "frequency":
        return f"{_fmt_num(value)} Hz"
    if slot_type in ("number", "percent", "duration_ms"):
        return _fmt_num(value)
    if slot_type == "duration":
        return _fmt_duration(value)
    if slot_type == "site":
        return f'"{value}"'
    if slot_type in ("ref", "enum", "raw"):
        return str(value)
    if slot_type == "voltage":
        return f"{_fmt_num(value)} uV"
    raise ValueError(f"unknown slot type {slot_type!r}")


_MODEL_SCHEMA_PATH = Path(__file__).with_name("protocol-model.schema.json")


def validate_model(model: dict) -> None:
    """Raise jsonschema.ValidationError if `model` is not a valid ProtocolModel."""
    import jsonschema  # lazy: optional dependency
    jsonschema.validate(model, json.loads(_MODEL_SCHEMA_PATH.read_text()))
