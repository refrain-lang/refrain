# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain resolver — AST -> IR.

The resolver runs through SPEC §6's static checks and emits a fully-
typed, fully-resolved `IRProtocol`. Phase 0b scope:

  - Reference resolution: classify each string-literal-in-expression-
    position as a stream reference, threshold reference, or plain value
    (SPEC §5.4).
  - Identifier resolution: `NameRef` to a `controls.<name>` entry, to
    `reward`, or to a primitive (in call position).
  - Acyclicity: SPEC §6.1. Enforced by strict source-order processing
    (SPEC §5.4 forbids forward references in v0.0).
  - Primitive call type checking against the registry (SPEC §6.2).
  - Unit / dimension consistency on binary ops.
  - Hardware validation against an amp profile (SPEC §6.3).
  - Resource budget accounting (SPEC §6.4) — coarse, sums per-primitive
    budgets without longest-path analysis.

Phase 0b deliberately defers:
  - Composition: `extends` / `amend` / `remove` / `final`. None of the
    three example protocols use composition; library-path resolution
    needs design before that lands. Tracked in docs/DESIGN-NOTES.md.
  - Rate alignment (SPEC §6.6). The three examples don't trigger rate
    mismatches because every stream they produce runs at the input
    sample rate.
  - CRED-nf completeness check (SPEC §6.5) — warnings only; the export
    layer flags missing fields visibly.

