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
