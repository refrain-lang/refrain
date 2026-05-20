# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Static per-protocol runtime-cost estimate — an authoring-time complexity hint.

This is a HEURISTIC, not a measurement. It walks a resolved `IRProtocol`,
estimates per-sample CPU cost from the drivers that dominate the streaming
evaluator, and projects a real-time factor (RTF) across rough hardware tiers
so a protocol author can see "this is likely too heavy for embedded hardware"
before deploying.

The coefficients below are PROVISIONAL. They are seeded from a single
benchmark run of the reference evaluator (the P1 suite, on a fast workstation)
and are expected to be re-calibrated by the P2 timing sweep. The *structure*
of the model — percentile windows dominate, scaling super-linearly with sample
rate — reflects measured behaviour; the absolute numbers are rough.

What the P1 benchmark showed:
  - A 3-band envelope pipeline costs ~1 us/sample.
  - Adding two 2-minute percentile-window thresholds raised per-chunk time
    ~380x: the per-sample rolling `np.percentile` over the window buffer is
    the overwhelming cost, and it grows as the window fills.
So percentile windows are the primary scaling risk, and the model says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from .eval_ import _classify_call
from .ir import (
    IRArray,
    IRBinaryOp,
    IRCall,
    IRConditional,
    IRExpr,
    IRProtocol,
    IRTuple,
)

# --- Provisional cost coefficients (reference workstation, us) ---------------
# PENDING P2 CALIBRATION. See module docstring.
K_PERCENTILE_US_PER_WINDOW_SAMPLE = 0.0092  # per-output-sample np.percentile over the window
K_ENVELOPE_US_PER_SAMPLE = 1.0              # one bandpass->hilbert->mag->smooth band
K_DISPATCH_US_PER_SAMPLE = 0.25            # fixed IR-walk / framing floor
K_BANDPOWER_US_PER_WINDOW_SAMPLE = 0.002   # UNCALIBRATED (windowed FFT, periodic)
K_COHERENCE_US_PER_WINDOW_SAMPLE = 0.01    # UNCALIBRATED (windowed cross-spectrum per pair)

# Rough per-tier slowdown vs the reference workstation. PROVISIONAL.
TIER_SLOWDOWN: dict[str, float] = {
    "workstation": 1.0,
    "laptop": 2.5,
    "embedded(Pi4)": 12.0,
}

RTF_TIGHT = 0.5   # above this: little headroom
RTF_EXCEED = 1.0  # at/above this: cannot keep up


@dataclass(frozen=True)
class CostDriver:
    name: str
    detail: str
    us_per_sample: float
    calibrated: bool  # False for drivers whose coefficient is a rough guess


@dataclass(frozen=True)
class CostReport:
    protocol: str
    sample_rate_hz: float
    n_channels: int
    drivers: tuple[CostDriver, ...]
    total_us_per_sample: float
    rtf_by_tier: dict[str, float]
    warnings: tuple[str, ...]
    any_uncalibrated: bool

    @property
    def dominant(self) -> CostDriver:
        return max(self.drivers, key=lambda d: d.us_per_sample)


def _iter_calls(expr: IRExpr):
    """Yield every IRCall in an expression tree (depth-first)."""
    if isinstance(expr, IRCall):
        yield expr
        for arg in expr.args:
            yield from _iter_calls(arg.value)
    elif isinstance(expr, IRBinaryOp):
        yield from _iter_calls(expr.left)
        yield from _iter_calls(expr.right)
    elif isinstance(expr, IRConditional):
        yield from _iter_calls(expr.cond)
        yield from _iter_calls(expr.then_branch)
        yield from _iter_calls(expr.else_branch)
    elif isinstance(expr, (IRArray, IRTuple)):
        for elt in expr.elements:
            yield from _iter_calls(elt)


def _window_ms(call: IRCall, default_ms: float) -> float:
    static, _ = _classify_call(call)
    val = static.get("window_ms", default_ms)
    try:
        return float(val)
    except (TypeError, ValueError):
        # window bound to a control ref or otherwise non-numeric — use default.
        return default_ms


def _all_stream_exprs(ir: IRProtocol):
    """Every IRExpr that can hold stream-producing calls."""
    for d in ir.derives.values():
        yield d.expression
    for ih in ir.inhibits.values():
        yield ih.metric
    if ir.reward.continuous is not None:
        yield ir.reward.continuous
    if ir.reward.event is not None:
        yield ir.reward.event
    yield from ir.output.values()