Errors are surfaced as `ResolveError` exceptions carrying the source
`Loc` where the problem was detected. The resolver bails on the first
error rather than accumulating; multi-error reporting is a Phase 0c
follow-up.
"""

from __future__ import annotations

from . import ast as A
from . import primitives as P
from .amp_profile import AmpProfile
from .ast import Loc
from .compose import ComposeError, ParentLoader, compose
from .ir import (
    IRArg,
    IRArray,
    IRBinaryOp,
    IRBlockExpr,
    IRBoolLit,
    IRCall,
    IRConditional,
    IRControl,
    IRControlRef,
    IRCustom,
    IRDerive,
    IRExpr,
    IRInhibit,
    IRInput,
    IRMeta,
    IRNumberLit,
    IRPhase,
    IRProtocol,
    IRRequires,
    IRReward,
    IRRewardField,
    IRSession,
    IRStreamRef,
    IRStringLit,
    IRThreshold,
    IRThresholdRef,
    IRTuple,
)
from .primitives import PrimitiveSpec, ResolvedArg, ResourceBudget
from .types_ import (
    BOOLEAN_STREAM,
    DIMENSIONLESS,
    EVENT_STREAM,
    FREQUENCY,
    TIME,
    VOLTAGE,
    Dimensions,
    StreamType,
    dims_compatible_for_add,
    scalar_stream,
    unit_dims,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ResolveError(Exception):
    """Raised on any static validation failure during resolution."""

    def __init__(self, message: str, loc: Loc | None = None):
        self.loc = loc
        if loc is not None:
            super().__init__(f"line {loc.line}:{loc.col}: {message}")
        else:
            super().__init__(message)


# ---------------------------------------------------------------------------
# Output channel registry (SPEC §4.8)
# ---------------------------------------------------------------------------


_ANALOG_OUTPUTS = {"audio_gain", "video_clarity", "video_brightness", "ambient_density"}
_EVENT_OUTPUTS = {"audio_chime", "score_increment"}
_KNOWN_OUTPUTS = _ANALOG_OUTPUTS | _EVENT_OUTPUTS
# Many real protocols add custom output channels (e.g. `game_speed` in
# the smr_cz example). v0.0 declares custom channels out of scope but
# the resolver accepts unknown channels with a soft type — full
# enforcement comes when extensions are defined.


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class _Resolver:
    """Single-use resolver: instantiate with the file AST, call `resolve()`."""

    def __init__(self, file_ast: A.File, amp: AmpProfile | None, bindings: dict | None = None):
        self.file = file_ast
        self.amp = amp
        self.bindings: dict = bindings or {}

        # Section-block AST snapshots (one each, last-wins per spec
        # composition default; composition isn't here yet so just first-wins).
        self.meta_ast: A.SectionBlock | None = None
        self.requires_ast: A.SectionBlock | None = None
        self.reward_ast: A.SectionBlock | None = None
        self.output_ast: A.SectionBlock | None = None
        self.controls_ast: A.SectionBlock | None = None
        self.session_ast: A.SectionBlock | None = None

        # Resolved named entities (filled in source order).
        self.inputs: dict[str, IRInput] = {}
        self.derives: dict[str, IRDerive] = {}
        self.thresholds: dict[str, IRThreshold] = {}
        self.inhibits: dict[str, IRInhibit] = {}
        self.customs: dict[str, IRCustom] = {}
        self.controls: dict[str, IRControl] = {}

        # Topological order — populated as named entities are resolved.
        self._topo: list[str] = []

        # Running resource budget.
        self._state_kb = 0
        self._worst_case_us = 0

        # Reward IR (filled by resolve_reward).
        self.reward_ir: IRReward | None = None

    # -- Top-level entry ----------------------------------------------------

    def resolve(self) -> IRProtocol:
        proto = self.file.protocol
        # Note: composition is applied by the public `resolve()` entry
        # point before constructing `_Resolver`, so any `extends` is
        # already merged in. If this invariant is violated (e.g. caller
        # constructs `_Resolver` directly), surface a clear error.
        if proto.extends is not None:
            raise ResolveError(
                f"internal: protocol still has `extends \"{proto.extends}\"` at "
                "resolver entry; composition must run first",
                loc=proto.loc,
            )

        self._hoist(proto)

        # Pass order matters:
        #   - controls FIRST — placement controls must be resolved before
        #     requires so that requires.channels = [site] can expand the
        #     bound placement via _bound_placement_value. Controls are
        #     protocol-global parameters with no dependency on requires.
        #   - requires SECOND (amp validation; uses resolved controls for
        #     placement expansion in _parse_channel_list). Controls can
        #     be referenced as NameRefs from any derive (e.g.
        #     `bandpass(center: orf, ...)` in the Othmer ILF protocol).
        #     Per SPEC §3 NameRefs are a distinct expression form from
        #     string-lit references; the §5.4 "previously declared" rule
        #     applies only to string-lits.
        #   - named decls in source order (inputs, derives, thresholds,
        #     inhibits, customs)
        #   - reward / output / session last, since they reference
        #     everything declared above
        self._resolve_controls()
        requires_ir = self._resolve_requires()
        self._resolve_named_decls(proto)
        self._resolve_reward()
        output_ir = self._resolve_output()
        session_ir = self._resolve_session()
        meta_ir = self._resolve_meta()

        return IRProtocol(
            name=proto.name,
            extends=None,
            meta=meta_ir,
            requires=requires_ir,
            inputs=self.inputs,
            derives=self.derives,
            thresholds=self.thresholds,
            inhibits=self.inhibits,
            customs=self.customs,
            reward=self.reward_ir or IRReward(continuous=None, event=None),
            output=output_ir,
            controls=self.controls,
            session=session_ir,
            topological_order=tuple(self._topo),
            budget=ResourceBudget(
                state_kb=self._state_kb,
                worst_case_us=self._worst_case_us,
            ),
            amp_profile=self.amp,
            loc=proto.loc,
        )

    # -- Hoisting -----------------------------------------------------------

    def _hoist(self, proto: A.Protocol) -> None:
        """Bucket each top-level statement by category."""
        for stmt in proto.body:
            if isinstance(stmt, A.SectionBlock):
                attr = {
                    "meta": "meta_ast",
                    "requires": "requires_ast",
                    "reward": "reward_ast",
                    "output": "output_ast",
                    "controls": "controls_ast",
                    "session": "session_ast",
                }.get(stmt.keyword)
                if attr is None:
                    raise ResolveError(
                        f"unknown section block keyword {stmt.keyword!r}",
                        loc=stmt.loc,
                    )
                if getattr(self, attr) is not None:
                    raise ResolveError(
                        f"duplicate `{stmt.keyword}` block",
                        loc=stmt.loc,
                    )
                setattr(self, attr, stmt)
            elif isinstance(stmt, (A.AmendDecl, A.RemoveDecl)):
                # After composition, only top-level section/named decls
                # remain. A stray amend/remove here means the protocol
                # used amend/remove without an `extends` — which has no
                # parent to compose against.
                raise ResolveError(
                    f"`{type(stmt).__name__.replace('Decl', '').lower()}` requires "
                    "an `extends` clause (nothing to compose against)",
                    loc=stmt.loc,
                )
            # Named decls are resolved in source order, not pre-hoisted.

    # -- Requires -----------------------------------------------------------

    def _resolve_requires(self) -> IRRequires:
        if self.requires_ast is None:
            # SPEC §4.2 doesn't say `requires` is mandatory, but in
            # practice every example has it. Allow absent for now.
            return IRRequires(
                coupling=None,
                sample_rate_min_hz=None,
                sample_rate_chosen_hz=self.amp.sample_rates_hz[-1] if self.amp else 256,
                channels=(),
                impedance="not_required",
                markers="not_required",
            )
        fields = self._assignments_dict(self.requires_ast.body)
        coupling = self._string_field(fields, "coupling")
        impedance = self._string_field(fields, "impedance", default="not_required")
        markers = self._string_field(fields, "markers", default="not_required")
        sample_rate_min, sample_rate_chosen = self._parse_sample_rate(fields)
        channels = self._parse_channel_list(fields)

        # Validate against amp profile.
        if self.amp is not None:
            if coupling and not self.amp.supports_coupling(coupling):
                raise ResolveError(
                    f"amp {self.amp.model!r} does not support {coupling!r} coupling "
                    f"(supports: {list(self.amp.coupling)})",
                    loc=self.requires_ast.loc,
                )
            missing = [c for c in channels if not self.amp.has_channel(c)]
            if missing:
                raise ResolveError(
                    f"amp {self.amp.model!r} is missing required channels: {missing}",
                    loc=self.requires_ast.loc,
                )

        return IRRequires(
            coupling=coupling,
            sample_rate_min_hz=sample_rate_min,
            sample_rate_chosen_hz=sample_rate_chosen,
            channels=tuple(channels),
            impedance=impedance,
            markers=markers,
        )

    def _parse_sample_rate(self, fields: dict[str, A.Expr]) -> tuple[int | None, int]:
        """`sample_rate = ">= 256 Hz"` -> (256, chosen_rate)."""
        if "sample_rate" not in fields:
            chosen = self.amp.sample_rates_hz[-1] if self.amp else 256
            return None, chosen
        expr = fields["sample_rate"]
        if not isinstance(expr, A.StringLit):
            raise ResolveError(
                "requires.sample_rate must be a comparison string like \">= 256 Hz\"",
                loc=expr.loc,
            )
        # Parse e.g. ">= 256 Hz".
        text = expr.value.strip()
        if not text.startswith(">="):
            raise ResolveError(
                f"requires.sample_rate must start with '>=' (got {text!r})",
                loc=expr.loc,
            )
        rest = text[2:].strip()
        # Strip optional ` Hz` suffix.
        if rest.endswith("Hz"):
            rest = rest[:-2].strip()
        try:
            min_hz = int(rest)
        except ValueError as exc:
            raise ResolveError(
                f"requires.sample_rate has unparseable rate {rest!r}",
                loc=expr.loc,
            ) from exc

        if self.amp is None:
            return min_hz, min_hz
        chosen = self.amp.best_sample_rate_at_least(min_hz)
        if chosen is None:
            raise ResolveError(
                f"amp {self.amp.model!r} has no sample rate >= {min_hz} Hz "
                f"(available: {list(self.amp.sample_rates_hz)})",
                loc=expr.loc,
            )
        return min_hz, chosen

    def _parse_channel_list(self, fields: dict[str, A.Expr]) -> list[str]:
        if "channels" not in fields:
            return []
        arr = fields["channels"]
        if not isinstance(arr, A.Array):
            raise ResolveError(
                "requires.channels must be an array of channel-name strings",
                loc=arr.loc,
            )
        out: list[str] = []
        for elt in arr.elements:
            if isinstance(elt, A.StringLit):
                out.append(elt.value)
            elif (
                isinstance(elt, A.NameRef)
                and elt.name in self.controls
                and self.controls[elt.name].type_kind == "placement"
            ):
                # Expand a placement control reference to its bound channel(s).
                # _bound_placement_value returns a str (active) or 2-tuple (bipolar).
                bound = self._bound_placement_value(elt.name)
                if isinstance(bound, str):
                    out.append(bound)
                else:
                    # bipolar: extend with both legs
                    out.extend(bound)
            else:
                raise ResolveError(
                    "requires.channels entries must be string literals",
                    loc=elt.loc,
                )
        return out

    # -- Meta ---------------------------------------------------------------

    def _resolve_meta(self) -> IRMeta:
        if self.meta_ast is None:
            return IRMeta(fields={})
        # Meta fields are simple value expressions; they should not
        # contain references to streams. Resolve as values.
        out: dict[str, IRExpr] = {}
        for stmt in self.meta_ast.body:
            if not isinstance(stmt, A.Assignment):
                raise ResolveError("meta block only allows assignments", loc=stmt.loc)
            out[stmt.target] = self._resolve_value_expr(stmt.value)
        return IRMeta(fields=out)

    # -- Named decls in source order ---------------------------------------

    def _resolve_named_decls(self, proto: A.Protocol) -> None:
        for stmt in proto.body:
            if isinstance(stmt, A.NamedDecl):
                if stmt.keyword == "input":
                    self.inputs[stmt.name] = self._resolve_input(stmt)
                    self._topo.append(f"input/{stmt.name}")
                elif stmt.keyword == "derive":
                    self.derives[stmt.name] = self._resolve_derive(stmt)
                    self._topo.append(f"derive/{stmt.name}")
                elif stmt.keyword == "threshold":
                    self.thresholds[stmt.name] = self._resolve_threshold(stmt)
                    self._topo.append(f"threshold/{stmt.name}")
                elif stmt.keyword == "inhibit":
                    self.inhibits[stmt.name] = self._resolve_inhibit(stmt)
                    self._topo.append(f"inhibit/{stmt.name}")
                elif stmt.keyword == "custom":
                    self.customs[stmt.name] = self._resolve_custom(stmt)
                    self._topo.append(f"custom/{stmt.name}")

    def _resolve_input(self, decl: A.NamedDecl) -> IRInput:
        fields = self._assignments_dict(decl.body)
        montage_ast = fields.get("montage")
        if montage_ast is None:
            raise ResolveError(f"input \"{decl.name}\" missing `montage` field", loc=decl.loc)
        if not isinstance(montage_ast, A.Call):
            raise ResolveError(
                f"input \"{decl.name}\".montage must be a call (bipolar/referential/...)",
                loc=montage_ast.loc,
            )
        # Placement substitution: rewrite any active-placement NameRef in the
        # montage call's args to a concrete StringLit before passing to
        # _resolve_call.  This keeps _resolve_call unchanged and produces an IR
        # with a concrete channel string — identical to a literal-site protocol.
        montage_ast = self._substitute_placement_args(montage_ast)
        montage_ir = self._resolve_call(montage_ast)
        return IRInput(
            name=decl.name,
            canonical_name=f"input/{decl.name}",
            stream_type=montage_ir.stream_type,
            montage=montage_ir,
            loc=decl.loc,
        )

    def _substitute_placement_args(self, call: A.Call) -> A.Call:
        """Rewrite montage A.Call args for placement substitution.

        Two cases are handled:

        1. ``active`` placement NameRef in any slot:
           ``referential(active: site)`` → ``referential(active: "Cz")``.
           The NameRef is replaced by an A.StringLit of the bound channel.

        2. ``bipolar(pair: <NameRef naming a bipolar placement>)`` form:
           The entire ``pair:`` arg is expanded into two args:
           ``bipolar(plus: "T3", minus: "T4")``.
           This makes the IR identical to a literal ``bipolar(plus:, minus:)``,
           so ``_resolve_call`` is unchanged.

        All other args are left unchanged.  Returns a (possibly new) A.Call.
        """
        # Special case: bipolar(pair: <bipolar-placement NameRef>)
        # Detect before iterating args so we can replace the whole call shape.
        if call.callee == "bipolar":
            pair_arg = None
            for arg in call.args:
                if (
                    arg.name == "pair"
                    and isinstance(arg.value, A.NameRef)
                    and arg.value.name in self.controls
                    and self.controls[arg.value.name].type_kind == "placement"
                    and self.controls[arg.value.name].kind == "bipolar"
                ):
                    pair_arg = arg
                    break
            if pair_arg is not None:
                pair = self._bound_placement_value(pair_arg.value.name)  # type: ignore[assignment]
                plus_str, minus_str = pair  # 2-tuple guaranteed by _bound_placement_value
                loc = pair_arg.value.loc
                plus_arg = A.Arg(name="plus", value=A.StringLit(value=plus_str, loc=loc), loc=pair_arg.loc)
                minus_arg = A.Arg(name="minus", value=A.StringLit(value=minus_str, loc=loc), loc=pair_arg.loc)
                # Replace the pair: arg with plus: / minus:; keep any other args unchanged.
                other_args = [a for a in call.args if a is not pair_arg]
                return A.Call(
                    callee=call.callee,
                    args=tuple([plus_arg, minus_arg] + other_args),
                    loc=call.loc,
                )

        # General case: rewrite active-placement NameRefs and pair-leg MemberAccess in individual slots.
        new_args = []
        changed = False
        for arg in call.args:
            if (
                isinstance(arg.value, A.NameRef)
                and arg.value.name in self.controls
                and self.controls[arg.value.name].type_kind == "placement"
                and self.controls[arg.value.name].kind == "active"
            ):
                channel = self._bound_placement_value(arg.value.name)
                new_arg = A.Arg(name=arg.name, value=A.StringLit(value=channel, loc=arg.value.loc), loc=arg.loc)
                new_args.append(new_arg)
                changed = True
            elif (
                isinstance(arg.value, A.MemberAccess)
                and isinstance(arg.value.target, A.NameRef)
                and arg.value.target.name in self.controls
                and self.controls[arg.value.target.name].type_kind == "placement"
                and self.controls[arg.value.target.name].kind == "pair"
                and arg.value.member in ("a", "b")
            ):
                # coh.a → bound leg[0]; coh.b → bound leg[1]
                pair_name = arg.value.target.name
                member = arg.value.member
                legs = self._bound_placement_value(pair_name)  # 2-tuple (a, b)
                leg = legs[0] if member == "a" else legs[1]
                new_arg = A.Arg(name=arg.name, value=A.StringLit(value=leg, loc=arg.value.loc), loc=arg.loc)
                new_args.append(new_arg)
                changed = True
            else:
                new_args.append(arg)
        if not changed:
            return call
        return A.Call(callee=call.callee, args=tuple(new_args), loc=call.loc)

    def _resolve_derive(self, decl: A.NamedDecl) -> IRDerive:
        fields = self._assignments_dict(decl.body)
        has_from = "from" in fields
        has_pipeline = "pipeline" in fields
        has_formula = "formula" in fields

        if has_formula and (has_from or has_pipeline):
            raise ResolveError(
                f"derive \"{decl.name}\" mixes formula form with from/pipeline",
                loc=decl.loc,
            )
        if has_formula:
            expr = self._resolve_stream_expr(fields["formula"])
        elif has_from and has_pipeline:
            from_expr = fields["from"]
            if not isinstance(from_expr, A.StringLit):
                raise ResolveError(
                    f"derive \"{decl.name}\".from must be a stream reference (string)",
                    loc=from_expr.loc,
                )
            pipeline = fields["pipeline"]
            if not isinstance(pipeline, A.Array):
                raise ResolveError(
                    f"derive \"{decl.name}\".pipeline must be an array of primitive calls",
                    loc=pipeline.loc,
                )
            expr = self._lower_pipeline(from_expr, pipeline)
        else:
            raise ResolveError(
                f"derive \"{decl.name}\" must declare either `formula` or `from`+`pipeline`",
                loc=decl.loc,
            )

        upstream = tuple(_collect_upstream(expr))
        return IRDerive(
            name=decl.name,
            canonical_name=f"derive/{decl.name}",
            stream_type=_expr_stream_type(expr),
            expression=expr,
            upstream=upstream,
            loc=decl.loc,
        )

    def _resolve_threshold(self, decl: A.NamedDecl) -> IRThreshold:
        fields = self._assignments_dict(decl.body)
        signal_expr = fields.get("signal")
        type_expr = fields.get("type")
        if signal_expr is None or type_expr is None:
            raise ResolveError(
                f"threshold \"{decl.name}\" needs both `signal` and `type` fields",
                loc=decl.loc,
            )
        if not isinstance(signal_expr, A.StringLit):
            raise ResolveError(
                f"threshold \"{decl.name}\".signal must be a stream reference",
                loc=signal_expr.loc,
            )
        signal_ir = self._resolve_string_as_reference(signal_expr)
        if not isinstance(signal_ir, IRStreamRef):
            raise ResolveError(
                f"threshold \"{decl.name}\".signal {signal_expr.value!r} is not a stream",
                loc=signal_expr.loc,
            )
        if not isinstance(type_expr, A.Call):
            raise ResolveError(
                f"threshold \"{decl.name}\".type must be a constructor call "
                "(absolute/percentile/dynamic)",
                loc=type_expr.loc,
            )
        type_call = self._resolve_call(type_expr, _stream_input_hint=signal_ir.stream_type)
        # The threshold value's dimensions follow the source stream's.
        thresh_type = scalar_stream(signal_ir.stream_type.dimensions)
        live_tunable = self._bool_field(fields, "live_tunable", default=False)
        return IRThreshold(
            name=decl.name,
            canonical_name=f"threshold/{decl.name}",
            signal=signal_ir.target,
            threshold_call=type_call,
            stream_type=thresh_type,
            live_tunable=live_tunable,
            loc=decl.loc,
        )

    def _resolve_inhibit(self, decl: A.NamedDecl) -> IRInhibit:
        fields = self._assignments_dict(decl.body)
        metric_expr = fields.get("metric")
        threshold_expr = fields.get("threshold")
        action_expr = fields.get("action")
        if metric_expr is None or threshold_expr is None or action_expr is None:
            raise ResolveError(
                f"inhibit \"{decl.name}\" needs `metric`, `threshold`, and `action`",
                loc=decl.loc,
            )
        if not isinstance(metric_expr, A.Call):
            raise ResolveError(
                f"inhibit \"{decl.name}\".metric must be a primitive call (e.g. bandpower)",
                loc=metric_expr.loc,
            )
        metric_ir = self._resolve_call(metric_expr)
        if not isinstance(threshold_expr, A.Call):
            raise ResolveError(
                f"inhibit \"{decl.name}\".threshold must be a constructor call",
                loc=threshold_expr.loc,
            )
        threshold_ir = self._resolve_call(threshold_expr, _stream_input_hint=metric_ir.stream_type)
        if not isinstance(action_expr, A.Call):
            raise ResolveError(
                f"inhibit \"{decl.name}\".action must be a call (mute/freeze/flag)",
                loc=action_expr.loc,
            )
        action_ir = self._resolve_call(action_expr)
        action_kind = action_ir.callee
        release_ms = _extract_duration_ms(action_ir, "release")
        final = self._bool_field(fields, "final", default=False)
        return IRInhibit(
            name=decl.name,
            canonical_name=f"inhibit/{decl.name}",
            metric=metric_ir,
            threshold=threshold_ir,
            action_kind=action_kind,
            action_release_ms=release_ms,
            final=final,
            loc=decl.loc,
        )

    def _resolve_custom(self, decl: A.NamedDecl) -> IRCustom:
        fields = self._assignments_dict(decl.body)
        module = self._string_field(fields, "module")
        if module is None:
            raise ResolveError(f"custom \"{decl.name}\" missing `module` field", loc=decl.loc)
        signature_str = self._string_field(fields, "signature", default="")
        budget_block = fields.get("budget")
        state_kb = 0
        worst_case_us = 0
        if isinstance(budget_block, A.BlockExpr):
            for bf in budget_block.body:
                if isinstance(bf, A.Assignment) and isinstance(bf.value, A.NumberLit):
                    if bf.target == "state_kb":
                        state_kb = int(bf.value.value)
                    elif bf.target == "worst_case_us":
                        worst_case_us = int(bf.value.value)
        self._state_kb += state_kb
        self._worst_case_us += worst_case_us
        return IRCustom(
            name=decl.name,
            canonical_name=f"custom/{decl.name}",
            module=module,
            signature_str=signature_str or "",
            budget=ResourceBudget(state_kb=state_kb, worst_case_us=worst_case_us),
            loc=decl.loc,
        )

    # -- Controls -----------------------------------------------------------

    def _resolve_controls(self) -> None:
        if self.controls_ast is None:
            return
        for stmt in self.controls_ast.body:
            if not isinstance(stmt, A.Assignment):
                raise ResolveError("controls block only allows assignments", loc=stmt.loc)
            if not isinstance(stmt.value, A.BlockExpr) or stmt.value.name is None:
                raise ResolveError(
                    f"control {stmt.target!r} must be a typed block "
                    "(e.g. `frequency {{ ... }}`)",
                    loc=stmt.loc,
                )
            self.controls[stmt.target] = self._resolve_control(stmt.target, stmt.value)
            self._topo.append(f"control/{stmt.target}")

    def _resolve_control(self, name: str, block: A.BlockExpr) -> IRControl:
        kind = block.name or ""
        dims = _control_kind_dims(kind, block.loc)
        fields = self._assignments_dict(block.body)

        if kind == "placement":
            return self._resolve_placement_control(name, fields, block.loc)

        default = self._resolve_value_expr(fields["default"]) if "default" in fields else None
        range_low: IRExpr | None = None
        range_high: IRExpr | None = None
        if "range" in fields:
            range_ast = fields["range"]
            if not isinstance(range_ast, A.Tuple) or len(range_ast.elements) != 2:
                raise ResolveError(
                    f"control {name!r}.range must be a 2-tuple",
                    loc=range_ast.loc,
                )
            range_low = self._resolve_value_expr(range_ast.elements[0])
            range_high = self._resolve_value_expr(range_ast.elements[1])
        log_scale = self._bool_field(fields, "log", default=False)
        live_tunable = self._bool_field(fields, "live_tunable", default=False)
        label_expr = fields.get("label")
        label = label_expr.value if isinstance(label_expr, A.StringLit) else None
        tune_strategy_expr = fields.get("tune_strategy")
        tune_strategy = (
            tune_strategy_expr.value
            if isinstance(tune_strategy_expr, A.StringLit)
            else None
        )
        return IRControl(
            name=name,
            canonical_name=f"control/{name}",
            type_kind=kind,
            dims=dims,
            default=default,
            range_low=range_low,
            range_high=range_high,
            log_scale=log_scale,
            label=label,
            live_tunable=live_tunable,
            tune_strategy=tune_strategy,
            loc=block.loc,
        )

    def _resolve_placement_control(self, name: str, fields: dict, loc) -> IRControl:
        """Parse and validate a `placement { ... }` control block."""
        # kind: "active" | "bipolar" | "pair" | "set" — required
        kind_expr = fields.get("kind")
        if not isinstance(kind_expr, A.StringLit) or kind_expr.value not in ("active", "bipolar", "pair", "set"):
            raise ResolveError(
                f"placement control {name!r} needs kind = \"active\", \"bipolar\", \"pair\", or \"set\"",
                loc=loc,
            )
        place_kind = kind_expr.value

        # live_tunable = true is forbidden for placement (frozen per session)
        if self._bool_field(fields, "live_tunable", default=False):
            raise ResolveError(
                f"placement control {name!r} cannot be live_tunable (site is frozen per session)",
                loc=loc,
            )

        final = self._bool_field(fields, "final", default=False)

        label_expr = fields.get("label")
        label = label_expr.value if isinstance(label_expr, A.StringLit) else None

        # "pair" reuses bipolar's 2-tuple value parsing (same shape: (a, b)).
        parse_kind = "bipolar" if place_kind == "pair" else place_kind

        allowed = self._parse_placement_allowed(name, parse_kind, fields.get("allowed"), loc)
        default = self._parse_placement_value(name, parse_kind, fields.get("default"), loc)

        if default is None:
            raise ResolveError(
                f"placement control {name!r} requires a default",
                loc=loc,
            )

        self._check_placement_in_allowed(name, default, allowed, loc)

        # Store default_placement: active → ("Cz",); bipolar/pair → ("leg_a", "leg_b")
        if place_kind == "active":
            default_placement = (default,)
        else:
            default_placement = default  # already a tuple (plus/a, minus/b)

        return IRControl(
            name=name,
            canonical_name=f"control/{name}",
            type_kind="placement",
            dims=DIMENSIONLESS,
            default=None,
            range_low=None,
            range_high=None,
            log_scale=False,
            label=label,
            live_tunable=False,
            tune_strategy=None,
            kind=place_kind,
            allowed=allowed,
            final=final,
            default_placement=default_placement,
            loc=loc,
        )

    def _parse_placement_value(self, name: str, place_kind: str, expr, loc):
        """Parse a single placement value (default or an element of allowed).

        For active:  expects A.StringLit → returns the channel string.
        For bipolar: expects A.Tuple of two A.StringLit → returns (plus, minus).
        Returns None if expr is None.
        """
        if expr is None:
            return None
        if place_kind == "active":
            if not isinstance(expr, A.StringLit):
                raise ResolveError(
                    f"placement control {name!r} (active): channel value must be a string literal",
                    loc=loc,
                )
            return expr.value
        else:  # bipolar
            if (
                not isinstance(expr, A.Tuple)
                or len(expr.elements) != 2
                or not isinstance(expr.elements[0], A.StringLit)
                or not isinstance(expr.elements[1], A.StringLit)
            ):
                raise ResolveError(
                    f"placement control {name!r} (bipolar): pair value must be a 2-tuple of strings",
                    loc=loc,
                )
            return (expr.elements[0].value, expr.elements[1].value)

    def _parse_placement_allowed(self, name: str, place_kind: str, expr, loc) -> tuple:
        """Parse the `allowed` field of a placement control.

        "any" (StringLit) or absent → () (sentinel meaning any channel is allowed).
        A non-empty array of channel values → tuple of parsed values. An empty
        array is rejected: () is reserved for "any", so `allowed = []` (which would
        otherwise silently mean "accept anything") is almost certainly an authoring
        mistake.
        """
        if expr is None:
            return ()
        if isinstance(expr, A.StringLit) and expr.value == "any":
            return ()
        if isinstance(expr, A.Array):
            if not expr.elements:
                raise ResolveError(
                    f"placement control {name!r}: allowed must be non-empty "
                    "(use \"any\" to allow any channel)",
                    loc=loc,
                )
            result = []
            for elt in expr.elements:
                val = self._parse_placement_value(name, place_kind, elt, loc)
                result.append(val)
            return tuple(result)
        raise ResolveError(
            f"placement control {name!r}: allowed must be \"any\" or an array of channel names",
            loc=loc,
        )

    def _check_placement_in_allowed(self, name: str, value, allowed: tuple, loc) -> None:
        """Raise ResolveError if allowed is non-empty and value is not in it."""
        if allowed and value not in allowed:
            raise ResolveError(
                f"placement {name!r}: {value!r} not in allowed {list(allowed)}",
                loc=loc,
            )

    def _bound_placement_value(self, name: str):
        """Return the concrete bound value for a placement control.

        Dispatches on ``ctrl.kind``:
          - ``active``  → returns a channel-name string.
          - ``bipolar`` → returns a 2-tuple ``(plus, minus)`` of channel-name strings.

        Looks up ``name`` in ``self.bindings`` (override) or falls back to the
        control's ``default_placement``.  Validates the result against ``allowed``
        and, when an amp profile is set, against the device's channel list.
        Raises ``ResolveError`` if:
          - the control is ``final`` and an override is supplied;
          - the bound value is not in ``allowed`` (non-empty list means restrict);
          - the amp does not have the channel (or either leg for bipolar).
        """
        ctrl = self.controls.get(name)
        if ctrl is None or ctrl.type_kind != "placement":
            raise ResolveError(
                f"internal: _bound_placement_value called for non-placement {name!r}",
            )
        loc = ctrl.loc

        if name in self.bindings:
            if ctrl.final:
                raise ResolveError(
                    f"placement {name!r} is final and cannot be overridden",
                    loc=loc,
                )
            value = self.bindings[name]
        else:
            # Use default: active default_placement is a 1-tuple; bipolar is already a 2-tuple.
            if ctrl.kind == "active":
                value = ctrl.default_placement[0]
            else:
                value = ctrl.default_placement  # 2-tuple (plus, minus)

        if ctrl.kind == "active":
            if not isinstance(value, str):
                raise ResolveError(
                    f"placement {name!r} (active): binding value must be a channel-name "
                    f"string, got {type(value).__name__}",
                    loc=loc,
                )
            # allowed = () means "any"; only validate when non-empty.
            self._check_placement_in_allowed(name, value, ctrl.allowed, loc)
            # Device check: the amp must have the channel.
            if self.amp is not None and not self.amp.has_channel(value):
                raise ResolveError(
                    f"amp {self.amp.model!r} is missing required channels: [{value!r}]",
                    loc=loc,
                )
            return value
        elif ctrl.kind == "pair":
            # pair: value must be a 2-tuple of strings (a, b) — symmetric legs.
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not isinstance(value[0], str)
                or not isinstance(value[1], str)
            ):
                raise ResolveError(
                    f"placement {name!r} (pair): binding value must be a 2-tuple of "
                    f"channel-name strings, got {value!r}",
                    loc=loc,
                )
            # allowed = () means "any"; only validate when non-empty.
            self._check_placement_in_allowed(name, value, ctrl.allowed, loc)
            # Device check: both legs must be present on the amp.
            if self.amp is not None:
                missing = [ch for ch in value if not self.amp.has_channel(ch)]
                if missing:
                    raise ResolveError(
                        f"amp {self.amp.model!r} is missing required channels: {missing}",
                        loc=loc,
                    )
            return value
        else:
            # bipolar: value must be a 2-tuple of strings
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not isinstance(value[0], str)
                or not isinstance(value[1], str)
            ):
                raise ResolveError(
                    f"placement {name!r} (bipolar): binding value must be a 2-tuple of "
                    f"channel-name strings, got {value!r}",
                    loc=loc,
                )
            # allowed = () means "any"; only validate when non-empty.
            self._check_placement_in_allowed(name, value, ctrl.allowed, loc)
            # Device check: both legs must be present on the amp.
            if self.amp is not None:
                missing = [ch for ch in value if not self.amp.has_channel(ch)]
                if missing:
                    raise ResolveError(
                        f"amp {self.amp.model!r} is missing required channels: {missing}",
                        loc=loc,
                    )
            return value

    # -- Reward -------------------------------------------------------------

    def _resolve_reward(self) -> None:
        if self.reward_ast is None:
            self.reward_ir = IRReward(continuous=None, event=None)
            return
        fields = self._assignments_dict(self.reward_ast.body)
        cont_expr = fields.get("continuous")
        event_expr = fields.get("event")
        if cont_expr is None and event_expr is None:
            raise ResolveError(
                "reward block must declare `continuous`, `event`, or both",
                loc=self.reward_ast.loc,
            )
        cont_ir = self._resolve_stream_expr(cont_expr) if cont_expr is not None else None
        event_ir = self._resolve_stream_expr(event_expr) if event_expr is not None else None
        if event_ir is not None and _expr_stream_type(event_ir) != EVENT_STREAM:
            raise ResolveError(
                f"reward.event must produce event_stream, got {_expr_stream_type(event_ir)}",
                loc=event_expr.loc if event_expr else None,
            )
        self.reward_ir = IRReward(continuous=cont_ir, event=event_ir, loc=self.reward_ast.loc)

    # -- Output -------------------------------------------------------------

    def _resolve_output(self) -> dict[str, IRExpr]:
        if self.output_ast is None:
            return {}
        out: dict[str, IRExpr] = {}
        for stmt in self.output_ast.body:
            if not isinstance(stmt, A.Assignment):
                raise ResolveError("output block only allows assignments", loc=stmt.loc)
            # Unknown channels are accepted with a warning emitted only
            # via the IR (the printer can show them). The spec calls
            # custom channels out-of-scope for v0.0; we don't reject.
            out[stmt.target] = self._resolve_stream_expr(stmt.value)
        return out

    # -- Session ------------------------------------------------------------

    def _resolve_session(self) -> IRSession:
        if self.session_ast is None:
            return IRSession(phases=())
        fields = self._assignments_dict(self.session_ast.body)
        phases_ast = fields.get("phases")
        if phases_ast is None:
            return IRSession(phases=(), loc=self.session_ast.loc)
        if not isinstance(phases_ast, A.Array):
            raise ResolveError("session.phases must be an array", loc=phases_ast.loc)
        phases: list[IRPhase] = []
        for elt in phases_ast.elements:
            if not isinstance(elt, A.BlockExpr) or elt.name != "phase":
                raise ResolveError(
                    "session.phases entries must be `phase { ... }` blocks",
                    loc=elt.loc,
                )
            phase_fields = self._assignments_dict(elt.body)
            name_expr = phase_fields.get("name")
            duration_expr = phase_fields.get("duration")
            if name_expr is None or duration_expr is None:
                raise ResolveError(
                    "session phase needs both `name` and `duration` fields",
                    loc=elt.loc,
                )
            if not isinstance(name_expr, A.StringLit):
                raise ResolveError("phase.name must be a string", loc=name_expr.loc)
            if not isinstance(duration_expr, A.NumberLit) or duration_expr.unit not in ("ms", "s", "min"):
                raise ResolveError(
                    "phase.duration must be a numeric literal in ms/s/min",
                    loc=duration_expr.loc,
                )
            duration_ms = _to_milliseconds(duration_expr)
            output_muted = self._bool_field(phase_fields, "output_muted", default=False)
            phases.append(
                IRPhase(
                    name=name_expr.value,
                    duration_ms=duration_ms,
                    output_muted=output_muted,
                    loc=elt.loc,
                )
            )
        return IRSession(phases=tuple(phases), loc=self.session_ast.loc)

    # -- Expression resolution ---------------------------------------------

    def _resolve_value_expr(self, expr: A.Expr) -> IRExpr:
        """Resolve an expression in *value* context — no stream refs expected.

        Used for meta fields, control defaults, range bounds, etc.
        """
        if isinstance(expr, A.NumberLit):
            return IRNumberLit(value=expr.value, dims=unit_dims(expr.unit), unit=expr.unit, loc=expr.loc)
        if isinstance(expr, A.StringLit):
            return IRStringLit(value=expr.value, loc=expr.loc)
        if isinstance(expr, A.BoolLit):
            return IRBoolLit(value=expr.value, loc=expr.loc)
        if isinstance(expr, A.Array):
            return IRArray(
                elements=tuple(self._resolve_value_expr(e) for e in expr.elements),
                loc=expr.loc,
            )
        if isinstance(expr, A.Tuple):
            return IRTuple(
                elements=tuple(self._resolve_value_expr(e) for e in expr.elements),
                loc=expr.loc,
            )
        if isinstance(expr, A.BlockExpr):
            inner: dict[str, IRExpr] = {}
            for s in expr.body:
                if isinstance(s, A.Assignment):
                    inner[s.target] = self._resolve_value_expr(s.value)
            return IRBlockExpr(kind=expr.name, fields=inner, loc=expr.loc)
        if isinstance(expr, A.NameRef):
            # In value context a bare name is unusual; some control
            # bodies reference other controls. Best-effort: treat as a
            # control ref or error.
            if expr.name in self.controls:
                return IRControlRef(
                    target=f"control/{expr.name}",
                    dims=self.controls[expr.name].dims,
                    loc=expr.loc,
                )
            raise ResolveError(
                f"unknown identifier {expr.name!r} in value context",
                loc=expr.loc,
            )
        # Fall through: try stream-expression resolution. Some value
        # contexts (like control defaults) accept arithmetic.
        return self._resolve_stream_expr(expr)

    def _resolve_stream_expr(self, expr: A.Expr) -> IRExpr:
        """Resolve an expression in *stream-producing* context.

        String literals that match a known name resolve to references;
        otherwise they're string values (which is fine in some sub-
        positions but produces a type error if a stream was expected).
        """
        if isinstance(expr, A.NumberLit):
            return IRNumberLit(value=expr.value, dims=unit_dims(expr.unit), unit=expr.unit, loc=expr.loc)
        if isinstance(expr, A.BoolLit):
            return IRBoolLit(value=expr.value, loc=expr.loc)
        if isinstance(expr, A.StringLit):
            return self._resolve_string_as_reference(expr)
        if isinstance(expr, A.NameRef):
            return self._resolve_name_ref(expr)
        if isinstance(expr, A.Call):
            return self._resolve_call(expr)
        if isinstance(expr, A.Array):
            return IRArray(
                elements=tuple(self._resolve_stream_expr(e) for e in expr.elements),
                loc=expr.loc,
            )
        if isinstance(expr, A.Tuple):
            return IRTuple(
                elements=tuple(self._resolve_stream_expr(e) for e in expr.elements),
                loc=expr.loc,
            )
        if isinstance(expr, A.BinaryOp):
            return self._resolve_binary(expr)
        if isinstance(expr, A.Conditional):
            return self._resolve_conditional(expr)
        if isinstance(expr, A.MemberAccess):
            return self._resolve_member_access(expr)
        if isinstance(expr, A.BlockExpr):
            inner: dict[str, IRExpr] = {}
            for s in expr.body:
                if isinstance(s, A.Assignment):
                    inner[s.target] = self._resolve_value_expr(s.value)
            return IRBlockExpr(kind=expr.name, fields=inner, loc=expr.loc)
        raise ResolveError(
            f"unhandled expression type {type(expr).__name__}",
            loc=getattr(expr, "loc", None),
        )

    def _resolve_string_as_reference(self, expr: A.StringLit) -> IRExpr:
        """Classify a string-lit per SPEC §5.4 — reference vs value."""
        name = expr.value
        if name in self.inputs:
            return IRStreamRef(
                target=f"input/{name}",
                stream_type=self.inputs[name].stream_type,
                loc=expr.loc,
            )
        if name in self.derives:
            return IRStreamRef(
                target=f"derive/{name}",
                stream_type=self.derives[name].stream_type,
                loc=expr.loc,
            )
        if name in self.thresholds:
            return IRThresholdRef(
                target=f"threshold/{name}",
                stream_type=self.thresholds[name].stream_type,
                loc=expr.loc,
            )
        return IRStringLit(value=name, loc=expr.loc)

    def _resolve_name_ref(self, expr: A.NameRef) -> IRExpr:
        if expr.name == "reward":
            # Without an immediate member-access this is half-formed;
            # but the parent MemberAccess handler is what usually drives
            # this. Return a sentinel that the caller adapts.
            return _RewardSentinel(loc=expr.loc)
        if expr.name in self.controls:
            return IRControlRef(
                target=f"control/{expr.name}",
                dims=self.controls[expr.name].dims,
                loc=expr.loc,
            )
        raise ResolveError(
            f"unknown identifier {expr.name!r}",
            loc=expr.loc,
        )

    def _resolve_member_access(self, expr: A.MemberAccess) -> IRExpr:
        # Special-case reward fields: `reward.continuous`, `reward.event`,
        # `reward.event.holds`.
        path = _collect_member_path(expr)
        if path is not None and path[0] == "reward":
            return self._resolve_reward_field(path[1:], expr.loc)
        raise ResolveError(
            "member access is only supported on `reward` (e.g. `reward.event.holds`)",
            loc=expr.loc,
        )

    def _resolve_reward_field(self, parts: tuple[str, ...], loc: Loc | None) -> IRRewardField:
        if self.reward_ir is None:
            raise ResolveError(
                "`reward.*` referenced before reward is declared",
                loc=loc,
            )
        if parts == ("continuous",):
            if self.reward_ir.continuous is None:
                raise ResolveError("reward.continuous is not declared in this protocol", loc=loc)
            return IRRewardField(
                field_path="continuous",
                stream_type=_expr_stream_type(self.reward_ir.continuous),
                loc=loc,
            )
        if parts == ("event",):
            if self.reward_ir.event is None:
                raise ResolveError("reward.event is not declared in this protocol", loc=loc)
            return IRRewardField(field_path="event", stream_type=EVENT_STREAM, loc=loc)
        if parts == ("event", "holds"):
            if self.reward_ir.event is None:
                raise ResolveError("reward.event is not declared in this protocol", loc=loc)
            return IRRewardField(field_path="event.holds", stream_type=BOOLEAN_STREAM, loc=loc)
        raise ResolveError(
            f"unknown reward field path {'.'.join(parts)!r}",
            loc=loc,
        )

    def _resolve_binary(self, expr: A.BinaryOp) -> IRBinaryOp:
        left = self._resolve_stream_expr(expr.left)
        right = self._resolve_stream_expr(expr.right)
        result_type = _binop_type(expr.op, left, right, expr.loc)
        return IRBinaryOp(op=expr.op, left=left, right=right, stream_type=result_type, loc=expr.loc)

    def _resolve_conditional(self, expr: A.Conditional) -> IRConditional:
        cond = self._resolve_stream_expr(expr.cond)
        cond_type = _expr_stream_type(cond)
        if cond_type != BOOLEAN_STREAM and not (
            isinstance(cond, IRRewardField) and cond.stream_type == BOOLEAN_STREAM
        ):
            # `reward.event.holds` is boolean; everything else must
            # produce a boolean stream too.
            if cond_type.value_kind != "boolean":
                raise ResolveError(
                    f"ternary condition must produce a boolean stream, got {cond_type}",
                    loc=expr.cond.loc,
                )
        then_ = self._resolve_stream_expr(expr.then_branch)
        else_ = self._resolve_stream_expr(expr.else_branch)
        # Result type = then_'s type (assume both branches compatible).
        return IRConditional(
            cond=cond,
            then_branch=then_,
            else_branch=else_,
            stream_type=_expr_stream_type(then_),
            loc=expr.loc,
        )

    def _resolve_call(
        self,
        call: A.Call,
        *,
        _stream_input_hint: StreamType | None = None,
    ) -> IRCall:
        """Resolve a primitive (or custom-primitive) call.

        `_stream_input_hint` is used for pipeline staging and inhibit
        threshold construction — situations where the input stream
        isn't visible from the call's arg list alone.
        """
        spec = P.lookup(call.callee)
        # Resolve args first (children).
        resolved_args: list[IRArg] = []
        for arg in call.args:
            resolved_value = self._resolve_stream_expr(arg.value)
            resolved_args.append(IRArg(name=arg.name, value=resolved_value, loc=arg.loc))

        if spec is None:
            # Custom primitive?
            if call.callee in self.customs:
                # Custom primitives are untyped for now — Phase 0b
                # doesn't parse the type string. Treat as a scalar
                # voltage stream by default.
                output_type = scalar_stream(VOLTAGE)
                return IRCall(
                    callee=call.callee,
                    primitive=None,
                    args=tuple(resolved_args),
                    stream_type=output_type,
                    loc=call.loc,
                )
            raise ResolveError(
                f"unknown primitive {call.callee!r} (not in stdlib or `custom` decls)",
                loc=call.loc,
            )

        # Resolve output type using the matching signature.
        output_type = _compute_call_output(
            spec,
            resolved_args,
            _stream_input_hint,
            call.loc,
        )

        # Per-primitive validation of `kind`-style enumerated string args.
        _validate_kind_args(spec, resolved_args, call.loc)

        # Account budget.
        self._state_kb += spec.budget.state_kb
        self._worst_case_us += spec.budget.worst_case_us

        return IRCall(
            callee=call.callee,
            primitive=spec,
            args=tuple(resolved_args),
            stream_type=output_type,
            loc=call.loc,
        )

    def _lower_pipeline(self, from_expr: A.StringLit, pipeline: A.Array) -> IRExpr:
        """Desugar `derive "X" { from = "Y"; pipeline = [p1, p2, ...] }`
        into a nested chain `pN(... p2(p1("Y", ...), ...), ...)`."""
        # Stage 0: the input stream reference.
        current: IRExpr = self._resolve_string_as_reference(from_expr)
        if not isinstance(current, IRStreamRef):
            raise ResolveError(
                f"pipeline `from = {from_expr.value!r}` does not name a known stream",
                loc=from_expr.loc,
            )
        # Each stage: synthesise a Call where the first positional arg
        # is the previous stage's output.
        for stage in pipeline.elements:
            if not isinstance(stage, A.Call):
                raise ResolveError(
                    "pipeline entries must be primitive calls",
                    loc=stage.loc,
                )
            current = self._lower_pipeline_stage(stage, current)
        return current

    def _lower_pipeline_stage(self, call: A.Call, prev: IRExpr) -> IRCall:
        """Build an IRCall with `prev` threaded as the implicit first input."""
        spec = P.lookup(call.callee)
        # Resolve the explicit args first.
        resolved_args: list[IRArg] = [IRArg(name=None, value=prev, loc=call.loc)]
        for arg in call.args:
            resolved_args.append(
                IRArg(
                    name=arg.name,
                    value=self._resolve_stream_expr(arg.value),
                    loc=arg.loc,
                )
            )
        if spec is None:
            raise ResolveError(
                f"unknown primitive {call.callee!r} in pipeline",
                loc=call.loc,
            )
        prev_type = _expr_stream_type(prev)
        output_type = _compute_call_output(
            spec,
            resolved_args,
            _stream_input_hint=prev_type,
            loc=call.loc,
        )
        _validate_kind_args(spec, resolved_args, call.loc)
        self._state_kb += spec.budget.state_kb
        self._worst_case_us += spec.budget.worst_case_us
        return IRCall(
            callee=call.callee,
            primitive=spec,
            args=tuple(resolved_args),
            stream_type=output_type,
            loc=call.loc,
        )

    # -- Field-extraction helpers -------------------------------------------

    def _assignments_dict(self, body: tuple) -> dict[str, A.Expr]:
        out: dict[str, A.Expr] = {}
        for s in body:
            if isinstance(s, A.Assignment):
                out[s.target] = s.value
        return out

    def _string_field(
        self,
        fields: dict[str, A.Expr],
        name: str,
        *,
        default: str | None = None,
    ) -> str | None:
        if name not in fields:
            return default
        expr = fields[name]
        if not isinstance(expr, A.StringLit):
            raise ResolveError(f"field {name!r} must be a string literal", loc=expr.loc)
        return expr.value

    def _bool_field(self, fields: dict[str, A.Expr], name: str, *, default: bool) -> bool:
        if name not in fields:
            return default
        expr = fields[name]
        if not isinstance(expr, A.BoolLit):
            raise ResolveError(f"field {name!r} must be a boolean", loc=expr.loc)
        return expr.value


# ---------------------------------------------------------------------------
# Free-standing helpers
# ---------------------------------------------------------------------------


class _RewardSentinel(IRExpr):
    """Placeholder for a bare `reward` NameRef that's expected to be
    followed by a member-access chain. The MemberAccess handler captures
    this case directly; this sentinel exists only for cases where it
    leaks (which would be a malformed protocol)."""

    __slots__ = ()

    def __init__(self, loc: Loc | None = None):
        object.__setattr__(self, "loc", loc)


def _expr_stream_type(expr: IRExpr) -> StreamType:
    """Best-effort stream type for any IR expression."""
    if hasattr(expr, "stream_type"):
        return expr.stream_type  # type: ignore[attr-defined]
    if isinstance(expr, IRNumberLit):
        return scalar_stream(expr.dims)
    if isinstance(expr, IRBoolLit):
        return BOOLEAN_STREAM
    if isinstance(expr, IRStringLit):
        # Plain strings don't have a stream type per se; return a
        # dimensionless scalar as a soft fallback. Type-mismatch checks
        # higher up catch real mistakes.
        return scalar_stream(DIMENSIONLESS)
    if isinstance(expr, IRControlRef):
        return scalar_stream(expr.dims)
    return scalar_stream(DIMENSIONLESS)


def _compute_call_output(
    spec: PrimitiveSpec,
    args: list[IRArg],
    _stream_input_hint: StreamType | None,
    loc: Loc | None,
) -> StreamType:
    """Run the primitive's output factory with a `ResolvedArg` dict."""
    arg_map: dict[str, ResolvedArg] = {}

    # Map positional args to the signature's positional params.
    sig = spec.signatures[0]  # for overloaded primitives, pick the first
    # for matching by arg name presence
    if len(spec.signatures) > 1:
        sig = _pick_signature(spec, args, loc)

    pos_params = [p for p in sig.params if p.name not in {a.name for a in args if a.name}]
    pos_args = [a for a in args if a.name is None]
    for param, arg in zip(pos_params, pos_args):
        arg_map[param.name] = _to_resolved_arg(arg.value)

    for arg in args:
        if arg.name is not None:
            arg_map[arg.name] = _to_resolved_arg(arg.value)

    # Inject the stream-input hint if no `__input__`/`input` arg explicitly
    # supplied. This handles pipeline-threaded inputs and inhibit thresholds.
    if _stream_input_hint is not None:
        if "__input__" not in arg_map and "input" not in arg_map and pos_args:
            arg_map["__input__"] = ResolvedArg(
                name=None, stream_type=_stream_input_hint, dims=None
            )

    return sig.output(arg_map)


