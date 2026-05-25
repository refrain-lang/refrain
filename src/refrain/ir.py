# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain intermediate representation.

The IR is what the resolver produces: a fully-typed, fully-resolved
protocol ready for the evaluator. Compared to the AST:

  - Every string-literal-in-expression-position has been classified as
    a stream reference, threshold reference, control reference, or
    plain value. (SPEC §5.4 said the grammar can't distinguish; the IR
    now records the classification.)
  - Every call has its `PrimitiveSpec` attached.
  - Every expression has its `StreamType` (or scalar `Dimensions`) typed.
  - The dataflow graph has been validated as a DAG and topologically
    ordered (canonical names like `"input/raw"`, `"derive/smr_envelope"`).
  - Composition (`extends`/`amend`/`remove`/`final`) has been applied.
    (Phase 0b ships the IR shape without composition; Phase 0c will
    wire that in.)
  - Hardware requirements have been satisfied against an amp profile,
    and the actual sample rate the runtime will use has been chosen.
  - The protocol's resource budget has been summed.

IR dataclasses are frozen at the field level. Dict fields hold resolved
maps; conventionally nobody mutates them after the resolver returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .amp_profile import AmpProfile
from .ast import Loc
from .primitives import PrimitiveSpec, ResourceBudget
from .types_ import Dimensions, StreamType


