"""Chunked runner: drives a step() callable, captures stream outputs and
per-chunk timing.

The callable contract is `step(raw_chunk: np.ndarray) -> dict[str, np.ndarray]`.
The runner concatenates per-chunk outputs into full-length streams (one
1-D array per stream name) and records `time.perf_counter_ns` deltas per
chunk. No statistical aggregation happens here — every chunk's latency is
preserved so downstream code can compute P50/P95/P99/P99.9 honestly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class StepCallable(Protocol):
    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        ...


@dataclass(frozen=True)
class RunResult:
    streams: dict[str, np.ndarray]      # name -> full-length 1-D array
    per_chunk_ns: tuple[int, ...]       # one wall-clock measurement per chunk


class ChunkedRunner:
    def __init__(self, *, chunk_size: int):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    def run(self, callable_: StepCallable, input_signal: np.ndarray) -> RunResult:
        if input_signal.ndim != 2:
            raise ValueError(
                f"input_signal must be 2D (n_samples, n_channels); got shape {input_signal.shape}"
            )
        total_samples = input_signal.shape[0]
        if total_samples % self.chunk_size != 0:
            raise ValueError(
                f"input_signal length {total_samples} is not divisible"
                f" by chunk_size {self.chunk_size}"
            )
        n_chunks = total_samples // self.chunk_size

        accumulated: dict[str, list[np.ndarray]] = {}
        per_chunk_ns: list[int] = []

        for i in range(n_chunks):
            start = i * self.chunk_size
            end = start + self.chunk_size
            chunk = input_signal[start:end]
            t0 = time.perf_counter_ns()
            out = callable_.step(chunk)
            t1 = time.perf_counter_ns()
            per_chunk_ns.append(t1 - t0)
            for name, arr in out.items():
                accumulated.setdefault(name, []).append(np.asarray(arr))

        streams = {name: np.concatenate(parts) for name, parts in accumulated.items()}
        return RunResult(streams=streams, per_chunk_ns=tuple(per_chunk_ns))