def _pick_signature(spec: PrimitiveSpec, args: list[IRArg], loc: Loc | None) -> P.Signature:
    """Disambiguate overloads by named-arg presence (e.g. bandpass band/center)."""
    named_arg_names = {a.name for a in args if a.name}
    for sig in spec.signatures:
        required_named = {p.name for p in sig.params if p.required and p.name not in {"__input__"}}
        # Crude match: every required named param either appears in
        # named args OR can be filled positionally.
        positional_capacity = len([a for a in args if a.name is None])
        if required_named.issubset(named_arg_names) or len(sig.params) <= positional_capacity + len(named_arg_names):
            return sig
    return spec.signatures[0]


_KIND_ENUMS: dict[str, tuple[str, ...]] = {
    "bandpass": P.BANDPASS_KINDS,
    "hilbert": P.HILBERT_KINDS,
}


def _validate_kind_args(spec: P.PrimitiveSpec, args: list[IRArg], loc: Loc | None) -> None:
    """For primitives that take a `kind: <enum>` parameter, verify the
    value is one of the permitted strings. Catches typos at static-check
    time rather than letting them surface as runtime ValueError in the
    evaluator."""
    allowed = _KIND_ENUMS.get(spec.name)
    if allowed is None:
        return
    for arg in args:
        if arg.name != "kind":
            continue
        if not isinstance(arg.value, IRStringLit):
            raise ResolveError(
                f"{spec.name}(kind: ...) requires a string literal, "
                f"got {type(arg.value).__name__}",
                loc=arg.loc,
            )
        if arg.value.value not in allowed:
            raise ResolveError(
                f"{spec.name}(kind: {arg.value.value!r}) is not a supported "
                f"filter family; allowed: {list(allowed)}",
                loc=arg.loc,
            )


