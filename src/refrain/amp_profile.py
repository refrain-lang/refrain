# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Amplifier capability profiles.

An `AmpProfile` describes what a connected amplifier supports: coupling
modes, available sample rates, channels, impedance check support, marker
support, and the runtime resource ceiling the protocol must stay under.

Profiles live as JSON files. v0.0r1 ships two stubs:
  - `amp_profiles/q21.json`       — Neurofield Q21, 21-channel research
  - `amp_profiles/openbci_cyton.json` — OpenBCI Cyton, 8-channel hobbyist

The resolver consumes a profile to validate a protocol's `requires`
block (SPEC §4.2, §6.3) and to compute resource-budget headroom
(SPEC §6.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


# Schema version. Bumped on backwards-incompatible field changes.
AMP_PROFILE_SCHEMA = "refrain-amp-profile/v0"

# Reference-operation keywords a montage may name. Mirrors
# `primitive_impls.REFERENCE_KEYWORDS`; duplicated to keep this module
# dependency-light (no scipy). Keep the two in sync.
AMP_REFERENCE_KEYWORDS = frozenset({"linked_ears", "common_average", "device"})


@dataclass(frozen=True, slots=True)
class Channel:
    """A physical or reference channel exposed by the amplifier."""

    name: str            # canonical label, e.g. "Cz", "T3", "A1"
    kind: str = "eeg"    # "eeg" | "reference" | "ground" | "marker" | "other"


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Per-protocol budget the runtime enforces. Used by SPEC §6.4 checks."""

    max_state_kb: int = 256
    max_worst_case_us_per_step: int = 1000


@dataclass(frozen=True, slots=True)
class AmpProfile:
    """A loaded amplifier profile."""

    model: str
    vendor: str
    firmware: str | None
    coupling: tuple[str, ...]            # subset of {"dc", "ac"}
    sample_rates_hz: tuple[int, ...]     # discrete rates the amp supports
    channels: tuple[Channel, ...]
    supports_impedance_check: bool
    supports_markers: bool
    max_simultaneous_channels: int
    adc_bits: int | None
    input_range_uv: float | None
    resource_limits: ResourceLimits
    reference: str | None = None

    # -- Conveniences -------------------------------------------------------

    def channel_names(self) -> set[str]:
        return {c.name for c in self.channels}

    def has_channel(self, name: str) -> bool:
        return any(c.name == name for c in self.channels)

    def supports_coupling(self, coupling: str) -> bool:
        return coupling in self.coupling

    def best_sample_rate_at_least(self, min_hz: float) -> int | None:
        """Highest supported rate that is >= `min_hz`. None if none qualifies."""
        candidates = [r for r in self.sample_rates_hz if r >= min_hz]
        return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class AmpProfileError(Exception):
    """Raised when a profile JSON fails schema validation."""


def load_amp_profile(path: str | Path) -> AmpProfile:
    """Load and validate an amp-profile JSON file."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AmpProfileError(f"{p}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise AmpProfileError(f"{p}: {exc}") from exc
    return _parse_amp_profile(data, source=str(p))


def _parse_amp_profile(data: dict, *, source: str) -> AmpProfile:
    schema = data.get("schema")
    if schema != AMP_PROFILE_SCHEMA:
        raise AmpProfileError(
            f"{source}: unknown profile schema {schema!r}; expected {AMP_PROFILE_SCHEMA!r}"
        )

    def required(key: str):
        if key not in data:
            raise AmpProfileError(f"{source}: missing required field {key!r}")
        return data[key]

    coupling = tuple(required("coupling"))
    if not all(c in ("dc", "ac") for c in coupling):
        raise AmpProfileError(f"{source}: coupling must be subset of [dc, ac], got {coupling}")

    sample_rates = tuple(int(r) for r in required("sample_rates_hz"))
    if not sample_rates:
        raise AmpProfileError(f"{source}: sample_rates_hz must not be empty")

    raw_channels = required("channels")
    channels: list[Channel] = []
    for ch in raw_channels:
        if isinstance(ch, str):
            channels.append(Channel(name=ch))
        else:
            channels.append(Channel(name=ch["name"], kind=ch.get("kind", "eeg")))

    limits_data = data.get("resource_limits", {})
    limits = ResourceLimits(
        max_state_kb=int(limits_data.get("max_state_kb", 256)),
        max_worst_case_us_per_step=int(limits_data.get("max_worst_case_us_per_step", 1000)),
    )

    reference = data.get("reference")
    if reference is not None:
        allowed = AMP_REFERENCE_KEYWORDS | {c.name for c in channels}
        if reference not in allowed:
            raise AmpProfileError(
                f"{source}: reference {reference!r} must be a reference keyword "
                f"{sorted(AMP_REFERENCE_KEYWORDS)} or a declared channel"
            )

    return AmpProfile(
        model=required("model"),
        vendor=required("vendor"),
        firmware=data.get("firmware"),
        coupling=coupling,
        sample_rates_hz=sample_rates,
        channels=tuple(channels),
        supports_impedance_check=bool(data.get("supports_impedance_check", False)),
        supports_markers=bool(data.get("supports_markers", False)),
        max_simultaneous_channels=int(data.get("max_simultaneous_channels", len(channels))),
        adc_bits=data.get("adc_bits"),
        input_range_uv=data.get("input_range_uv"),
        resource_limits=limits,
        reference=reference,
    )


__all__ = [
    "AMP_PROFILE_SCHEMA",
    "AmpProfile",
    "AmpProfileError",
    "Channel",
    "ResourceLimits",
    "load_amp_profile",
]
