"""Idiomatic numpy/scipy baselines, one per protocol. Each module exposes
a `Baseline` class with `step(raw_chunk) -> dict[str, np.ndarray]` matching
the corresponding Refrain protocol's stream outputs."""
