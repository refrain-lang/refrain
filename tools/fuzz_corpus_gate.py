# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Task-0 gate: run the whole protocol corpus under the metamorphic tier across
several fixed seeds on a known-good engine, and require ZERO metamorphic
violations and ZERO hollow passes.

A hollow pass is a protocol that reports FUZZED while asserting nothing; the
runner raises VacuityError for that, which `run_batch` classifies as an ERRORED
outcome whose reason starts with "generator-bug:". This harness counts those
separately from parse/resolve/eval errors, which are pre-existing corpus gaps
(coherence, bandpower, montage) and are NOT gate failures.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from refrain.compose import default_library_dirs, filesystem_loader  # noqa: E402
from refrain.fuzz.runner import (  # noqa: E402
    ERRORED,
    FUZZED,
    SKIPPED,
    run_batch,
)
from refrain.parser import ParseError, parse_file  # noqa: E402
from refrain.resolver import ResolveError, resolve  # noqa: E402


def _resolver(library_dirs):
    loader = filesystem_loader(library_dirs) if library_dirs else None

    def resolve_fn(path):
        try:
            return resolve(parse_file(Path(path)), None, parent_loader=loader)
        except (ParseError, ResolveError) as exc:
            return (str(exc).splitlines() or ["error"])[0][:80]

    return resolve_fn


def main() -> int:
    ap = argparse.ArgumentParser(description="Metamorphic-tier corpus gate")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--seeds", default="41,42,43,44,45")
    ap.add_argument("--chunk-size", type=int, default=64)
    ap.add_argument("--library", action="append", default=[])
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    library_dirs = [Path(d) for d in args.library] + default_library_dirs()
    resolve_fn = _resolver(library_dirs)

    total_violations = 0
    total_generator_bugs = 0
    for seed in seeds:
        t0 = time.perf_counter()
        outcomes = run_batch(
            [args.corpus], max_scenarios=0, chunk_size=args.chunk_size,
            resolve_fn=resolve_fn, seed=seed,
        )
        dt = time.perf_counter() - t0
        violations = [o for o in outcomes if o.status == FUZZED and o.passed is False]
        gen_bugs = [o for o in outcomes if o.status == ERRORED
                    and (o.reason or "").startswith("generator-bug:")]
        other_err = [o for o in outcomes if o.status == ERRORED and o not in gen_bugs]
        fuzzed = [o for o in outcomes if o.status == FUZZED]
        skipped = [o for o in outcomes if o.status == SKIPPED]
        total_violations += len(violations)
        total_generator_bugs += len(gen_bugs)

        n_errored = len(outcomes) - len(fuzzed) - len(skipped)
        print(f"\n=== seed {seed}  ({dt:.1f}s) ===")
        print(f"  fuzzed {len(fuzzed)} / skipped {len(skipped)} / errored {n_errored}")
        print(f"  VIOLATIONS:     {len(violations)}")
        print(f"  generator-bugs: {len(gen_bugs)}   (hollow passes — must be 0)")
        print(f"  other errors:   {len(other_err)}  (pre-existing corpus gaps, not a gate failure)")
        for o in violations:
            print(f"    [VIOLATION] {o.path}")
        for o in gen_bugs:
            print(f"    [HOLLOW]    {o.path}: {o.reason}")

    print(f"\n=== GATE ===\n  violations across {len(seeds)} seeds: {total_violations}"
          f"\n  hollow passes: {total_generator_bugs}")
    ok = total_violations == 0 and total_generator_bugs == 0
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
