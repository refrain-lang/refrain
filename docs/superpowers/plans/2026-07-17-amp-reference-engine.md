# Amp Reference Abstraction (`amp.reference`) — Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a protocol read its montage reference from the connected amp profile at resolve time (`referential(active: site, reference: amp.reference)`), folding to a string literal so the IR is identical to a literal-authored protocol.

**Architecture:** `amp` becomes a resolver namespace root (mirroring the existing `reward` namespace) whose one allow-listed field, `reference`, folds to an `IRStringLit` during `resolve()`, and fails closed when no profile or field is present. This is purely additive — no existing protocol changes behaviour.

**Tech Stack:** Python 3.10+ (parser/resolver), pytest.

**Spec:** `docs/superpowers/specs/2026-07-16-amp-reference-abstraction-design.md`

**Scope of THIS plan — the additive increment (v0.15.0).** Just the `amp.reference` namespace + the profile field. It is releasable and fully green on its own, and it is everything the `refrain-protocols` working slice needs (a re-authored SMR-at-Cz protocol folds to `device` on BrainBit and `linked_ears` on Q21 — and Q21 declares A1/A2, so its runtime path already works).

**Deliberately NOT in this plan (see "Deferred: fail-closed hardening" at the end):** the `linked_ears` runtime break (both evaluators) and the consistency lint. Those are BREAKING and would turn refrain's own suite red — three shipped examples (`examples/smr_cz.refrain`, `alpha_theta.refrain`, `critical_fluctuation_cue.refrain`) use literal `linked_ears` with no ear electrodes, and many golden tests compile them. They need the examples fixed + goldens regenerated, and are a separate scoped increment. Keeping them out is what lets THIS increment ship green.

## Global Constraints

- **Purely additive.** `amp.reference` is opt-in; every existing protocol must still resolve unchanged with `amp=None`. No test in the current suite may change or newly skip.
- **No amp-profile schema bump.** The `reference` field is optional; `AMP_PROFILE_SCHEMA` stays `"refrain-amp-profile/v0"`.
- **Fail closed.** No profile, no field, or a non-allow-listed field is a `ResolveError` — never a default or a guess.
- **Allow-list is exactly `{reference}`.** `amp.clean_hf_floor` is a later sub-project; nothing else is exposed.
- **`amp` becomes a reserved namespace root.** No corpus protocol uses `amp` as an identifier today.
- **`refrain` + `refrain_core` are lockstep since v0.14.0.** This increment touches no Rust, so the `refrain_core` wheel is a straight rebuild. The version bump + tag follow the release procedure (bump `pyproject.toml` + CHANGELOG in a `release: v0.15.0` PR; tag the merge commit — never before).
- **Env:** run Python via the worktree venv: `.venv/bin/python`.

## File Structure

- `src/refrain/amp_profile.py` — add optional `reference` field + load-time validation. Keep lightweight (no scipy import).
- `src/refrain/amp_profiles/{brainbit_flex,q21,openbci_cyton}.json` — populate `reference`.
- `src/refrain/resolver.py` — `amp` namespace in `_resolve_member_access`.
- `tests/test_amp_reference.py` — new: namespace + fold-equivalence.
- `tests/test_amp_profile.py` — extend for the `reference` field.
- `CHANGELOG.md` — Added entry.

---

### Task 1: `AmpProfile.reference` field + load validation

**Files:**
- Modify: `src/refrain/amp_profile.py` (dataclass at :46; `_parse_amp_profile` at :101; construction at ~:135)
- Test: `tests/test_amp_profile.py`

**Interfaces:**
- Produces: `AmpProfile.reference: str | None` (default `None`); `load_amp_profile` raises `AmpProfileError` when `reference` is present but is neither a reference keyword nor a declared channel.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_amp_profile.py`:

```python
def test_reference_attribute_exists():
    p = load_amp_profile(PROFILES_DIR / "openbci_cyton.json")
    assert hasattr(p, "reference")

