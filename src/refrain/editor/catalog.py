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


_MODEL_SCHEMA_PATH = Path(__file__).with_name("protocol-model.schema.json")


def validate_model(model: dict) -> None:
    """Raise jsonschema.ValidationError if `model` is not a valid ProtocolModel."""
    import jsonschema  # lazy: optional dependency
    jsonschema.validate(model, json.loads(_MODEL_SCHEMA_PATH.read_text()))
