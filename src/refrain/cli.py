# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain command-line interface.

v0.0r1 subcommands:
  - `refrain check FILE`                       — parse-only validation
  - `refrain resolve FILE [--amp AMP.json]`    — parse + resolve + type check;
                                                 print the resolved IR or
                                                 a CRED-nf supplement

Reserved for later sessions:
  - `refrain run FILE [--input recording.fif]` (Session 3)
  - `refrain export cred-nf FILE` (will alias `resolve --print cred-nf`)

The entry point is `main()`, wired in `pyproject.toml` as
`refrain = "refrain.cli:main"`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .amp_profile import AmpProfileError, load_amp_profile
from .ir_print import print_cred_nf, print_ir
from .parser import ParseError, parse_file
from .resolver import ResolveError, resolve


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
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    n_stmts = len(file_ast.protocol.body)
    n_imports = len(file_ast.imports)
    print(f"OK: {path}: protocol {file_ast.protocol.name!r} ({n_imports} imports, {n_stmts} top-level statements)")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    """Parse + resolve. Print the IR or CRED-nf supplement based on --print."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: {path}: no such file", file=sys.stderr)
        return 2

    amp = None
    if args.amp is not None:
        amp_path = Path(args.amp)
        if not amp_path.exists():
            print(f"error: {amp_path}: no such amp-profile file", file=sys.stderr)
            return 2
        try:
            amp = load_amp_profile(amp_path)
        except AmpProfileError as exc:
            print(f"error: {amp_path}: {exc}", file=sys.stderr)
            return 1

    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    try:
        ir = resolve(file_ast, amp)
    except ResolveError as exc:
        print(f"error: {path}: resolve failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    if args.print == "cred-nf":
        sys.stdout.write(print_cred_nf(ir))
    else:
        sys.stdout.write(print_ir(ir))
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

    resolve_cmd = sub.add_parser(
        "resolve",
        help="Parse, resolve and type-check; print IR or CRED-nf supplement.",
    )
    resolve_cmd.add_argument("file", help="Path to the .refrain protocol file.")
    resolve_cmd.add_argument(
        "--amp",
        help=(
            "Path to an amp-profile JSON file. "
            "If omitted, hardware checks are skipped and the chosen sample "
            "rate falls back to the protocol's minimum."
        ),
        default=None,
    )
    resolve_cmd.add_argument(
        "--print",
        choices=["ir", "cred-nf"],
        default="ir",
        help="Output format: `ir` (default) or `cred-nf` (markdown table per SPEC §8).",
    )
    resolve_cmd.set_defaults(func=_cmd_resolve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
