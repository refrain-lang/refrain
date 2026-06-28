"""Shared fixture: parse + resolve the smr_cz benchmark protocol."""
from __future__ import annotations

from pathlib import Path

from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]
SMR_PROTOCOL = REPO_ROOT / "bench" / "protocols" / "realistic_smr.refrain"


def resolved_smr_ir():
    """Return the resolved IR for the SMR protocol with no amp profile."""
    file_ast = parse_file(SMR_PROTOCOL)
    return resolve(file_ast, amp=None, parent_loader=None)