# ---------------------------------------------------------------------------
# Expression layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IRNode:
    """Base for every IR node. `loc` carries the source span for
    diagnostics. Excluded from equality/repr like the AST analogue."""

    loc: Loc | None = field(default=None, kw_only=True, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class IRExpr(IRNode):
    """Marker base for typed expressions."""


# Atoms ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IRNumberLit(IRExpr):
    value: float
    dims: Dimensions
    # Original surface unit (`"Hz"`, `"ms"`, `"min"`, ...) preserved
    # alongside the abstract `dims` so the printer can render
    # `250 ms` rather than the abstract `250 time`. None means a bare
    # numeric literal.
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class IRStringLit(IRExpr):
    """A genuine string value (not a reference). E.g. `description = "..."`."""

    value: str


@dataclass(frozen=True, slots=True)
class IRBoolLit(IRExpr):
    value: bool


@dataclass(frozen=True, slots=True)
class IRStreamRef(IRExpr):
    """Reference to a named stream — input or derive.

    `target` is the canonical name `"input/<name>"` or `"derive/<name>"`.
    `stream_type` is the referenced stream's type.
    """

    target: str
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRThresholdRef(IRExpr):
    """Reference to a `threshold "<name>"` declaration."""

    target: str        # canonical name "threshold/<name>"
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRControlRef(IRExpr):
    """Reference to a `controls.<name>` entry (used in expression position)."""

    target: str        # canonical name "control/<name>"
    dims: Dimensions


@dataclass(frozen=True, slots=True)
class IRRewardField(IRExpr):
    """`reward.continuous`, `reward.event`, or `reward.event.holds`."""

    field_path: str        # "continuous" | "event" | "event.holds"
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRArg(IRNode):
    """A typed argument inside a call."""

    name: str | None    # None for positional
    value: IRExpr


@dataclass(frozen=True, slots=True)
class IRCall(IRExpr):
    """A typed primitive (or custom) call."""

    callee: str
    primitive: PrimitiveSpec | None    # None for custom primitives
    args: tuple[IRArg, ...]
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRArray(IRExpr):
    elements: tuple[IRExpr, ...]


@dataclass(frozen=True, slots=True)
class IRTuple(IRExpr):
    elements: tuple[IRExpr, ...]


@dataclass(frozen=True, slots=True)
class IRBinaryOp(IRExpr):
    op: str
    left: IRExpr
    right: IRExpr
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRConditional(IRExpr):
    cond: IRExpr
    then_branch: IRExpr
    else_branch: IRExpr
    stream_type: StreamType


@dataclass(frozen=True, slots=True)
class IRBlockExpr(IRExpr):
    """`phase { ... }`, `frequency { ... }`, `voltage { ... }`, etc.

    `kind` is the leading identifier (`None` for anonymous records).
    `fields` is the resolved body keyed by field name.
    """

    kind: str | None
    fields: dict[str, IRExpr]


# ---------------------------------------------------------------------------
# Protocol-level shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IRInput:
    name: str
    canonical_name: str    # "input/<name>"
    stream_type: StreamType
    montage: IRCall        # the bipolar/referential/source_project call
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRDerive:
    name: str
    canonical_name: str    # "derive/<name>"
    stream_type: StreamType
    expression: IRExpr     # body of the derive — pipeline desugared or formula
    upstream: tuple[str, ...]  # canonical names this derive consumes
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRThreshold:
    name: str
    canonical_name: str    # "threshold/<name>"
    signal: str            # canonical name of the source stream
    threshold_call: IRCall # absolute() / percentile() / dynamic()
    stream_type: StreamType
    live_tunable: bool
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRInhibit:
    name: str
    canonical_name: str    # "inhibit/<name>"
    metric: IRExpr         # typically a bandpower() call
    threshold: IRCall      # absolute() / percentile() etc.
    action_kind: str       # "mute" | "freeze" | "flag"
    action_release_ms: float | None
    final: bool            # §11.4 — children cannot override or remove
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRCustom:
    name: str
    canonical_name: str    # "custom/<name>"
    module: str
    signature_str: str     # raw type string; full parsing deferred
    budget: ResourceBudget
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRReward:
    """`reward { continuous?, event? }`. At least one must be present."""

    continuous: IRExpr | None
    event: IRExpr | None
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRControl:
    name: str
    canonical_name: str    # "control/<name>"
    type_kind: str         # "frequency"|"duration"|"voltage"|"percent"|"boolean"|"enum"|"placement"
    dims: Dimensions
    default: IRExpr | None
    range_low: IRExpr | None
    range_high: IRExpr | None
    log_scale: bool
    label: str | None
    live_tunable: bool
    tune_strategy: str | None
    loc: Loc | None = None
    # Placement-control fields (all None/empty/False for numeric controls):
    kind: str | None = None             # placement only: "active" | "bipolar" | "pair" | "set"
    allowed: tuple = ()                  # placement only: tuple of channel names or pairs; () means "any"
    final: bool = False
    default_placement: tuple = ()        # active: ("Cz",); bipolar/pair: ("T3","T4"); set: ("Cz",); () if none
    set_min: int | None = None           # set only: minimum number of sites (default 1)
    set_max: int | None = None           # set only: maximum number of sites (None = unlimited)


@dataclass(frozen=True, slots=True)
class IRPhase:
    name: str
    duration_ms: float
    output_muted: bool
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRSession:
    phases: tuple[IRPhase, ...]
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRRequires:
    """Resolved hardware requirements."""

    coupling: str | None            # "dc" | "ac" | None (unspecified)
    sample_rate_min_hz: int | None  # protocol's >= threshold
    sample_rate_chosen_hz: int      # selected from amp's available rates
    channels: tuple[str, ...]
    impedance: str                  # "required" | "preferred" | "not_required"
    markers: str                    # "required" | "preferred" | "not_required"


@dataclass(frozen=True, slots=True)
class IRMeta:
    """All `meta` block fields kept verbatim for CRED-nf export and
    introspection. Values are IRExprs so units and richer structure
    (lists of outcome measures, etc.) survive."""

    fields: dict[str, IRExpr]


@dataclass(frozen=True, slots=True)
class IRProtocol:
    """A fully resolved, typed, validated protocol."""

    name: str
    extends: str | None
    meta: IRMeta
    requires: IRRequires
    inputs: dict[str, IRInput]
    derives: dict[str, IRDerive]
    thresholds: dict[str, IRThreshold]
    inhibits: dict[str, IRInhibit]
    customs: dict[str, IRCustom]
    reward: IRReward
    output: dict[str, IRExpr]   # output-channel-name -> typed expression
    controls: dict[str, IRControl]
    session: IRSession
    topological_order: tuple[str, ...]
    budget: ResourceBudget
    amp_profile: AmpProfile | None
    loc: Loc | None = None


__all__ = [
    "IRArg",
    "IRArray",
    "IRBinaryOp",
    "IRBlockExpr",
    "IRBoolLit",
    "IRCall",
    "IRConditional",
    "IRControl",
    "IRControlRef",
    "IRCustom",
    "IRDerive",
    "IRExpr",
    "IRInhibit",
    "IRInput",
    "IRMeta",
    "IRNode",
    "IRNumberLit",
    "IRPhase",
    "IRProtocol",
    "IRRequires",
    "IRReward",
    "IRRewardField",
    "IRSession",
    "IRStreamRef",
    "IRStringLit",
    "IRThreshold",
    "IRThresholdRef",
    "IRTuple",
]
