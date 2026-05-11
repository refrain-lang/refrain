# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Input sources for the evaluator.

A `Source` is anything that produces chunked, multi-channel EEG samples:
recordings (FIF / EDF / XDF) and synthetic signals share the same
interface so the evaluator doesn't care which is which.

  - FifSource — mne.io.read_raw_fif
  - EdfSource — mne.io.read_raw_edf
  - XdfSource — pyxdf.load_xdf, selects first EEG-typed stream by
    default; `--stream NAME` chooses among multiple streams
  - SyntheticSource — driven by a `SignalGenerator` (see `synthetic.py`)

Format detection: `open_source(path)` dispatches on extension. Tests
inject a concrete Source directly.

mne and pyxdf are imported lazily so the parser-only install (without
the `[eval]` extra) still works.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

import numpy as np


class SourceError(Exception):
    """Raised when an input source can't be opened or read."""


class Source(ABC):
    """A multi-channel EEG sample stream.

    Implementations expose a fixed sample rate and channel layout, and
    yield chunks of shape `(n_samples, n_channels)` from `iter_chunks`.
    """

    @property
    @abstractmethod
    def sample_rate_hz(self) -> float: ...

    @property
    @abstractmethod
    def channel_names(self) -> tuple[str, ...]: ...

    @property
    @abstractmethod
    def n_samples(self) -> int:
        """Total number of samples available, or -1 for unbounded sources."""

    @abstractmethod
    def iter_chunks(self, chunk_size: int) -> Iterator[np.ndarray]:
        """Yield `(chunk_size, n_channels)` float64 arrays.

        The final chunk may be shorter than `chunk_size` if the source
        is finite and its total length isn't a multiple. Implementations
        must close any underlying resources when the iterator is
        exhausted (or when GC'd).
        """


# ---------------------------------------------------------------------------
# FIF
# ---------------------------------------------------------------------------


class FifSource(Source):
    """Wraps `mne.io.read_raw_fif`."""

    def __init__(self, path: str | Path):
        try:
            import mne  # noqa: PLC0415  (lazy import)
        except ImportError as exc:
            raise SourceError(
                "FIF support requires `mne`; install with `pip install refrain[eval]`"
            ) from exc
        try:
            self._raw = mne.io.read_raw_fif(str(path), preload=True, verbose="ERROR")
        except OSError as exc:
            raise SourceError(f"{path}: {exc}") from exc
        self._path = Path(path)

    @property
    def sample_rate_hz(self) -> float:
        return float(self._raw.info["sfreq"])

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(self._raw.ch_names)

    @property
    def n_samples(self) -> int:
        return int(self._raw.n_times)

    def iter_chunks(self, chunk_size: int) -> Iterator[np.ndarray]:
        # Shape: (n_channels, n_samples). MNE keeps channels-first; we
        # transpose to (n_samples, n_channels) per the Source contract.
        data = self._raw.get_data().T.astype(np.float64, copy=False)
        for start in range(0, data.shape[0], chunk_size):
            yield data[start : start + chunk_size]


# ---------------------------------------------------------------------------
# EDF
# ---------------------------------------------------------------------------


class EdfSource(Source):
    """Wraps `mne.io.read_raw_edf`."""

    def __init__(self, path: str | Path):
        try:
            import mne  # noqa: PLC0415
        except ImportError as exc:
            raise SourceError(
                "EDF support requires `mne`; install with `pip install refrain[eval]`"
            ) from exc
        try:
            self._raw = mne.io.read_raw_edf(str(path), preload=True, verbose="ERROR")
        except (OSError, ValueError) as exc:
            raise SourceError(f"{path}: {exc}") from exc
        self._path = Path(path)

    @property
    def sample_rate_hz(self) -> float:
        return float(self._raw.info["sfreq"])

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(self._raw.ch_names)

    @property
    def n_samples(self) -> int:
        return int(self._raw.n_times)

    def iter_chunks(self, chunk_size: int) -> Iterator[np.ndarray]:
        data = self._raw.get_data().T.astype(np.float64, copy=False)
        for start in range(0, data.shape[0], chunk_size):
            yield data[start : start + chunk_size]


# ---------------------------------------------------------------------------
# XDF (LabStreamingLayer)
# ---------------------------------------------------------------------------


