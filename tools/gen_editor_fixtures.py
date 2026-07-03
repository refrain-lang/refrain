#!/usr/bin/env python3
"""Emit the refrain-editor golden parity corpus from the reference library.

For every in-subset protocol: model = describe_protocol(src).model;
expected = render_protocol(model). Writes <name>.model.json + <name>.refrain
to the output dir. The TS package vendors these; its parity test reproduces
each <name>.refrain byte-exact from <name>.model.json.

Recurses into protocols/ subfolders so device-specific sets (e.g.
protocols/brainbit/) are included.

Run: python tools/gen_editor_fixtures.py <refrain-protocols-dir> <out-dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from refrain.editor import describe_protocol, render_protocol


def main() -> None:
    corpus = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(corpus.glob("protocols/**/*.refrain")) + sorted(corpus.glob("drafts/**/*.refrain"))
    written = 0
    for f in files:
        d = describe_protocol(f.read_text())
        if not d["ok"] or not d["in_subset"]:
            continue
        model = d["model"]
        # NO sort_keys: preserve insertion order so meta (iterated in order by
        # both renderers) round-trips. JS JSON.parse preserves textual key order.
        (out / f"{f.stem}.model.json").write_text(json.dumps(model, indent=2) + "\n")
        (out / f"{f.stem}.refrain").write_text(render_protocol(model))
        (out / f"{f.stem}.src.refrain").write_text(f.read_text())  # verbatim hand-written original
        written += 1
    print(f"wrote {written} fixture pairs to {out}")


if __name__ == "__main__":
    main()
