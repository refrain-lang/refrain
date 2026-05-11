# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain — an open description language for clinical neurofeedback protocols.

v0.0r1 reference implementation. This phase exposes parser + AST only;
the resolver, type checker, IR, evaluator, and runtime arrive in later
phases. See `docs/SPEC.md` for the language reference and `docs/TOUR.md`
for an introduction.
"""

from . import ast
from .parser import ParseError, parse, parse_file
from .unparser import unparse

__version__ = "0.0.0"

__all__ = [
    "__version__",
    "ast",
    "parse",
    "parse_file",
    "unparse",
    "ParseError",
]
