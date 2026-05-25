# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Shared pytest configuration for the Refrain test suite.

Provides the ``backend`` fixture that controls which evaluator backend is
exercised by the behavioral (push-mode live) tests.

Usage
-----
Set the ``REFRAIN_EVAL_BACKEND`` environment variable before running pytest:

    REFRAIN_EVAL_BACKEND=rust pytest tests/test_eval_*.py

The default (env unset) is ``"python"``, so the full test suite continues to
run unchanged.  A test opts in to backend-parametrization by declaring
``backend`` as a parameter; the fixture threads it into the
``Evaluator.live(..., backend=backend)`` call.

Tests that exercise Python-only internals or pull-mode paths are not
parametrized.  Instead, they carry ``@pytest.mark.skipif(RUST_BACKEND_ACTIVE,
reason=...)`` so that under a rust run they appear as SKIPPED with an
explicit justification rather than failing.
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Module-level constants — read the env exactly once
# ---------------------------------------------------------------------------

EVAL_BACKEND: str = os.environ.get("REFRAIN_EVAL_BACKEND", "python")
RUST_BACKEND_ACTIVE: bool = (EVAL_BACKEND == "rust")


# ---------------------------------------------------------------------------
# backend fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> str:
    """Return the active evaluator backend name (``"python"`` or ``"rust"``).

    Tests that declare this fixture thread its return value into their
    ``Evaluator.live(..., backend=backend)`` call.

    When ``REFRAIN_EVAL_BACKEND=rust``, the fixture first verifies that the
    ``refrain_core`` wheel is installed; the test is *skipped* (not errored)
    when the wheel is absent, so CI passes gracefully on machines without a
    compiled wheel.
    """
    if EVAL_BACKEND not in ("python", "rust"):
        pytest.fail(
            f"REFRAIN_EVAL_BACKEND={EVAL_BACKEND!r} is not a valid backend. "
            "Valid values: 'python', 'rust'."
        )
    if EVAL_BACKEND == "rust":
        pytest.importorskip(
            "refrain_core",
            reason="refrain_core wheel not installed — build with: "
            "cd refrain-core && maturin develop --release",
        )
    return EVAL_BACKEND
