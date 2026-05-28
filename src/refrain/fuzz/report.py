# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Render the balanced fuzz report.

Two co-equal sections:
  A) "What your protocol does" — behavioral summary derived from coverage
     + structural smells (unreachable / unassertable branches).
  B) "Engine check" — verdicts + coverage matrix + don't-care breakdown
     by reason + metamorphic violations.
"""
from __future__ import annotations

from collections.abc import Iterable

from .check import MetamorphicViolation, PerScenarioResult
from .scenario import Verdict

_BAR = "━" * 60

_MAX_FIRED_SHOWN = 4
_MAX_NOT_FIRED_SHOWN = 3


def _leaf_tags(r: PerScenarioResult) -> set[str]:
    """Return the leaf-side (last colon-segment) of all coverage tags."""
    return {t.split(":")[-1] for t in r.coverage_tags}


def render_report(
    *,
    protocol_name: str,
    results: Iterable[PerScenarioResult],
    metamorphic_violations: list[MetamorphicViolation],
    all_coverage_tags: set[str],
) -> str:
    rs = list(results)
    out: list[str] = []
    out.append(f"\n{_BAR}\nrefrain fuzz: {protocol_name}\n{_BAR}\n")

    # --- Section A: What your protocol does ---
    out.append("\n## What your protocol does\n")
    behavior = _behavioral_summary(rs)
    out.append(behavior + "\n")

    smells = _structural_smells(rs, all_coverage_tags)
    if smells:
        out.append("\nStructural smells:\n")
        for smell in smells:
            out.append(f"  • {smell}\n")

    # --- Section B: Engine check ---
    out.append("\n## Engine check\n")
    pass_count = sum(1 for r in rs if r.verdict is Verdict.PASS)
    missed = [r for r in rs if r.verdict is Verdict.MISSED]
    spurious = [r for r in rs if r.verdict is Verdict.SPURIOUS]
    out.append(f"  scenarios:  {len(rs)}\n")
    out.append(f"  pass:       {pass_count}\n")
    out.append(f"  missed:     {len(missed)}\n")
    out.append(f"  spurious:   {len(spurious)}\n")

    total_dc = sum(r.n_dont_care_intervals for r in rs)
    total_crisp = sum(r.n_crisp_assertions for r in rs)
    out.append(f"  crisp asserts: {total_crisp}\n")
    out.append(f"  don't-care intervals: {total_dc}\n")

    if missed:
        out.append("\n  MISSED (engine failed to fire when oracle predicted SHOULD-FIRE):\n")
        for r in missed:
            out.append(f"    [VIOLATION:MISSED] {r.label}\n")
    if spurious:
        out.append("\n  SPURIOUS (engine fired when oracle predicted SHOULD-NOT-FIRE):\n")
        for r in spurious:
            out.append(f"    [VIOLATION:SPURIOUS] {r.label} ({r.n_events} extra events)\n")
    if metamorphic_violations:
        out.append("\n  METAMORPHIC monotonicity violations:\n")
        for v in metamorphic_violations:
            series_str = " < ".join(f"{lab}={n}" for lab, n in v.series)
            out.append(f"    [VIOLATION:METAMORPHIC] {v.tag_group}: {series_str}\n")

    overall = "PASS" if (pass_count == len(rs) and not metamorphic_violations) else "FAIL"
    out.append(f"\n  overall: {overall}\n")
    out.append(_BAR + "\n")
    return "".join(out)


def _behavioral_summary(results: list[PerScenarioResult]) -> str:
    """Plain-language summary inferred from the pivotal-scenario coverage.

    Heuristic: the generator's pivotal scenarios carry a `:true` / `:false`
    leaf-side suffix (see generate.py `_pivotal_scenarios_for_leaf`). We use
    `n_events > 0` as the "engine fired" proxy (an emitted event = the reward
    fired), independent of the PASS/MISSED verdict, so the summary describes
    OBSERVED behaviour rather than oracle agreement.
    """
    # TRUE-pivot scenarios where the engine fired → reward responds to the
    # favourable condition.
    fired = [
        r for r in results
        if r.n_events > 0 and "true" in _leaf_tags(r)
    ]
    # FALSE-pivot scenarios where the engine stayed silent → reward is
    # correctly suppressed by the adverse condition.
    did_not_fire = [
        r for r in results
        if r.n_events == 0 and "false" in _leaf_tags(r)
    ]
    lines = []
    if fired:
        shown = ", ".join(r.label for r in fired[:_MAX_FIRED_SHOWN])
        ellipsis = "…" if len(fired) > _MAX_FIRED_SHOWN else ""
        lines.append(
            f"  Reward fires when the favourable conditions hold "
            f"(observed in {len(fired)} scenarios: {shown}{ellipsis})."
        )
    if did_not_fire:
        shown = ", ".join(r.label for r in did_not_fire[:_MAX_NOT_FIRED_SHOWN])
        lines.append(
            f"  Reward does NOT fire under the {len(did_not_fire)} adverse-condition scenarios "
            f"(e.g. {shown})."
        )
    if not lines:
        lines.append("  (No pivotal scenarios produced contrast yet — broaden coverage.)")
    return "\n".join(lines)


def _structural_smells(
    results: list[PerScenarioResult], all_coverage_tags: set[str]
) -> list[str]:
    """Tags the generator intended to cover but no scenario asserted crisply.

    A tag is "covered" if some result carrying it made >0 crisp assertions.
    From PerScenarioResult alone we cannot tell whether an uncovered tag was
    never reached or was reached but fully DON'T-CARE, so the message stays
    deliberately non-committal ("uncovered").
    """
    covered = {t for r in results for t in r.coverage_tags if r.n_crisp_assertions > 0}
    uncovered = all_coverage_tags - covered
    return [f"uncovered (no crisp assertion): {t}" for t in sorted(uncovered)]


__all__ = ["render_report"]