def test_reference_keyword_accepted(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "device",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    assert load_amp_profile(f).reference == "device"

def test_reference_channel_name_accepted(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz", "A1"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "A1",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    assert load_amp_profile(f).reference == "A1"

def test_reference_invalid_raises(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "bogus",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    with pytest.raises(AmpProfileError, match="reference"):
        load_amp_profile(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_amp_profile.py -k reference -v`
Expected: FAIL (`AttributeError` / no validation).

- [ ] **Step 3: Add the field and validation**

In `src/refrain/amp_profile.py`, add a module-level constant after `AMP_PROFILE_SCHEMA` (~line 27). This mirrors `primitive_impls.REFERENCE_KEYWORDS` but is duplicated deliberately — `amp_profile` must stay free of the scipy import that `primitive_impls` pulls in:

```python
# Reference-operation keywords a montage may name. Mirrors
# `primitive_impls.REFERENCE_KEYWORDS`; duplicated to keep this module
# dependency-light (no scipy). Keep the two in sync.
AMP_REFERENCE_KEYWORDS = frozenset({"linked_ears", "common_average", "device"})
```

Add the field to the `AmpProfile` dataclass as the **last** field (after `resource_limits` at :60 — it must be last because it is the only field with a default):

```python
    resource_limits: ResourceLimits
    reference: str | None = None
```

In `_parse_amp_profile`, just before the `return AmpProfile(...)`, add validation:

```python
    reference = data.get("reference")
    if reference is not None:
        allowed = AMP_REFERENCE_KEYWORDS | {c.name for c in channels}
        if reference not in allowed:
            raise AmpProfileError(
                f"{source}: reference {reference!r} must be a reference keyword "
                f"{sorted(AMP_REFERENCE_KEYWORDS)} or a declared channel"
            )
```

Then add `reference=reference,` as the last argument in the `return AmpProfile(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_amp_profile.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/refrain/amp_profile.py tests/test_amp_profile.py
git commit -m "feat(amp): optional reference field on AmpProfile with load validation"
```

---

### Task 2: Populate `reference` on the three shipped profiles

**Files:**
- Modify: `src/refrain/amp_profiles/brainbit_flex.json`, `q21.json`, `openbci_cyton.json`
- Test: `tests/test_amp_profile.py`

**Interfaces:**
- Consumes: `AmpProfile.reference` (Task 1).
- Produces: shipped profiles resolve `amp.reference` to `device` (BrainBit) / `linked_ears` (Q21, OpenBCI).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_amp_profile.py`:

```python
def test_shipped_profiles_declare_reference():
    assert load_amp_profile(PROFILES_DIR / "brainbit_flex.json").reference == "device"
    assert load_amp_profile(PROFILES_DIR / "q21.json").reference == "linked_ears"
    assert load_amp_profile(PROFILES_DIR / "openbci_cyton.json").reference == "linked_ears"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_amp_profile.py::test_shipped_profiles_declare_reference -v`
Expected: FAIL (`None != 'device'`).

- [ ] **Step 3: Add the field to each JSON**

In `brainbit_flex.json`, add `"reference": "device",` after `"sample_rates_hz"`, and trim `_comment_reference` to just the human explanation (it is now a real field). In `q21.json` and `openbci_cyton.json`, add `"reference": "linked_ears",`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_amp_profile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/amp_profiles/
git commit -m "feat(amp): declare reference on brainbit_flex/q21/openbci_cyton profiles"
```

---

### Task 3: `amp` namespace in the resolver (+ fold-equivalence)

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_member_access` at :1901; add `_resolve_amp_field`; add `_AMP_ALLOWED_FIELDS` module constant)
- Test: `tests/test_amp_reference.py` (new)

**Interfaces:**
- Consumes: `self.amp` (`resolver.py:131`), `AmpProfile.reference` (Task 1), `IRStringLit` (imported at :70, constructor `IRStringLit(value=..., loc=...)`), `_collect_member_path` (returns `(root, m1, ...)`).
- Produces: `amp.reference` folds to `IRStringLit(value=<profile.reference>)`; fail-closed `ResolveError` on `amp=None`, missing field, or non-allow-listed member.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_amp_reference.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`amp.reference` namespace: resolve-time fold + fail-closed behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import refrain
from refrain.amp_profile import load_amp_profile
from refrain.ir_json import ir_to_json_obj
from refrain.resolver import ResolveError, resolve

PROFILES = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles"
BRAINBIT = load_amp_profile(PROFILES / "brainbit_flex.json")
Q21 = load_amp_profile(PROFILES / "q21.json")


def _proto(reference: str) -> str:
    # Minimal SMR-at-Cz protocol; `reference` is spliced verbatim so both the
    # amp.reference form and a literal form come from one template. The reward
    # references the derive/threshold as string literals and via dwell(above(...)),
    # matching real-protocol idiom (validated against refrain v0.14.0).
    return f'''
protocol "t_v1" {{
  meta {{ title = "t"; status = "draft" }}
  requires {{ sample_rate = ">= 250 Hz"; channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: {reference}) }}
  derive "env" {{
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz)), hilbert(), magnitude() ]
  }}
  threshold "env_t" {{ signal = "env"; type = absolute(value: 5 uV) }}
  reward {{ event = dwell(condition: above("env", "env_t"), duration: 250 ms) }}
  output {{ audio = reward.event }}
}}
'''


def _reference_arg(ir: dict) -> str:
    montage = ir["inputs"]["raw"]["montage"]
    arg = next(a for a in montage["args"] if a["name"] == "reference")
    return arg["value"]["value"]


def test_amp_reference_folds_to_device_on_brainbit():
    ir = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=BRAINBIT))
    assert _reference_arg(ir) == "device"


def test_amp_reference_folds_to_linked_ears_on_q21():
    ir = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=Q21))
    assert _reference_arg(ir) == "linked_ears"


