# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain command-line interface.

v0.0r1 ships one subcommand: `refrain check FILE` — parse-only validation.

Subcommands to land in later sessions:
  - `refrain resolve FILE [--amp AMP_PROFILE.json]`  (Session 2)
  - `refrain run FILE [--input recording.fif]`       (Session 3)
  - `refrain export cred-nf FILE`                    (Session 3+)

The entry point is `main()`, wired in `pyproject.toml` as
`refrain = "refrain.cli:main"`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .parser import ParseError, parse_file


def _cmd_check(args: argparse.Namespace) -> int:
    """Parse the file. Exit 0 with a one-line OK; exit 1 with a diagnostic
    that includes line/col when the parser can extract them."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: {path}: no such file", file=sys.stderr)
        return 2
    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        # ParseError wraps Lark's error message which already includes
        # line/col for syntax errors. Print it on stderr.
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    n_stmts = len(file_ast.protocol.body)
    n_imports = len(file_ast.imports)
    print(f"OK: {path}: protocol {file_ast.protocol.name!r} ({n_imports} imports, {n_stmts} top-level statements)")
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="refrain",
        description="Refrain language tools (v0.0r1).",
    )
    p.add_argument("--version", action="version", version=f"refrain {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Parse a .refrain file and report status.")
    check.add_argument("file", help="Path to the .refrain protocol file.")
    check.set_defaults(func=_cmd_check)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
