"""Pins the compiled IR of every in-repo protocol.

Each protocol that existed at 0.24.0 must still compile to the structure and
the coefficients recorded in ``fixtures/ir_corpus_identity.json``. Language
additions must not move either.

``added`` exists because new protocols legitimately join ``bench/protocols/``
after 0.24.0. A frozen equality over the whole corpus would then fail for a
reason that is not a regression, and the usual fix (regenerating the snapshot)
would destroy the guard. Instead a new protocol is declared in ``added`` and
the old ones stay pinned. Do not collapse this back into a frozen equality, and
never regenerate a pinned path's entry.

**Why this is two comparisons rather than one hash.** The first version of this
test hashed ``json.dumps(ir_to_json_obj(...))`` verbatim. It passed only on the
machine that wrote the fixture: the IR carries scipy-designed filter
coefficients at full double precision, and those differ in the last bits
between CPU architectures. The split was exact — of the 21 bench protocols, the
17 embedding coefficients all hashed differently on Linux/x86_64 from the
macOS/arm64 fixture, and the 4 embedding none all matched.

Rounding the floats before hashing was tried and rejected: it absorbs a few ULP
but rounding has boundaries, and a protocol with 190 coefficients gets 190
chances to sit on one. At 12 significant digits a 64-ULP perturbation still
moved 23 of the 34 pinned protocols. A guard that holds for the perturbations
you happened to test is the same defect in a new place.

So structure and values are compared separately: every key, string, int, bool
and *float position* exactly, via a hash of the IR with the floats blinded out;
and every float by relative tolerance. There are no rounding boundaries, a
structural change cannot hide in a float, and a coefficient change cannot hide
in the structure.

The pinned values were re-baselined once, when this scheme replaced the raw
hash. Every pre-existing protocol's entry was checked against what the v0.24.0
compiler produces for the same file, and all 34 matched: 0.25.0 genuinely does
not move any of them. That check is what the pin rests on; the fixture is its
record.

This tolerance is a property of this test, not of the IR. ``content_hash`` in
``compile_json`` still hashes the unquantized canonical bytes, so it remains
architecture-dependent for any protocol containing a filter — see
``docs/superpowers/records/2026-11-19-content-hash-is-not-portable.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from pathlib import Path
from typing import Any

from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse_file
from refrain.resolver import ResolveWarning, resolve

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).parent / "fixtures" / "ir_corpus_identity.json"
CORPUS = sorted(REPO.glob("examples/**/*.refrain")) + sorted(
    (REPO / "bench" / "protocols").glob("*.refrain")
)

# A float's position in the IR is structure and is compared exactly; its value
# is compared with a tolerance. `rel_tol` sits far above cross-architecture
# noise (a few ULP, ~1e-16 relative) and far below any coefficient change a
# person could intend — a different filter order or band edge moves the leading
# digits. `abs_tol` covers values at or near zero, where `rel_tol` means nothing.
_REL_TOL = 1e-9
_ABS_TOL = 1e-12

# Stands in for a float in the blinded structure. Not producible by the
# emitter, so it cannot collide with real content.
_FLOAT_HOLE = "\u0000float\u0000"


def blind(obj: Any, floats: list[float]) -> Any:
    """Replace every float with `_FLOAT_HOLE`, appending its value to `floats`.

    Dicts are walked in sorted key order so `floats` has a canonical order
    independent of emitter dict ordering.
    """
    if isinstance(obj, bool):  # bool is not a float, but be explicit
        return obj
    if isinstance(obj, float):
        floats.append(obj)
        return _FLOAT_HOLE
    if isinstance(obj, dict):
        return {k: blind(obj[k], floats) for k in sorted(obj)}
    if isinstance(obj, list):
        return [blind(v, floats) for v in obj]
    return obj


def fingerprint(ir: Any) -> tuple[str, list[float]]:
    """Return (sha256 of the float-blinded IR JSON, the floats in canonical order)."""
    floats: list[float] = []
    blinded = blind(ir_to_json_obj(ir, sample_rate_hz=256), floats)
    structure = hashlib.sha256(
        json.dumps(blinded, sort_keys=True).encode()
    ).hexdigest()
    return structure, floats


def snapshot() -> tuple[dict[str, str], dict[str, list[float]], list[str]]:
    """Return (path -> structure hash, path -> floats, sorted unresolvable paths)."""
    structures: dict[str, str] = {}
    floats: dict[str, list[float]] = {}
    unresolvable: list[str] = []
    for path in CORPUS:
        rel = path.relative_to(REPO).as_posix()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResolveWarning)
                ir = resolve(parse_file(path))
        except Exception:
            unresolvable.append(rel)
            continue
        structures[rel], floats[rel] = fingerprint(ir)
    return structures, floats, sorted(unresolvable)


def _expected() -> dict:
    return json.loads(SNAPSHOT.read_text())


def test_corpus_is_the_expected_size():
    # 9 examples + 1 othmer library parent + 25 bench protocols as of 0.24.0.
    assert len(CORPUS) == 35 + len(_expected()["added"])


def test_every_protocol_ir_structure_is_identical_to_the_snapshot():
    """Keys, strings, ints, bools, and every float's position — compared exactly."""
    structures, _, _ = snapshot()
    pinned = _expected()["structure"]
    assert {p: structures.get(p) for p in pinned} == pinned


