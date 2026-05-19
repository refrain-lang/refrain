"""Numerical equivalence checker for the benchmark suite.

The whole DSL-tax measurement rests on the assertion that Refrain and the
baselines compute the same thing. If they don't, the timing comparison is
meaningless. This module is the gate: every (refrain_output, baseline_output)
pair must pass `assert_equivalent` before any timing is reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class EquivalenceFailure(AssertionError):
    """Raised when refrain and baseline streams disagree beyond tolerance."""


@dataclass(frozen=True)
class EquivalenceReport:
    passed: bool
    streams_checked: tuple[str, ...]
    warmup_samples: int
    atol: float
    rtol: float


def assert_equivalent(
    refrain_streams: dict[str, np.ndarray],
    baseline_streams: dict[str, np.ndarray],
    *,
    warmup_samples: int,
    atol: float = 1e-9,
    rtol: float = 1e-6,
) -> EquivalenceReport:
    """Assert that every stream named in `refrain_streams` also exists in
    `baseline_streams` and matches within tolerance after the warmup window.

    Extra streams in `baseline_streams` are ignored.

    Raises `EquivalenceFailure` on any disagreement; returns an
    `EquivalenceReport` on success.
    """
    names = tuple(sorted(refrain_streams.keys()))
    for name in names:
        if name not in baseline_streams:
            raise EquivalenceFailure(
                f"stream {name!r} missing from baseline output"
            )
        a = np.asarray(refrain_streams[name])
        b = np.asarray(baseline_streams[name])
        if a.shape != b.shape:
            raise EquivalenceFailure(
                f"stream {name!r} shape mismatch: refrain={a.shape}, baseline={b.shape}"
            )
        if a.ndim == 0 or a.shape[0] <= warmup_samples:
            continue
        a_steady = a[warmup_samples:]
        b_steady = b[warmup_samples:]
        if not np.allclose(a_steady, b_steady, atol=atol, rtol=rtol, equal_nan=False):
            max_abs = float(np.max(np.abs(a_steady - b_steady)))
            first_diff = int(np.argmax(np.abs(a_steady - b_steady)))
            raise EquivalenceFailure(
                f"stream {name!r}: max |diff| = {max_abs:.3e} "
                f"(atol={atol}, rtol={rtol}); first divergence at "
                f"steady-state sample {first_diff} "
                f"(refrain={a_steady[first_diff]!r}, baseline={b_steady[first_diff]!r})"
            )
    return EquivalenceReport(
        passed=True,
        streams_checked=names,
        warmup_samples=warmup_samples,
        atol=atol,
        rtol=rtol,
    )
