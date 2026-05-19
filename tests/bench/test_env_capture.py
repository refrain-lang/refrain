"""Env capture: required keys present, no crashes on missing optional info."""

from __future__ import annotations

from bench.harness.env_capture import capture_env


REQUIRED_KEYS = (
    "python_version",
    "platform",
    "cpu",
    "numpy_version",
    "scipy_version",
    "refrain_version",
    "git_sha",
)


def test_capture_env_returns_required_keys():
    env = capture_env()
    for key in REQUIRED_KEYS:
        assert key in env, f"missing required key: {key}"


def test_capture_env_values_are_strings_or_none():
    env = capture_env()
    for key, val in env.items():
        assert val is None or isinstance(val, str), f"{key} is {type(val).__name__}"


def test_capture_env_records_git_sha_format():
    env = capture_env()
    sha = env["git_sha"]
    if sha is not None:
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha.lower())