def test_every_protocol_coefficient_is_unchanged_within_tolerance():
    _, floats, _ = snapshot()
    pinned = _expected()["floats"]
    for path, expected in pinned.items():
        actual = floats.get(path)
        assert actual is not None, f"{path} no longer resolves"
        assert len(actual) == len(expected), (
            f"{path}: {len(actual)} floats, snapshot has {len(expected)}"
        )
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert math.isclose(a, e, rel_tol=_REL_TOL, abs_tol=_ABS_TOL), (
                f"{path}: float {i} moved {e!r} -> {a!r}"
            )


def test_new_protocols_are_declared_in_added():
    structures, _, _ = snapshot()
    expected = _expected()
    new = set(structures) - set(expected["structure"])
    assert new == set(expected["added"])


def test_the_set_of_unresolvable_protocols_has_not_grown():
    _, _, unresolvable = snapshot()
    # examples/othmer_ilf_cz_pz.refrain extends a library parent and needs an
    # import resolver, so it does not resolve standalone.
    assert unresolvable == _expected()["unresolvable"]


def test_blinding_separates_structure_from_values():
    """The two halves of the guard, pinned so neither can be weakened alone.

    A simplification back to hashing raw `json.dumps` output would pass here
    and fail on any other CPU architecture, which is how the machine-bound
    version of this test reached main.
    """
    base = {"coeff": [-1.8185096561303429, 0.0], "order": 4, "kind": "bandpass", "on": True}

    f1: list[float] = []
    s1 = blind(base, f1)
    assert f1 == [-1.8185096561303429, 0.0]
    assert s1 == {"coeff": [_FLOAT_HOLE, _FLOAT_HOLE], "order": 4, "kind": "bandpass", "on": True}

    # An int is not blinded, so an int -> float change is a structural change.
    f2: list[float] = []
    assert blind({**base, "order": 4.0}, f2) != s1

    # A bool stays a bool rather than becoming a hole.
    f3: list[float] = []
    assert blind(base, f3)["on"] is True

    # Nudging a coefficient leaves the structure identical and shows in floats.
    nudged = {**base, "coeff": [math.nextafter(base["coeff"][0], math.inf), 0.0]}
    f4: list[float] = []
    assert blind(nudged, f4) == s1
    assert f4 != f1
    assert math.isclose(f4[0], f1[0], rel_tol=_REL_TOL, abs_tol=_ABS_TOL)

    # A change a person could mean is outside the tolerance.
    assert not math.isclose(
        f1[0] * (1 + 1e-6), f1[0], rel_tol=_REL_TOL, abs_tol=_ABS_TOL
    )
