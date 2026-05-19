"""Chunked runner: drives a step() callable, collects outputs and latencies."""

from __future__ import annotations

import numpy as np

from bench.harness.runner import ChunkedRunner, RunResult


class _DoubleIt:
    """Trivial step() callable: doubles input, emits as 'x' stream."""

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        return {"x": raw_chunk[:, 0] * 2.0}


def test_runner_returns_concatenated_streams():
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((1024, 1))
    runner = ChunkedRunner(chunk_size=32)
    result = runner.run(_DoubleIt(), input_signal)
    assert isinstance(result, RunResult)
    assert result.streams["x"].shape == (1024,)
    np.testing.assert_array_equal(result.streams["x"], input_signal[:, 0] * 2.0)


def test_runner_records_one_latency_per_chunk():
    runner = ChunkedRunner(chunk_size=32)
    input_signal = np.zeros((256, 1))
    result = runner.run(_DoubleIt(), input_signal)
    expected_chunks = 256 // 32
    assert len(result.per_chunk_ns) == expected_chunks
    assert all(t > 0 for t in result.per_chunk_ns)


def test_runner_rejects_non_divisible_length():
    runner = ChunkedRunner(chunk_size=32)
    input_signal = np.zeros((100, 1))
    try:
        runner.run(_DoubleIt(), input_signal)
    except ValueError as exc:
        assert "chunk_size" in str(exc)
    else:
        raise AssertionError("expected ValueError on non-divisible length")


def test_runner_passes_correct_chunk_shape():
    seen_shapes: list[tuple[int, int]] = []

    class _Spy:
        def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
            seen_shapes.append(raw_chunk.shape)
            return {"x": raw_chunk[:, 0]}

    runner = ChunkedRunner(chunk_size=64)
    runner.run(_Spy(), np.zeros((256, 2)))
    assert seen_shapes == [(64, 2), (64, 2), (64, 2), (64, 2)]