def _to_resolved_arg(expr: IRExpr) -> ResolvedArg:
    if isinstance(expr, (IRStreamRef, IRThresholdRef, IRRewardField)):
        return ResolvedArg(name=None, stream_type=expr.stream_type, dims=None)
    if isinstance(expr, IRCall):
        return ResolvedArg(name=None, stream_type=expr.stream_type, dims=None)
    if isinstance(expr, IRBinaryOp):
        return ResolvedArg(name=None, stream_type=expr.stream_type, dims=None)
    if isinstance(expr, IRNumberLit):
        return ResolvedArg(name=None, stream_type=None, dims=expr.dims)
    if isinstance(expr, IRControlRef):
        return ResolvedArg(name=None, stream_type=None, dims=expr.dims)
    if isinstance(expr, IRBoolLit):
        return ResolvedArg(name=None, stream_type=BOOLEAN_STREAM, dims=None)
    return ResolvedArg(name=None, stream_type=None, dims=None)


def _binop_type(op: str, left: IRExpr, right: IRExpr, loc: Loc | None) -> StreamType:
    lt = _expr_stream_type(left)
    rt = _expr_stream_type(right)
    if op in ("+", "-"):
        if not dims_compatible_for_add(lt.dimensions, rt.dimensions):
            raise ResolveError(
                f"`{op}` requires matching dimensions, got {lt} vs {rt}",
                loc=loc,
            )
        return scalar_stream(lt.dimensions)
    if op == "*":
        return scalar_stream(lt.dimensions * rt.dimensions)
    if op == "/":
        return scalar_stream(lt.dimensions / rt.dimensions)
    if op in ("<", ">", "<=", ">=", "==", "!="):
        if not dims_compatible_for_add(lt.dimensions, rt.dimensions):
            raise ResolveError(
                f"`{op}` requires matching dimensions, got {lt} vs {rt}",
                loc=loc,
            )
        return BOOLEAN_STREAM
    raise ResolveError(f"unknown binary operator {op!r}", loc=loc)


