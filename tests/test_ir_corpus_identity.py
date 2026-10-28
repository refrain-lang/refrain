"""Pins the compiled IR of every in-repo protocol, byte for byte.

Each protocol that existed at 0.24.0 must keep hashing to the value in
``fixtures/ir_corpus_identity.json``. Language additions must not move it.

``added`` exists because new protocols legitimately join ``bench/protocols/``
after 0.24.0. A frozen equality over the whole corpus would then fail for a
reason that is not a regression, and the usual fix (regenerating the snapshot)
would destroy the guard. Instead a new protocol is declared in ``added`` and
the old ones stay pinned. Do not collapse this back into a frozen equality, and
never regenerate ``hashes`` for an existing path.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse_file
from refrain.resolver import ResolveWarning, resolve

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).parent / "fixtures" / "ir_corpus_identity.json"
CORPUS = sorted(REPO.glob("examples/**/*.refrain")) + sorted(
    (REPO / "bench" / "protocols").glob("*.refrain")
)


def snapshot() -> tuple[dict[str, str], list[str]]:
    """Return (path -> sha256 of the IR JSON, sorted paths that fail to resolve)."""
    hashes: dict[str, str] = {}
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
        text = json.dumps(ir_to_json_obj(ir, sample_rate_hz=256), sort_keys=True)
        hashes[rel] = hashlib.sha256(text.encode()).hexdigest()
    return hashes, sorted(unresolvable)


def _expected() -> dict:
    return json.loads(SNAPSHOT.read_text())


def test_corpus_is_the_expected_size():
    # 9 examples + 1 othmer library parent + 25 bench protocols as of 0.24.0.
    assert len(CORPUS) == 35 + len(_expected()["added"])


def test_every_protocol_ir_is_byte_identical_to_the_snapshot():
    hashes, _ = snapshot()
    pinned = _expected()["hashes"]
    assert {p: hashes.get(p) for p in pinned} == pinned


def test_new_protocols_are_declared_in_added():
    hashes, _ = snapshot()
    expected = _expected()
    new = set(hashes) - set(expected["hashes"])
    assert new == set(expected["added"])


def test_the_set_of_unresolvable_protocols_has_not_grown():
    _, unresolvable = snapshot()
    # examples/othmer_ilf_cz_pz.refrain extends a library parent and needs an
    # import resolver, so it does not resolve standalone.
    assert unresolvable == _expected()["unresolvable"]