class XdfSource(Source):
    """Wraps `pyxdf.load_xdf`.

    An XDF file may contain multiple streams (EEG, markers, gaze, etc.).
    By default this source picks the first stream whose `type` is `EEG`.
    Use the `stream_name` argument to override (matches the stream's
    `name` field).
    """

    def __init__(self, path: str | Path, *, stream_name: str | None = None):
        try:
            import pyxdf  # noqa: PLC0415
        except ImportError as exc:
            raise SourceError(
                "XDF support requires `pyxdf`; install with `pip install refrain[eval]`"
            ) from exc
        try:
            streams, _header = pyxdf.load_xdf(str(path), verbose=False)
        except Exception as exc:  # noqa: BLE001 — pyxdf raises plain RuntimeError
            raise SourceError(f"{path}: {exc}") from exc
        chosen = _pick_xdf_stream(streams, stream_name, path)

        info = chosen["info"]
        self._path = Path(path)
        self._sample_rate = float(info["nominal_srate"][0])
        # Extract channel labels from the optional `desc.channels.channel.label` tree.
        labels: list[str] = []
        try:
            ch_list = info["desc"][0]["channels"][0]["channel"]
            labels = [c.get("label", [f"ch{i}"])[0] for i, c in enumerate(ch_list)]
        except (KeyError, IndexError, TypeError):
            n_ch = int(info["channel_count"][0])
            labels = [f"ch{i}" for i in range(n_ch)]
        self._channel_names = tuple(labels)
        # time_series shape: (n_samples, n_channels). Already in our convention.
        self._data = np.asarray(chosen["time_series"], dtype=np.float64)
        if self._data.ndim == 1:
            self._data = self._data[:, None]

    @property
    def sample_rate_hz(self) -> float:
        return self._sample_rate

    @property
    def channel_names(self) -> tuple[str, ...]:
        return self._channel_names

    @property
    def n_samples(self) -> int:
        return int(self._data.shape[0])

    def iter_chunks(self, chunk_size: int) -> Iterator[np.ndarray]:
        for start in range(0, self._data.shape[0], chunk_size):
            yield self._data[start : start + chunk_size]


def _pick_xdf_stream(streams: list[dict], stream_name: str | None, path) -> dict:
    if not streams:
        raise SourceError(f"{path}: contains no streams")
    if stream_name is not None:
        for s in streams:
            name = s["info"]["name"][0] if s["info"]["name"] else ""
            if name == stream_name:
                return s
        names = [s["info"]["name"][0] if s["info"]["name"] else "?" for s in streams]
        raise SourceError(
            f"{path}: no stream named {stream_name!r}; available: {names}"
        )
    # Default: first EEG-typed stream.
    for s in streams:
        stype = s["info"]["type"][0] if s["info"]["type"] else ""
        if stype.upper() == "EEG":
            return s
    # Fall back to the first stream.
    return streams[0]


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------


class SyntheticSource(Source):
    """Wraps a `SignalGenerator` from `refrain.synthetic`.

    Construction:
        from refrain.synthetic import SignalGenerator
        gen = SignalGenerator(sample_rate_hz=256, channels=("Cz",), seed=42)
        src = SyntheticSource(gen, duration_s=60)
    """

    def __init__(self, generator, duration_s: float):
        self._generator = generator
        self._duration_s = duration_s
        self._n_samples = int(round(duration_s * generator.sample_rate_hz))

    @property
    def sample_rate_hz(self) -> float:
        return float(self._generator.sample_rate_hz)

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(self._generator.channels)

    @property
    def n_samples(self) -> int:
        return self._n_samples

    def iter_chunks(self, chunk_size: int) -> Iterator[np.ndarray]:
        # The generator owns the time cursor; we just pull chunks until done.
        remaining = self._n_samples
        while remaining > 0:
            size = min(chunk_size, remaining)
            yield self._generator.next_chunk(size)
            remaining -= size


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


_EXTENSIONS = {
    ".fif": "fif",
    ".edf": "edf",
    ".xdf": "xdf",
}


def open_source(
    path: str | Path,
    *,
    stream_name: str | None = None,
) -> Source:
    """Dispatch on extension to the right `Source` implementation."""
    p = Path(path)
    ext = p.suffix.lower()
    fmt = _EXTENSIONS.get(ext)
    if fmt == "fif":
        return FifSource(p)
    if fmt == "edf":
        return EdfSource(p)
    if fmt == "xdf":
        return XdfSource(p, stream_name=stream_name)
    raise SourceError(
        f"{p}: unrecognised extension {ext!r}; expected one of {sorted(_EXTENSIONS)}"
    )


__all__ = [
    "EdfSource",
    "FifSource",
    "Source",
    "SourceError",
    "SyntheticSource",
    "XdfSource",
    "open_source",
]
