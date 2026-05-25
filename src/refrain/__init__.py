# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain — an open description language for clinical neurofeedback protocols.

A `.refrain` file declares a hardware montage, DSP pipeline, threshold logic,
reward/inhibit expressions, output bindings, clinician controls, and session
phases. This Python reference implementation parses a protocol, resolves it
against an amplifier profile into a typed IR (`refrain.resolver.resolve`), runs
it live or offline (`refrain.eval_.Evaluator`), and emits portable IR-JSON for
the Rust core. See `docs/SPEC.md` for the language reference, `docs/TOUR.md` for
an introduction, and `docs/EMBEDDING.md` for host integration.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from . import ast
from .parser import ParseError, parse, parse_file
from .unparser import unparse

try:
    # Single source of truth: the installed distribution's metadata (from
    # pyproject). A built wheel always reports its real version; an editable
    # dev install may lag until `pip install -e .` is re-run.
    __version__ = _pkg_version("refrain")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.4.0"

__all__ = [
    "__version__",
    "ast",
    "parse",
    "parse_file",
    "unparse",
    "ParseError",
]
