"""Smoke test for bench/ package layout. All other bench tests depend on these imports working."""

import importlib


def test_bench_package_imports():
    importlib.import_module("bench")
    importlib.import_module("bench.harness")
    importlib.import_module("bench.baselines")


def test_bench_protocols_dir_exists():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent.parent
    assert (repo / "bench" / "protocols").is_dir()