def estimate_cost(ir: IRProtocol, *, sample_rate_hz: float | None = None) -> CostReport:
    """Estimate per-sample CPU cost and project real-time factor per tier.

    `sample_rate_hz` defaults to the protocol's chosen rate. The estimate is a
    provisional heuristic (see module docstring) — treat it as a hint, not a
    guarantee.
    """
    sr = float(sample_rate_hz) if sample_rate_hz is not None else float(
        ir.requires.sample_rate_chosen_hz
    )
    drivers: list[CostDriver] = []

    # --- percentile windows (the dominant, calibrated driver) ----------------
    pctl_calls: list[IRCall] = []
    for t in ir.thresholds.values():
        if t.threshold_call.callee == "percentile":
            pctl_calls.append(t.threshold_call)
    for ih in ir.inhibits.values():
        if ih.threshold.callee == "percentile":
            pctl_calls.append(ih.threshold)
    if pctl_calls:
        total_window_samples = sum(
            _window_ms(c, 120_000.0) / 1000.0 * sr for c in pctl_calls
        )
        us = K_PERCENTILE_US_PER_WINDOW_SAMPLE * total_window_samples
        windows_s = sorted({_window_ms(c, 120_000.0) / 1000.0 for c in pctl_calls})
        win_desc = ", ".join(f"{w:g}s" for w in windows_s)
        drivers.append(CostDriver(
            name=f"percentile windows ({len(pctl_calls)})",
            detail=f"windows: {win_desc} @ {sr:g} Hz "
                   f"= {total_window_samples:,.0f} window-samples/output-sample",
            us_per_sample=us,
            calibrated=True,
        ))

    # --- coherence pairs (uncalibrated) --------------------------------------
    coh_calls = [c for e in _all_stream_exprs(ir) for c in _iter_calls(e)
                 if c.callee == "coherence"]
    if coh_calls:
        win_samples = sum(_window_ms(c, 1000.0) / 1000.0 * sr for c in coh_calls)
        drivers.append(CostDriver(
            name=f"coherence pairs ({len(coh_calls)})",
            detail=f"{win_samples:,.0f} window-samples (FFT cross-spectrum per pair)",
            us_per_sample=K_COHERENCE_US_PER_WINDOW_SAMPLE * win_samples,
            calibrated=False,
        ))

    # --- bandpower (uncalibrated) --------------------------------------------
    bp_calls = [c for e in _all_stream_exprs(ir) for c in _iter_calls(e)
                if c.callee == "bandpower"]
    if bp_calls:
        win_samples = sum(_window_ms(c, 1000.0) / 1000.0 * sr for c in bp_calls)
        drivers.append(CostDriver(
            name=f"bandpower ({len(bp_calls)})",
            detail=f"{win_samples:,.0f} window-samples (periodic windowed FFT)",
            us_per_sample=K_BANDPOWER_US_PER_WINDOW_SAMPLE * win_samples,
            calibrated=False,
        ))

    # --- envelope bands (bandpass-bearing derives) ---------------------------
    n_bands = sum(
        1 for d in ir.derives.values()
        if any(c.callee == "bandpass" for c in _iter_calls(d.expression))
    )
    if n_bands:
        drivers.append(CostDriver(
            name=f"envelope bands ({n_bands})",
            detail="bandpass -> hilbert -> magnitude -> smooth, per band",
            us_per_sample=K_ENVELOPE_US_PER_SAMPLE * n_bands,
            calibrated=True,
        ))

    # --- fixed dispatch floor ------------------------------------------------
    drivers.append(CostDriver(
        name="dispatch floor",
        detail="per-chunk IR walk / framing overhead, amortized per sample",
        us_per_sample=K_DISPATCH_US_PER_SAMPLE,
        calibrated=True,
    ))

    total = sum(d.us_per_sample for d in drivers)
    rtf_by_tier = {
        tier: total * sr / 1e6 * slow for tier, slow in TIER_SLOWDOWN.items()
    }

    warnings = _build_warnings(drivers, total, rtf_by_tier)
    any_uncalibrated = any(not d.calibrated for d in drivers)

    return CostReport(
        protocol=ir.name,
        sample_rate_hz=sr,
        n_channels=len(ir.requires.channels),
        drivers=tuple(drivers),
        total_us_per_sample=total,
        rtf_by_tier=rtf_by_tier,
        warnings=tuple(warnings),
        any_uncalibrated=any_uncalibrated,
    )


def _build_warnings(
    drivers: list[CostDriver],
    total: float,
    rtf_by_tier: dict[str, float],
) -> list[str]:
    warnings: list[str] = []
    for tier, rtf in rtf_by_tier.items():
        if rtf >= RTF_EXCEED:
            warnings.append(
                f"{tier}: projected RTF {rtf:.2f} >= {RTF_EXCEED:g} — "
                f"will NOT keep up in real time."
            )
        elif rtf >= RTF_TIGHT:
            warnings.append(
                f"{tier}: projected RTF {rtf:.2f} — tight, little headroom."
            )
    if drivers and total > 0:
        dom = max(drivers, key=lambda d: d.us_per_sample)
        share = dom.us_per_sample / total
        if share >= 0.5:
            warnings.append(
                f"dominant cost: '{dom.name}' is {share:.0%} of the estimate."
            )
    return warnings