def _collect_member_path(expr: A.Expr) -> tuple[str, ...] | None:
    """Flatten a chain of MemberAccess back to `(root_name, m1, m2, ...)`.
    Returns None if the root isn't a NameRef."""
    parts: list[str] = []
    current = expr
    while isinstance(current, A.MemberAccess):
        parts.append(current.member)
        current = current.target
    if isinstance(current, A.NameRef):
        parts.append(current.name)
        parts.reverse()
        return tuple(parts)
    return None


def _collect_upstream(expr: IRExpr) -> set[str]:
    """Walk an IR expression collecting canonical names of stream refs."""
    out: set[str] = set()

    def visit(e: IRExpr) -> None:
        if isinstance(e, IRStreamRef):
            out.add(e.target)
        elif isinstance(e, IRThresholdRef):
            out.add(e.target)
        elif isinstance(e, IRControlRef):
            out.add(e.target)
        elif isinstance(e, IRRewardField):
            out.add(f"reward/{e.field_path}")
        elif isinstance(e, IRCall):
            for arg in e.args:
                visit(arg.value)
        elif isinstance(e, IRBinaryOp):
            visit(e.left)
            visit(e.right)
        elif isinstance(e, IRConditional):
            visit(e.cond)
            visit(e.then_branch)
            visit(e.else_branch)
        elif isinstance(e, IRArray) or isinstance(e, IRTuple):
            for elt in e.elements:
                visit(elt)
        elif isinstance(e, IRBlockExpr):
            for v in e.fields.values():
                visit(v)

    visit(expr)
    return out


