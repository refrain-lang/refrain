# Refrain benchmark suite

This directory holds the performance benchmark suite for the Refrain
reference evaluator. See
[`docs/superpowers/specs/2026-05-19-performance-benchmark-design.md`](../docs/superpowers/specs/2026-05-19-performance-benchmark-design.md)
for design, metrics, and methodology.

Phase P1 (this commit set) ships harness + equivalence; timing measurements
land in P2.

Run the equivalence audit:

    python -m bench equivalence
