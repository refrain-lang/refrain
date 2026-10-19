#!/usr/bin/env python3
"""Emit the refrain-editor golden parity corpus from the reference library.

For every in-subset protocol: model = describe_protocol(src).model;
expected = render_protocol(model). Writes <name>.model.json + <name>.refrain
to the output dir. The TS package vendors these; its parity test reproduces
each <name>.refrain byte-exact from <name>.model.json.

Recurses into protocols/ subfolders so device-specific sets (e.g.
protocols/brainbit/) are included.

**Most protocols need an amp profile to resolve at all.** `amp.reference` is
an error without one, so describing with `amp=None` drops every protocol in
the amp-portable library — 36 of 46, at the time of writing. Each protocol is
therefore tried against no amp first (the HRV protocols need a tachogram
channel no EEG profile has), then against each built-in profile until one
resolves. The amp only decides *whether* a protocol resolves: the model it
produces is identical under every profile that resolves it, which is checked
below, so the fixture carries no amp in its name or contents.

Anything still skipped is printed with the reason. A protocol silently
missing from this corpus is how it came to cover 10 of 46 without anyone
noticing.

Every protocol accepted as in-subset also has its rendered output resolved and
its IR compared with the original; a mismatch or a ResolveError is a hard
failure rather than a fixture.

Run: python tools/gen_editor_fixtures.py <refrain-protocols-dir> <out-dir>
                                        [required,protocol,stems]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import refrain
from refrain.amp_profile import load_amp_profile
from refrain.editor import describe_protocol, render_protocol
from refrain.ir_json import ir_to_json_obj
from refrain.resolver import resolve

AMP_PROFILE_DIR = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles"


def _candidate_amps() -> list[tuple[str, object | None]]:
    """No amp first, then every built-in profile in a stable order."""
    return [("none", None)] + [
        (p.stem, load_amp_profile(p)) for p in sorted(AMP_PROFILE_DIR.glob("*.json"))
    ]


class AmpDependentModel(RuntimeError):
    """A protocol described differently under two amps that both resolve it."""


def _describe_with_any_amp(
    src: str, amps: list[tuple[str, object | None]], *, stem: str
) -> tuple[dict, str | None]:
    """First (description, amp name) that is ok and in subset, else the last try.

    Every other amp that also resolves must produce the same model. The fixture
    carries no amp in its name, so an amp-dependent model would make this
    corpus depend on the order profiles happen to be tried. Raise instead.
    """
    chosen: tuple[dict, str] | None = None
    last: dict = {}
    for name, amp in amps:
        last = describe_protocol(src, amp=amp)
        if not (last["ok"] and last["in_subset"]):
            continue
        if chosen is None:
            chosen = (last, name)
        elif json.dumps(last["model"]) != json.dumps(chosen[0]["model"]):
            raise AmpDependentModel(
                f"{stem}: model under amp {name!r} differs from amp {chosen[1]!r}. "
                "The fixture name cannot say which amp it came from, so this "
                "corpus is no longer well defined — give the fixtures an amp "
                "dimension before regenerating."
            )
    if chosen is not None:
        return chosen
    return last, None


class RoundTripBroken(RuntimeError):
    """The editor rendered a protocol back out and the language rejected it,
    or accepted it as something else."""


def _check_round_trip(src: str, out: str, amp: object | None, stem: str) -> None:
    """Resolve what we just rendered and compare IR with the original.

    Writing render_protocol(model) to disk without re-resolving it is how the
    corpus came to contain four protocols that the editor could produce and
    the resolver would refuse: a placement with `allowed` omitted came back as
    the illegal `allowed = []`. Every gate was green, because nothing ever
    asked the language what it thought of the output. This asks.
    """
    try:
        after = ir_to_json_obj(resolve(refrain.parse(out), amp))
    except Exception as e:
        raise RoundTripBroken(
            f"{stem}: the editor rendered source that does not resolve: "
            f"{type(e).__name__}: {e}"
        ) from e
    before = ir_to_json_obj(resolve(refrain.parse(src), amp))
    if before != after:
        raise RoundTripBroken(
            f"{stem}: rendered source resolves, but to a different IR than the "
            "original. The fixture would enshrine the difference."
        )


def _why_skipped(d: dict) -> str:
    if not d.get("ok"):
        diags = d.get("diagnostics") or []
        return diags[0]["message"] if diags else "did not resolve"
    return "not in the editor subset"


def main() -> None:
    corpus = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    amps = _candidate_amps()
    files = sorted(corpus.glob("protocols/**/*.refrain")) + sorted(
        corpus.glob("drafts/**/*.refrain")
    )
    written, skipped = 0, []
    for f in files:
        src = f.read_text()
        d, amp_name = _describe_with_any_amp(src, amps, stem=f.stem)
        if amp_name is None:
            skipped.append((f.stem, _why_skipped(d)))
            continue
        model = d["model"]
        amp = dict(amps)[amp_name]
        _check_round_trip(src, render_protocol(model), amp, f.stem)
        # NO sort_keys: preserve insertion order so meta (iterated in order by
        # both renderers) round-trips. JS JSON.parse preserves textual key order.
        (out / f"{f.stem}.model.json").write_text(json.dumps(model, indent=2) + "\n")
        (out / f"{f.stem}.refrain").write_text(render_protocol(model))
        (out / f"{f.stem}.src.refrain").write_text(src)  # verbatim hand-written original
        written += 1
    print(f"wrote {written} fixture pairs to {out} (of {len(files)} protocols)")
    required = [s for s in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if s]
    if required:
        got = {f.stem for f in files} - {stem for stem, _ in skipped}
        missing = [s for s in required if s not in got]
        if missing:
            raise SystemExit(
                "these protocols were required to be in the editor subset and "
                f"are not: {', '.join(missing)}\n"
                "A fixture COUNT cannot stand in for this check: the corpus "
                "total is reached with or without some of the fixes, because a "
                "protocol can be held out by an unrelated second obstacle."
            )
        print(f"all {len(required)} required protocols are in the subset")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for stem, why in skipped:
            print(f"  {stem}: {why}")


if __name__ == "__main__":
    main()