def test_fold_is_byte_identical_to_literal_device_on_brainbit():
    got = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=BRAINBIT))
    want = ir_to_json_obj(resolve(refrain.parse(_proto('"device"')), amp=BRAINBIT))
    assert got == want


def test_amp_reference_without_profile_fails_closed():
    with pytest.raises(ResolveError, match="requires an amp profile"):
        resolve(refrain.parse(_proto("amp.reference")), amp=None)


def test_amp_non_allowlisted_field_fails():
    with pytest.raises(ResolveError, match="not an exposed amp field"):
        resolve(refrain.parse(_proto("amp.adc_bits")), amp=BRAINBIT)


def test_amp_reference_missing_on_profile_fails(tmp_path):
    data = json.loads((PROFILES / "brainbit_flex.json").read_text())
    data.pop("reference", None)
    f = tmp_path / "no_ref.json"; f.write_text(json.dumps(data))
    amp = load_amp_profile(f)
    with pytest.raises(ResolveError, match="declares no 'reference'"):
        resolve(refrain.parse(_proto("amp.reference")), amp=amp)
```

Note on the byte-identical test: it compares against a literal-`"device"` protocol (both resolved with `amp=BRAINBIT`, both `requires.channels=["Cz"]`), which is safe because `"device"` needs no ear electrodes. A literal-`"linked_ears"` comparison is deliberately NOT used — that would require declaring ears and is coupled to the deferred lint. For Q21 we assert the folded value only.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_amp_reference.py -v`
Expected: FAIL — `_resolve_member_access` currently raises "member access is only supported on `reward`".

- [ ] **Step 3: Add the amp namespace to the resolver**

In `src/refrain/resolver.py`, add a module-level constant near the top (after imports):

```python
# Amp-profile fields a protocol may read via the `amp` namespace. An
# allow-list, not the whole dataclass: exposed fields are facts a protocol
# ADOPTS; the profile's other fields are constraints the resolver CHECKS.
_AMP_ALLOWED_FIELDS: tuple[str, ...] = ("reference",)
```

Extend `_resolve_member_access` (`:1901`) — add the `amp` branch before the final `raise`, and add the helper method right after it:

```python
    def _resolve_member_access(self, expr: A.MemberAccess) -> IRExpr:
        # Special-case reward fields: `reward.continuous`, `reward.event`,
        # `reward.event.holds`.
        path = _collect_member_path(expr)
        if path is not None and path[0] == "reward":
            return self._resolve_reward_field(path[1:], expr.loc)
        if path is not None and path[0] == "amp":
            return self._resolve_amp_field(path[1:], expr.loc)
        raise ResolveError(
            "member access is only supported on `reward` (e.g. `reward.event.holds`) "
            "and `amp` (e.g. `amp.reference`)",
            loc=expr.loc,
        )

    def _resolve_amp_field(self, parts: tuple[str, ...], loc: Loc | None) -> IRExpr:
        member = ".".join(("amp",) + parts)
        if len(parts) != 1 or parts[0] not in _AMP_ALLOWED_FIELDS:
            raise ResolveError(
                f"{member!r} is not an exposed amp field; "
                f"allowed: {', '.join(_AMP_ALLOWED_FIELDS)}",
                loc=loc,
            )
        if self.amp is None:
            raise ResolveError(
                f"{member!r} requires an amp profile, but resolve() was called "
                f"with amp=None",
                loc=loc,
            )
        value = getattr(self.amp, parts[0])
        if value is None:
            raise ResolveError(
                f"amp profile {self.amp.model!r} declares no {parts[0]!r}",
                loc=loc,
            )
        return IRStringLit(value=value, loc=loc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_amp_reference.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_amp_reference.py
git commit -m "feat(resolver): amp.reference namespace, fail-closed, folds at resolve time"
```

