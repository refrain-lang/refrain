# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain command-line interface.

v0.0r1 subcommands:
  - `refrain check FILE`                       — parse-only validation
  - `refrain resolve FILE [--amp AMP.json]`    — parse + resolve + type check;
                                                 print the resolved IR or
                                                 a CRED-nf supplement
  - `refrain run FILE --input PATH | --synthetic [...]`
                                                — run the resolved protocol
                                                  against a recording or
                                                  synthetic source; emit
                                                  events to stdout / JSONL
  - `refrain fuzz FILE [--max-scenarios N]`     — auto-synthesise scenarios
                                                  from the IR, predict expected
                                                  behaviour, run the evaluator,
                                                  and report (see PROTOCOL-FUZZER)

The entry point is `main()`, wired in `pyproject.toml` as
`refrain = "refrain.cli:main"`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .amp_profile import AmpProfileError, load_amp_profile
from .compose import default_library_dirs, filesystem_loader
from .cost import estimate_cost
from .ir_print import print_cred_nf, print_ir
from .parser import ParseError, parse_file
from .resolver import ResolveError, resolve
from .synthetic import channels_for_synthetic

if TYPE_CHECKING:
    from .ir import IRProtocol


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

    # Build the parent loader from --library dirs and REFRAIN_LIBRARY_PATH.
    library_dirs = [Path(d) for d in (args.library or [])] + default_library_dirs()
    loader = filesystem_loader(library_dirs) if library_dirs else None

    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    try:
        ir = resolve(file_ast, amp, parent_loader=loader)
    except ResolveError as exc:
        print(f"error: {path}: resolve failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    if args.print == "cred-nf":
        sys.stdout.write(print_cred_nf(ir))
    else:
        sys.stdout.write(print_ir(ir))
    return 0


def _cmd_cost(args: argparse.Namespace) -> int:
    """Parse + resolve, then print a PROVISIONAL runtime-cost estimate and a
    per-tier real-time-factor projection (an authoring-time complexity hint)."""
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

    library_dirs = [Path(d) for d in (args.library or [])] + default_library_dirs()
    loader = filesystem_loader(library_dirs) if library_dirs else None

    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    try:
        ir = resolve(file_ast, amp, parent_loader=loader)
    except ResolveError as exc:
        print(f"error: {path}: resolve failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    rep = estimate_cost(ir, sample_rate_hz=args.sample_rate)

    out = sys.stdout
    out.write(f"cost estimate: {rep.protocol}  "
              f"({rep.sample_rate_hz:g} Hz, {rep.n_channels} ch)\n")
    out.write("  PROVISIONAL heuristic — coefficients from a single benchmark run, "
              "pending calibration.\n\n")
    out.write(f"  {'driver':<26}{'est. us/sample':>16}\n")
    for d in sorted(rep.drivers, key=lambda d: d.us_per_sample, reverse=True):
        flag = "" if d.calibrated else "  (uncalibrated)"
        out.write(f"  {d.name:<26}{d.us_per_sample:>16,.2f}{flag}\n")
        out.write(f"      {d.detail}\n")
    out.write(f"  {'TOTAL':<26}{rep.total_us_per_sample:>16,.2f}\n\n")

    out.write("  projected real-time factor (RTF; < 1.0 keeps up):\n")
    for tier, rtf in rep.rtf_by_tier.items():
        verdict = "OK" if rtf < 0.5 else ("tight" if rtf < 1.0 else "EXCEEDS")
        out.write(f"    {tier:<16}{rtf:>8.3f}   {verdict}\n")

    if rep.warnings:
        out.write("\n")
        for w in rep.warnings:
            out.write(f"  ! {w}\n")
    if rep.any_uncalibrated:
        out.write("\n  note: some drivers use uncalibrated coefficients "
                  "(coherence/bandpower); treat those as rough.\n")
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
    resolve_cmd.add_argument(
        "--library",
        action="append",
        default=[],
        metavar="DIR",
        help=(
            "Directory to search for `extends`-referenced parent protocols. "
            "Repeatable; first match wins. The REFRAIN_LIBRARY_PATH env var "
            "is also consulted (`:`-separated, like PATH)."
        ),
    )
    resolve_cmd.set_defaults(func=_cmd_resolve)

    # `refrain cost`
    cost_cmd = sub.add_parser(
        "cost",
        help="Estimate runtime cost and project real-time factor per hardware tier.",
    )
    cost_cmd.add_argument("file", help="Path to the .refrain protocol file.")
    cost_cmd.add_argument(
        "--sample-rate", type=float, default=None, metavar="HZ",
        help="Sample rate to estimate for (default: the protocol's chosen rate).",
    )
    cost_cmd.add_argument("--amp", default=None, help="Path to amp-profile JSON.")
    cost_cmd.add_argument(
        "--library", action="append", default=[], metavar="DIR",
        help="Library search dir for `extends`-referenced parents.",
    )
    cost_cmd.set_defaults(func=_cmd_cost)

    # `refrain run`
    run_cmd = sub.add_parser(
        "run",
        help="Run the resolved protocol against a recording or synthetic source.",
    )
    run_cmd.add_argument("file", help="Path to the .refrain protocol file.")
    src_group = run_cmd.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "--input",
        metavar="PATH",
        help="Input recording (auto-detected: .fif/.edf/.xdf).",
    )
    src_group.add_argument(
        "--synthetic",
        action="store_true",
        help="Use a deterministic synthetic source (pink noise + scheduled SMR bursts).",
    )
    run_cmd.add_argument(
        "--stream", metavar="NAME",
        help="XDF stream name (when --input is an XDF with multiple streams).",
    )
    run_cmd.add_argument(
        "--duration", metavar="SECONDS", type=float, default=60.0,
        help="Synthetic-source duration in seconds (default: 60).",
    )
    run_cmd.add_argument(
        "--smr-bursts", metavar="N", type=int, default=3,
        help="Number of evenly-spaced 3-second SMR bursts to schedule (synthetic).",
    )
    run_cmd.add_argument(
        "--amp", help="Path to amp-profile JSON.", default=None,
    )
    run_cmd.add_argument(
        "--library", action="append", default=[], metavar="DIR",
        help="Library search dir for `extends`-referenced parents.",
    )
    run_cmd.add_argument(
        "--output", metavar="PATH",
        help="Write events as JSON-lines to PATH (default: print summary only).",
    )
    run_cmd.add_argument(
        "--chunk-size", type=int, default=64,
        help="Samples per evaluator step (default: 64 = 250 ms at 256 Hz).",
    )
    run_cmd.set_defaults(func=_cmd_run)

    # `refrain fuzz`
    fuzz_cmd = sub.add_parser(
        "fuzz",
        help="Generate a directed scenario corpus, run the evaluator, and "
             "check it against an independent semantics-derived oracle.",
    )
    fuzz_cmd.add_argument("file", help="Path to the .refrain protocol file.")
    fuzz_cmd.add_argument(
        "--max-scenarios", type=int, default=0, metavar="N",
        help="Cap the corpus at the first N scenarios (0 = no cap).",
    )
    fuzz_cmd.add_argument(
        "--chunk-size", type=int, default=64,
        help="Samples per evaluator step (default: 64).",
    )
    fuzz_cmd.add_argument(
        "--amp", help="Path to amp-profile JSON.", default=None,
    )
    fuzz_cmd.add_argument(
        "--library", action="append", default=[], metavar="DIR",
        help="Library search dir for `extends`-referenced parents.",
    )
    fuzz_cmd.set_defaults(func=_cmd_fuzz)

    return p


def _cmd_run(args: argparse.Namespace) -> int:
    """Run an evaluator against the chosen source."""
    from .eval_ import eval_protocol
    from .sources import SourceError, SyntheticSource, open_source
    from .synthetic import SignalGenerator

    path = Path(args.file)
    if not path.exists():
        print(f"error: {path}: no such file", file=sys.stderr)
        return 2

    # Amp profile (optional).
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

    library_dirs = [Path(d) for d in (args.library or [])] + default_library_dirs()
    loader = filesystem_loader(library_dirs) if library_dirs else None

    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    try:
        ir = resolve(file_ast, amp, parent_loader=loader)
    except ResolveError as exc:
        print(f"error: {path}: resolve failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    # Source
    if args.synthetic:
        # Pick channels matching the protocol's requires + the reference
        # electrodes its montages need.
        channels = channels_for_synthetic(ir)
        bursts = _scheduled_bursts(args.duration, args.smr_bursts)
        gen = SignalGenerator(
            sample_rate_hz=int(amp.sample_rates_hz[-1]) if amp else 256,
            channels=channels,
            bursts=bursts,
            seed=42,
        )
        source = SyntheticSource(gen, duration_s=args.duration)
        source_label = f"synthetic ({args.duration:g}s, {args.smr_bursts} bursts)"
    else:
        try:
            source = open_source(args.input, stream_name=args.stream)
        except SourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        source_label = args.input

    # Run.
    out_file = open(args.output, "w") if args.output else None
    n_events = {"value": 0, "event": 0}
    by_channel: dict[str, int] = {}
    try:
        for ev in eval_protocol(ir, source, chunk_size=args.chunk_size):
            n_events[ev.kind] = n_events.get(ev.kind, 0) + 1
            by_channel[ev.channel] = by_channel.get(ev.channel, 0) + 1
            if out_file is not None:
                out_file.write(json.dumps({
                    "t": ev.timestamp_s,
                    "channel": ev.channel,
                    "kind": ev.kind,
                    "value": ev.value,
                }) + "\n")
    finally:
        if out_file is not None:
            out_file.close()

    # Summary on stderr (so stdout stays JSONL-only when --output is unused).
    duration_s = source.n_samples / source.sample_rate_hz if source.n_samples > 0 else 0
    print(f"protocol: {ir.name}", file=sys.stderr)
    print(f"source:   {source_label}", file=sys.stderr)
    print(f"duration: {duration_s:.1f}s, {source.sample_rate_hz} Hz", file=sys.stderr)
    print(f"events:   {n_events['event']} discrete, {n_events['value']} value summaries", file=sys.stderr)
    for ch in sorted(by_channel):
        print(f"  {ch}: {by_channel[ch]}", file=sys.stderr)
    return 0


def _scheduled_bursts(duration_s: float, n_bursts: int):
    """Schedule N evenly-spaced 3-second SMR bursts within `duration_s`.

    Bursts inject at 13.5 Hz (mid-SMR) at 25 µV peak on the first channel.
    """
    from .synthetic import SMRBurst

    if n_bursts <= 0:
        return ()
    out = []
    spacing = duration_s / (n_bursts + 1)
    for i in range(n_bursts):
        start = spacing * (i + 1) - 1.5
        out.append(SMRBurst(
            start_s=max(0.0, start),
            end_s=start + 3.0,
            center_hz=13.5,
            amplitude_uv=25.0,
        ))
    return tuple(out)


def _cmd_fuzz(args: argparse.Namespace) -> int:
    """Generate the directed scenario corpus, run each scenario through the
    evaluator, check it against the independent oracle, and render a report."""
    return _fuzz_single(args.file, args)


def _fuzz_single(path: str, args: argparse.Namespace) -> int:
    """Fuzz a single protocol file and map the outcome to an exit code."""
    from .fuzz.check import VacuityError
    from .fuzz.runner import FUZZED, SKIPPED, fuzz_protocol

    args = argparse.Namespace(**{**vars(args), "file": path})
    ir = _parse_resolve_or_report(args)
    if isinstance(ir, int):
        return ir
    try:
        outcome = fuzz_protocol(
            ir, path=path, max_scenarios=args.max_scenarios, chunk_size=args.chunk_size,
        )
    except VacuityError as exc:
        print(f"GENERATOR BUG: {exc}", file=sys.stderr)
        return 2
    if outcome.status == SKIPPED:
        print(f"SKIPPED (unsupported: {outcome.reason})")
        return 0
    print(outcome.report, file=sys.stderr)            # FUZZED
    return 0 if outcome.passed else 1


def _parse_resolve_or_report(args: argparse.Namespace) -> IRProtocol | int:
    """Resolve `args.file` to an IRProtocol, or return an int exit code after
    printing a diagnostic (mirrors `_cmd_run`'s error handling). The caller
    dispatches on `isinstance(result, int)` to detect the error path."""
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

    library_dirs = [Path(d) for d in (args.library or [])] + default_library_dirs()
    loader = filesystem_loader(library_dirs) if library_dirs else None

    try:
        file_ast = parse_file(path)
    except ParseError as exc:
        print(f"error: {path}: parse failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2
    try:
        return resolve(file_ast, amp, parent_loader=loader)
    except ResolveError as exc:
        print(f"error: {path}: resolve failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