def _control_kind_dims(kind: str, loc: Loc | None) -> Dimensions:
    if kind == "frequency":
        return FREQUENCY
    if kind == "duration":
        return TIME
    if kind == "voltage":
        return VOLTAGE
    if kind == "percent":
        return DIMENSIONLESS
    if kind in ("boolean", "enum"):
        return DIMENSIONLESS
    if kind == "placement":
        return DIMENSIONLESS   # categorical (channel identifiers); no unit arithmetic
    raise ResolveError(f"unknown control type {kind!r}", loc=loc)


def _to_milliseconds(n: A.NumberLit) -> float:
    if n.unit == "ms":
        return n.value
    if n.unit == "s":
        return n.value * 1000.0
    if n.unit == "min":
        return n.value * 60_000.0
    raise ResolveError(f"unexpected duration unit {n.unit!r}", loc=n.loc)


def _extract_duration_ms(action_call: IRCall, param_name: str) -> float | None:
    for arg in action_call.args:
        if arg.name == param_name and isinstance(arg.value, IRNumberLit):
            n = arg.value
            if n.dims == TIME:
                # Convert based on the original AST unit. We don't have
                # that here; the resolver lost it. Use a heuristic on
                # magnitude — durations under 1.0 are seconds, otherwise
                # they're ms. This will be replaced when IRNumberLit
                # tracks its surface unit.
                # Simpler: store the value in seconds at parse time and
                # multiply here. For now, return value as-is in ms scale.
                return n.value
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(
    file_ast: A.File,
    amp: AmpProfile | None = None,
    *,
    bindings: dict[str, object] | None = None,
    parent_loader: ParentLoader | None = None,
) -> IRProtocol:
    """Resolve a parsed `File` AST into an `IRProtocol`.

    Pass an `AmpProfile` to validate hardware requirements (§6.3) and
    to compute the chosen sample rate. Pass `None` to skip those checks.

    Pass ``bindings`` to override placement control values at resolve time.
    Keys are control names; values are concrete channel strings (for
    ``active`` placements).  Omitted names fall back to the control's
    declared ``default``.

    Pass a `parent_loader` (typically built via
    `refrain.compose.filesystem_loader([...])`) if the protocol uses
    `extends` to inherit from a parent. Composition runs as a pre-pass
    on the AST; the resolver then operates on the merged result.
    """
    try:
        composed = compose(file_ast, parent_loader)
    except ComposeError as exc:
        # Re-raise as ResolveError so callers see a single error type.
        # `exc.args[0]` is the bare message without `ResolveError`'s
        # line-prefix re-wrap; passing `loc` reconstructs it correctly.
        msg = exc.args[0] if exc.args else str(exc)
        if exc.loc is not None and msg.startswith(f"line {exc.loc.line}:{exc.loc.col}: "):
            msg = msg.split(": ", 1)[1]
        raise ResolveError(msg, loc=exc.loc) from exc
    return _Resolver(composed, amp, bindings).resolve()


__all__ = ["resolve", "ResolveError"]
