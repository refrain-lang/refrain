# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Typed errors for the protocol fuzzer."""
from __future__ import annotations


class UnsupportedProtocol(Exception):  # noqa: N818
    """A protocol shape the fuzzer cannot yet represent.

    `reason` is a short, stable, feature-mapped string used for the batch
    skip breakdown and the coverage metric (e.g. "single-condition reward").
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = ["UnsupportedProtocol"]
