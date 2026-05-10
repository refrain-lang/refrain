"""Smoke tests for the grammar — verifies grammar.lark loads and basic
constructs parse. Real parser tests come once parser.py is wired up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

lark = pytest.importorskip("lark")
GRAMMAR_PATH = Path(__file__).resolve().parent.parent / "src" / "refrain" / "grammar.lark"


def _parser() -> "lark.Lark":
    return lark.Lark.open(str(GRAMMAR_PATH), start="start", parser="earley")


def test_grammar_loads() -> None:
    """The Lark grammar should parse without syntax errors."""
    _parser()


def test_minimal_protocol_parses() -> None:
    """The smallest possible protocol should parse cleanly."""
    src = """
    protocol "minimal" {
      meta {
        version = "0.1.0"
      }
    }
    """
    tree = _parser().parse(src)
    assert tree is not None


def test_smr_cz_example_parses() -> None:
    """The SMR Cz example file should parse cleanly. This is the round-trip
    target for v0.0 — if this passes, the grammar covers the Phase 0 SMR
    protocol surface.
    """
    example_path = (
        Path(__file__).resolve().parent.parent / "examples" / "smr_cz.refrain"
    )
    src = example_path.read_text()
    tree = _parser().parse(src)
    assert tree is not None