---

### Task 4: CHANGELOG + full-suite verification

**Files:**
- Modify: `CHANGELOG.md`
- (Version bump to 0.15.0 in `pyproject.toml` happens in the separate release PR per the release procedure — NOT here.)

- [ ] **Step 1: Add the CHANGELOG entry**

At the top of `CHANGELOG.md`, add:

```markdown
## [Unreleased]

### Added
- **`amp.reference` — montage reference from the connected amp profile.** A
  protocol may write `referential(active: site, reference: amp.reference)`; the
  resolver folds it to the connected profile's `reference` (`device` /
  `linked_ears` / `common_average` / a channel) at resolve time, producing an IR
  identical to a literal-authored one. `amp` is a new resolver namespace root
  (allow-list: `reference`). Fails closed: `resolve(amp=None)`, a missing field,
  or a non-allow-listed field is a `ResolveError`. `AmpProfile` gains an optional
  `reference` field (no schema bump); the three shipped profiles declare it.
```

- [ ] **Step 2: Run the full Python suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all; the pre-existing 172 + the new tests). No pre-existing test changed; no new skips.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for amp.reference (additive)"
```

---

## Post-plan verification (definition of done — additive increment)

- [ ] `.venv/bin/python -m pytest -q` green, no pre-existing test changed, no new skips.
- [ ] Manual smoke (once, to see it): an `amp.reference` protocol resolves to `device` on `brainbit_flex` and `linked_ears` on `q21` (covered by `tests/test_amp_reference.py`).
- [ ] Releasable as v0.15.0 via the separate release PR (bump `pyproject.toml`, rebuild the `refrain_core` wheel, tag the merge commit — never before).

---

## Deferred: fail-closed hardening (separate follow-on plan)

Captured here so it is not lost. NOT part of the increment above — it is BREAKING and would turn refrain's own suite red, so it ships as its own plan/release after the example + golden cleanup.

**Newly discovered prerequisite (found during this plan's self-review):** three shipped examples use literal `reference: "linked_ears"` with no ear electrodes in `requires.channels` and would be rejected by the lint / would raise at eval:
- `examples/smr_cz.refrain` (`channels = ["Cz"]`)
- `examples/alpha_theta.refrain` (`channels = ["Pz"]`)
- `examples/critical_fluctuation_cue.refrain` (`channels = ["Cz"]`, `active: sites`)

Fixing them means adding `A1`, `A2` to each `requires.channels`, then regenerating and eyeballing every golden that compiles them (`test_ir_json`, `test_editor_*`, `test_compile_json`, `test_server`, the fuzz synthetic-channel tests). That cascade is exactly why this is a separate increment.

**The hardening itself (design in the spec, §"Breaking change" and §"The consistency lint"):**
1. **Python runtime break** — `ReferentialImpl._resolve_reference` (`primitive_impls.py:119-123`) raises `ValueError` instead of returning `None` on an earless source; drop the "falls back to common_average" docstring clause.
2. **Rust runtime break + fallible constructor** — `Referential::new` (`eval.rs:232`) returns `Result<Self, String>` (the `linked_ears` `cand.len() < 2` arm at :250 returns `Err`); thread `Result` through `Montage::referential` (:204), `build_montage`, and `Evaluator::new` (:822); add `RefrainError::UnrealizableMontage` (`mobile.rs:22`) and map it at the constructor (`mobile.rs:116`), unwrap at the test caller (`mobile.rs:227`), and map to `PyValueError` at `python.rs:88` (construct the evaluator before the `guard` closure).
3. **Consistency lint** — in `resolver.py`, a literal `reference: "linked_ears"` montage requires `>= 2` of A1/A2/M1/M2/T9/T10 in `requires.channels`, else `ResolveError`; scoped to the literal spelling so it never fires on `amp.reference`. Wiring: store `self._requires_channels = requires_ir.channels` right after `requires_ir = self._resolve_requires()` (`resolver.py:211`); add the lint helper and call it in `_resolve_input` right after the placement-substitution line (`resolver.py:473`), reading `self._requires_channels`.
4. **CHANGELOG** — two BREAKING entries.
5. **Sequencing with `refrain-protocols`** — the lint and the 21-protocol `requires.channels` fix land together (the lint rejects exactly what the fix corrects).
