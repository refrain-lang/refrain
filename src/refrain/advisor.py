# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Protocol autopilot advisor (SPEC §7.10).

A pure, deterministic state machine. The Evaluator hands it one `ChunkFacts`
per chunk; it keeps exactly one current advice result (`advice()`) and an
audit-event log (`drain_events()`). Time is counted in samples. Mirrored
line-for-line by `refrain-core/src/advisor.rs`, parity-gated by
`refrain-core/tests/advisor_parity.rs`. Change both or neither — including
every message string.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .advisor_trace import trace_protocol

ADVISOR_VERSION = "1"
DEFAULT_TARGET = (0.50, 0.75)
DEFAULT_WATCH_S = 120.0
DEFAULT_BETWEEN_S = 180.0
DEFAULT_SETTLE_S = 60.0
DEFAULT_GUARD_MAX = 0.15
UNITS = {"number": "", "percent": "%", "voltage": "uV", "frequency": "Hz"}
_BLOCKING = ("not_training_phase", "equipment_settling", "guard")
_NOT_TRAINING = "Advice paused: not a training phase."


class AdviceError(ValueError):
    """apply/dismiss with an id that is not the current advice, or an
    automatic application the protocol does not allow."""


# --- deterministic formatting (identical formulas in advisor.rs) -----------

def r6(x: float) -> float:
    """Round half away from zero to 6 decimal places."""
    if x < 0:
        return -r6(-x)
    return math.floor(x * 1e6 + 0.5) / 1e6


def pct(x: float) -> int:
    return int(math.floor(x * 100.0 + 0.5))


def mmss(seconds: float) -> str:
    s = int(math.floor(seconds))
    return f"{s // 60}:{s % 60:02d}"


def fmt_value(v: float, decimals: int, units: str) -> str:
    text = f"{v:.{decimals}f}"
    if units == "%":
        return text + "%"
    return f"{text} {units}" if units else text


def percentile_of(values: list[float], p: float) -> float:
    s = sorted(values)
    n = len(s)
    if n == 1:
        return s[0]
    rank = p / 100.0 * (n - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def snap(v: float, round_to: float | None) -> float:
    if round_to is None:
        return v
    return math.floor(v / round_to + 0.5) * round_to


def _default_samples(seconds: float, sr: float) -> int:
    return max(1, int(math.floor(seconds * sr + 0.5)))


# --- inputs ----------------------------------------------------------------

@dataclass
class ChunkFacts:
    """What the Evaluator observed during one chunk (all arrays length n)."""

    n: int
    running: bool                 # evaluator state == "run"
    phase_index: int              # -1 when the protocol has no session phases
    phase_name: str | None
    output_muted: bool            # phase mute or failed-seed mute
    clock_frozen: bool            # host paused the session clock
    bundle: str | None            # active reward bundle; None = top-level reward
    muted: Any                    # bool[n] combined inhibit gate
    inhibits: dict[str, Any]      # bare inhibit name -> bool[n]
    checks: list[Any]             # active reward's sub-conditions, bool[n] each
    events: Any                   # dwell rising edges, bool[n]
    derive_samples: dict[str, Any] = field(default_factory=dict)  # "derive/x" -> float[n]


def facts_from_json(op: dict) -> ChunkFacts:
    """Expand the compact scenario encoding: a bool/float stands for a constant
    array of length n; `events` is the count of leading true samples."""
    n = int(op["n"])

    def bools(v: Any) -> np.ndarray:
        return np.asarray(v, dtype=bool) if isinstance(v, list) else np.full(n, bool(v))

    def floats(v: Any) -> np.ndarray:
        return (np.asarray(v, dtype=np.float64) if isinstance(v, list)
                else np.full(n, float(v)))

    ev = np.zeros(n, dtype=bool)
    ev[: int(op.get("events", 0))] = True
    return ChunkFacts(
        n=n, running=bool(op["running"]), phase_index=int(op["phase_index"]),
        phase_name=op.get("phase_name"), output_muted=bool(op["output_muted"]),
        clock_frozen=bool(op["clock_frozen"]), bundle=op.get("bundle"),
        muted=bools(op["muted"]),
        inhibits={k: bools(v) for k, v in (op.get("inhibits") or {}).items()},
        checks=[bools(c) for c in op.get("checks") or []],
        events=ev,
        derive_samples={k: floats(v) for k, v in (op.get("derive_samples") or {}).items()},
    )


# --- configuration (built once from IR-JSON) --------------------------------

@dataclass(frozen=True)
class KnobPolicy:
    control: str
    label: str
    units: str
    strategy: str
    fixes: str
    higher_is: str
    apply: str
    lo: float
    hi: float
    round_to: float | None
    decimals: int
    between: int
    step: float | None
    from_entity: str | None
    window: int | None
    percentile: float | None
    citations: tuple[str, ...]


@dataclass(frozen=True)
class CheckInfo:
    name: str | None
    knob: str | None
    higher_is: str | None


@dataclass(frozen=True)
class BundleInfo:
    combine: str
    checks: tuple[CheckInfo, ...]


@dataclass(frozen=True)
class Config:
    target: tuple[float, float]
    phases: frozenset[str] | None
    watch: int
    between: int
    settle: int
    guards: dict[str, tuple[float, str | None]]
    limiters: dict[str, str]
    tighten_first: tuple[str, ...]
    knobs: dict[str, KnobPolicy]
    knob_for_check: dict[str, str]
    bundles: dict[str, BundleInfo]
    relevant: frozenset[str]
    inhibit_controls: frozenset[str]
    labels: dict[str, str]
    units: dict[str, str]
    defaults: dict[str, float]
    provenance: dict[str, Any] | None


def build_config(ir: dict, sr: float) -> Config:
    ap = ir.get("autopilot") or {}
    tr = trace_protocol(ir)
    controls = ir.get("controls") or {}
    labels: dict[str, str] = {}
    units: dict[str, str] = {}
    defaults: dict[str, float] = {}
    for name, c in controls.items():
        labels[name] = c.get("label") or name
        units[name] = UNITS.get(c.get("type_kind"), "")
        d = c.get("default")
        if isinstance(d, dict) and d.get("node") == "number":
            defaults[name] = float(d["value"])
    phase_list = (ir.get("session") or {}).get("phases") or []
    if ap.get("phases") is not None:
        phases: frozenset[str] | None = frozenset(ap["phases"])
    elif phase_list:
        phases = frozenset(p["name"] for p in phase_list if not p.get("output_muted"))
    else:
        phases = None

    def samples(key: str, default_s: float) -> int:
        v = ap.get(key)
        return int(v) if v is not None else _default_samples(default_s, sr)

    between = samples("between_moves_samples", DEFAULT_BETWEEN_S)
    declared = ap.get("guards") or {}
    guards = {}
    for name in sorted(ir.get("inhibits") or {}):
        g = declared.get(name)
        guards[name] = (float(g["max"]), g.get("say")) if g else (DEFAULT_GUARD_MAX, None)
    knobs: dict[str, KnobPolicy] = {}
    for name, c in controls.items():
        p = c.get("autopilot")
        if not p:
            continue
        kb = p.get("between_moves_samples")
        knobs[name] = KnobPolicy(
            control=name, label=p.get("say") or labels[name], units=units[name],
            strategy=p["strategy"], fixes=p["fixes"], higher_is=p["higher_is"],
            apply=p["apply"], lo=float(p["limits"][0]), hi=float(p["limits"][1]),
            round_to=p.get("round_to"), decimals=int(p.get("decimals", 2)),
            between=int(kb) if kb is not None else between,
            step=p.get("step"), from_entity=p.get("from"),
            window=p.get("window_samples"), percentile=p.get("percentile"),
            citations=tuple(p.get("citation") or ()),
        )
    bundles = {
        key: BundleInfo(
            combine=b["combine"],
            checks=tuple(CheckInfo(e["name"], e["knob"], e["higher_is"]) for e in b["checks"]),
        )
        for key, b in tr["bundles"].items()
    }
    provenance = None
    if ir.get("autopilot"):
        provenance = {"evidence": ap["evidence"], "citation": list(ap["citation"]),
                      "rationale": ap["rationale"], "reviewed": ap.get("reviewed")}
    return Config(
        target=tuple(ap["reward_target"]) if ap.get("reward_target") else DEFAULT_TARGET,
        phases=phases,
        watch=samples("watch_samples", DEFAULT_WATCH_S),
        between=between,
        settle=samples("equipment_settle_samples", DEFAULT_SETTLE_S),
        guards=guards,
        limiters={k: v["say"] for k, v in (ap.get("limiters") or {}).items()},
        tighten_first=tuple(ap.get("tighten_first") or ()),
        knobs=knobs,
        knob_for_check={p.fixes: name for name, p in knobs.items()},
        bundles=bundles,
        relevant=frozenset(tr["check_controls"]) | frozenset(tr["inhibit_controls"]),
        inhibit_controls=frozenset(tr["inhibit_controls"]),
        labels=labels, units=units, defaults=defaults, provenance=provenance,
    )


# --- evidence ---------------------------------------------------------------

@dataclass
class _Bucket:
    start: int
    end: int
    n_train: int
    n_clean: int
    n_cond: int
    checks: list[int]
    guards: dict[str, int]
    events: int


@dataclass
class _Evidence:
    start: int
    end: int
    n_train: int
    n_clean: int
    reward_rate: float | None
    check_rates: list[float]
    guard_rates: dict[str, float]
    chimes_per_min: float


# --- the advisor ------------------------------------------------------------

class Advisor:
    def __init__(self, ir: dict, sample_rate_hz: float) -> None:
        self.sr = float(sample_rate_hz)
        self.cfg = build_config(ir, self.sr)
        self.values: dict[str, float] = dict(self.cfg.defaults)
        self.now = 0
        self.buckets: list[_Bucket] = []
        self.rebase: dict[str, list[float]] = {
            name: [] for name, p in sorted(self.cfg.knobs.items()) if p.strategy == "rebaseline"}
        self.last_phase_index: int | None = None
        self.in_training = False
        self.bundle_key = ""
        self.settle_until = 0
        self.last_move_at: int | None = None
        self.dismissed: dict[tuple[str, str], int] = {}
        self.reversal: dict[str, Any] | None = None
        self.counter = 0
        self.standing: tuple[tuple, str] | None = None
        self.events: list[dict] = []
        self.current = self._result("hold", "not_training_phase", _NOT_TRAINING)

    # ---- host-facing ----

    def rebaseline_sources(self) -> list[str]:
        return sorted({self.cfg.knobs[c].from_entity for c in self.rebase})

    def advice(self) -> dict:
        return copy.deepcopy(self.current)

    def drain_events(self) -> list[dict]:
        out, self.events = self.events, []
        return out

    def feed(self, f: ChunkFacts) -> None:
        start = self.now
        self.now += int(f.n)
        phase_ok = self.cfg.phases is None or f.phase_name in self.cfg.phases
        if f.phase_index != self.last_phase_index:
            self.last_phase_index = f.phase_index
            if phase_ok:
                self._reset()
        self.in_training = bool(f.running and phase_ok and not f.output_muted
                                and not f.clock_frozen)
        self.bundle_key = f.bundle or ""
        if self.in_training and start >= self.settle_until:
            self._ingest(f, start)
        self._evaluate()

    def apply(self, advice_id: str, by: str) -> tuple[str, float, dict]:
        if by not in ("clinician", "autopilot"):
            raise ValueError(f"by must be 'clinician' or 'autopilot', got {by!r}")
        res = self.current
        if res["id"] != advice_id or res["state"] != "adjust":
            raise AdviceError(f"advice {advice_id!r} is not the current suggestion")
        ctl = res["control"]
        if by == "autopilot" and not ctl["auto_allowed"]:
            raise AdviceError(
                f"advice {advice_id!r} changes {ctl['name']!r}, which this protocol allows "
                "only as a suggestion")
        control, to = ctl["name"], ctl["proposed"]
        frm = self.values.get(control)
        self.values[control] = to
        ev = self._emit("applied", id=advice_id, control=control,
                        **{"from": r6(frm) if frm is not None else None, "to": to}, by=by)
        if res["reason"] == "reversal":
            self.reversal = None
        else:
            self.reversal = {"control": control, "from": frm, "direction": ctl["direction"],
                             "checked": False, "fire": False}
        self.last_move_at = self.now
        self.standing = None
        self._reset()
        self._evaluate()
        return control, to, ev

    def dismiss(self, advice_id: str) -> dict:
        res = self.current
        if res["id"] != advice_id or res["state"] not in ("adjust", "hint"):
            raise AdviceError(f"advice {advice_id!r} is not the current suggestion")
        ctl = res["control"]
        policy = self.cfg.knobs.get(ctl["name"])
        between = policy.between if policy is not None else self.cfg.between
        self.dismissed[(ctl["name"], ctl["direction"])] = self.now + between
        if res["reason"] == "reversal":
            self.reversal = None
        ev = self._emit("dismissed", id=advice_id, control=ctl["name"])
        self.standing = None
        self._evaluate()
        return ev

    def note_control(self, control: str, value: float, source: str) -> None:
        if source not in ("manual", "seed"):
            raise ValueError(f"source must be 'manual' or 'seed', got {source!r}")
        frm = self.values.get(control)
        self.values[control] = float(value)
        if control not in self.cfg.relevant:
            return
        if source == "manual":
            self._emit("changed_manually", control=control,
                       **{"from": r6(frm) if frm is not None else None, "to": r6(float(value))})
            if self.standing is not None:
                self._emit("superseded", id=self.standing[1], reason="changed_manually")
                self.standing = None
            if self.reversal is not None and self.reversal["control"] == control:
                self.reversal = None
            self.last_move_at = self.now
        self._reset()
        self._evaluate()

    def mark_equipment_change(self) -> None:
        self._emit("equipment_change")
        self.settle_until = self.now + self.cfg.settle
        self._reset()
        self._evaluate()

    def policy(self) -> dict:
        c = self.cfg
        return {
            "advisor_version": ADVISOR_VERSION,
            "provenance": c.provenance,
            "reward_target": [r6(c.target[0]), r6(c.target[1])],
            "phases": sorted(c.phases) if c.phases is not None else None,
            "watch_s": r6(c.watch / self.sr),
            "between_moves_s": r6(c.between / self.sr),
            "equipment_settle_s": r6(c.settle / self.sr),
            "tighten_first": list(c.tighten_first),
            "guards": {g: {"max": r6(m), "say": s} for g, (m, s) in c.guards.items()},
            "limiters": dict(c.limiters),
            "controls": {
                name: {
                    "label": p.label, "units": p.units, "strategy": p.strategy,
                    "fixes": p.fixes, "higher_is": p.higher_is, "apply": p.apply,
                    "limits": [r6(p.lo), r6(p.hi)], "round_to": p.round_to, "step": p.step,
                    "percentile": p.percentile, "between_moves_s": r6(p.between / self.sr),
                    "citation": list(p.citations),
                }
                for name, p in sorted(c.knobs.items())
            },
        }

    # ---- evidence ----

    def _bundle(self) -> BundleInfo | None:
        return self.cfg.bundles.get(self.bundle_key)

    def _reset(self) -> None:
        self.buckets = []
        for buf in self.rebase.values():
            buf.clear()

    def _ingest(self, f: ChunkFacts, start: int) -> None:
        clean = ~np.asarray(f.muted, dtype=bool)
        checks = [np.asarray(c, dtype=bool) for c in f.checks]
        n_cond = 0
        if checks:
            stacked = np.stack(checks)
            b = self._bundle()
            cond = stacked.any(axis=0) if (b is not None and b.combine == "any") else stacked.all(axis=0)
            n_cond = int(np.count_nonzero(cond & clean))
        self.buckets.append(_Bucket(
            start=start, end=self.now, n_train=int(f.n),
            n_clean=int(np.count_nonzero(clean)), n_cond=n_cond,
            checks=[int(np.count_nonzero(c & clean)) for c in checks],
            guards={k: int(np.count_nonzero(np.asarray(v, dtype=bool)))
                    for k, v in f.inhibits.items()},
            events=int(np.count_nonzero(np.asarray(f.events, dtype=bool) & clean)),
        ))
        for control, buf in self.rebase.items():
            p = self.cfg.knobs[control]
            src = f.derive_samples.get(p.from_entity)
            if src is None:
                continue
            for v in np.asarray(src, dtype=np.float64)[clean]:
                if math.isfinite(v):
                    buf.append(float(v))
            if len(buf) > p.window:
                del buf[: len(buf) - p.window]
        horizon = self.now - 2 * self.cfg.watch
        self.buckets = [b for b in self.buckets if b.end > horizon]

    def _evidence(self) -> _Evidence | None:
        horizon = self.now - 2 * self.cfg.watch
        sel: list[_Bucket] = []
        clean = 0
        for b in reversed(self.buckets):
            if b.end <= horizon:
                break
            sel.append(b)
            clean += b.n_clean
            if clean >= self.cfg.watch:
                break
        if not sel:
            return None
        sel.reverse()
        n_train = sum(b.n_train for b in sel)
        n_clean = sum(b.n_clean for b in sel)
        bundle = self._bundle()
        k = len(bundle.checks) if bundle is not None else 0
        has = k > 0 and n_clean > 0 and all(len(b.checks) == k for b in sel)
        return _Evidence(
            start=sel[0].start, end=sel[-1].end, n_train=n_train, n_clean=n_clean,
            reward_rate=sum(b.n_cond for b in sel) / n_clean if has else None,
            check_rates=[sum(b.checks[i] for b in sel) / n_clean for i in range(k)] if has else [],
            guard_rates={g: (sum(b.guards.get(g, 0) for b in sel) / n_train if n_train else 0.0)
                         for g in self.cfg.guards},
            chimes_per_min=(sum(b.events for b in sel) / (n_clean / self.sr / 60.0)
                            if n_clean else 0.0),
        )

    def _check_label(self, i: int) -> str:
        b = self._bundle()
        name = b.checks[i].name if b is not None and i < len(b.checks) else None
        return name or f"check {i}"

    def _evidence_dict(self, ev: _Evidence) -> dict:
        lo, hi = self.cfg.target
        return {
            "window_start_s": r6(ev.start / self.sr),
            "window_end_s": r6(ev.end / self.sr),
            "clean_s": r6(ev.n_clean / self.sr),
            "required_s": r6(self.cfg.watch / self.sr),
            "reward_rate": r6(ev.reward_rate) if ev.reward_rate is not None else None,
            "target": [r6(lo), r6(hi)],
            "checks": {self._check_label(i): r6(r) for i, r in enumerate(ev.check_rates)},
            "guards": {g: r6(r) for g, r in ev.guard_rates.items()},
            "chimes_per_min": r6(ev.chimes_per_min),
        }

    # ---- decision ----

    def _result(self, state, reason, message, *, level="observation", limiter=None,
                control=None, evidence=None, eligible_at=None) -> dict:
        return {
            "advisor_version": ADVISOR_VERSION, "id": None, "state": state, "level": level,
            "reason": reason, "message": message, "t_s": r6(self.now / self.sr),
            "limiter": limiter, "control": control, "evidence": evidence,
            "eligible_at_s": r6(eligible_at / self.sr) if eligible_at is not None else None,
        }

    def _evaluate(self) -> None:
        res, key = self._decide()
        self._publish(res, key)

    def _decide(self) -> tuple[dict, tuple | None]:
        if not self.in_training:
            return self._result("hold", "not_training_phase", _NOT_TRAINING), None
        if self.now < self.settle_until:
            left = int(math.ceil((self.settle_until - self.now) / self.sr))
            return self._result("hold", "equipment_settling",
                                f"Settling after equipment change ({left} s left)."), None
        ev = self._evidence()
        evd = self._evidence_dict(ev) if ev is not None else None
        if ev is not None:
            worst = None
            for g in sorted(self.cfg.guards):
                mx = self.cfg.guards[g][0]
                r = ev.guard_rates[g]
                if r > mx and (worst is None or r > ev.guard_rates[worst]):
                    worst = g
            if worst is not None:
                say = self.cfg.guards[worst][1]
                rate_pct = pct(ev.guard_rates[worst])
                if say is not None:
                    msg = f"{say} ({worst} guard active {rate_pct}% of training time)."
                else:
                    msg = f"{worst} guard active {rate_pct}% of training time."
                return self._result("hold", "guard", msg, evidence=evd), None
        clean = ev.n_clean if ev is not None else 0
        if clean < self.cfg.watch:
            return self._result(
                "collecting", "collecting",
                f"Collecting clean signal ({mmss(clean / self.sr)} of {mmss(self.cfg.watch / self.sr)}).",
                evidence=evd), None
        if ev.reward_rate is None:
            return self._result("hold", "observing",
                                "Observing: this protocol has no reward condition to judge.",
                                evidence=evd), None
        rate = ev.reward_rate
        lo, hi = self.cfg.target
        head = f"Reward met {pct(rate)}% of clean time (target {pct(lo)}-{pct(hi)}%)."
        rev = self._reversal_step(rate, evd)
        if rev is not None:
            return rev
        if lo <= rate <= hi:
            return self._result("hold", "on_track", f"On track. {head}", evidence=evd), None
        return self._outside_band(ev, evd, "easier" if rate < lo else "harder", head)

    def _reversal_step(self, rate: float, evd: dict) -> tuple[dict, tuple] | None:
        r = self.reversal
        if r is None:
            return None
        if not r["checked"]:
            r["checked"] = True
            lo, hi = self.cfg.target
            worse = ((r["direction"] == "harder" and rate < lo)
                     or (r["direction"] == "easier" and rate > hi))
            if not worse:
                self.reversal = None
                return None
            r["fire"] = True
        if not r["fire"]:
            return None
        p = self.cfg.knobs[r["control"]]
        cur = self.values[p.control]
        back = "easier" if r["direction"] == "harder" else "harder"
        prev = r6(r["from"])
        msg = (f"The last change made reward worse ({pct(rate)}% of clean time). Return "
               f"{p.label} {fmt_value(cur, p.decimals, p.units)} -> "
               f"{fmt_value(prev, p.decimals, p.units)}.")
        return (self._result("adjust", "reversal", msg, level="policy",
                             control=self._control_dict(p, cur, prev, back), evidence=evd,
                             eligible_at=self.now),
                ("adjust", "reversal", p.control, back, prev))

    def _pick_check(self, ev: _Evidence, need: str) -> int:
        rates = ev.check_rates
        if need == "easier":
            best = 0
            for i in range(1, len(rates)):
                if rates[i] < rates[best]:
                    best = i
            return best
        names = [c.name for c in self._bundle().checks]
        for name in self.cfg.tighten_first:
            if name in names:
                control = self.cfg.knob_for_check.get(name)
                if control is not None and self._propose(self.cfg.knobs[control], "harder") is not None:
                    return names.index(name)
        best = 0
        for i in range(1, len(rates)):
            if rates[i] > rates[best]:
                best = i
        return best

    def _propose(self, p: KnobPolicy, need: str) -> float | None:
        cur = self.values[p.control]
        up = (need == "easier") == (p.higher_is == "easier")
        if p.strategy == "fixed_step":
            v = cur + p.step if up else cur - p.step
        elif p.strategy == "proportional_step":
            v = cur * (1.0 + p.step) if up else cur * (1.0 - p.step)
        else:
            buf = self.rebase.get(p.control, [])
            if len(buf) < p.window:
                return None
            v = percentile_of(buf, p.percentile)
        v = r6(min(max(snap(v, p.round_to), p.lo), p.hi))
        if abs(v - cur) < 1e-9 or (v > cur) != up:
            return None
        return v

    def _control_dict(self, p: KnobPolicy, cur: float, proposed: float | None, need: str) -> dict:
        return {
            "name": p.control, "label": p.label, "units": p.units, "round_to": p.round_to,
            "current": r6(cur), "proposed": proposed, "direction": need, "strategy": p.strategy,
            "auto_allowed": p.apply == "auto" and p.strategy != "rebaseline",
        }

    def _eligible_at(self, p: KnobPolicy, need: str) -> int | None:
        t = self.last_move_at + p.between if self.last_move_at is not None else None
        d = self.dismissed.get((p.control, need))
        if d is not None and (t is None or d > t):
            t = d
        return t

    def _outside_band(self, ev: _Evidence, evd: dict, need: str, head: str):
        idx = self._pick_check(ev, need)
        info = self._bundle().checks[idx]
        label = self._check_label(idx)
        limiter = {"check": label, "pass_rate": r6(ev.check_rates[idx])}
        control = self.cfg.knob_for_check.get(info.name) if info.name is not None else None
        p = self.cfg.knobs.get(control) if control is not None else None
        if p is None:
            say = self.cfg.limiters.get(info.name) if info.name is not None else None
            if say is None and not self.cfg.knobs:
                hint = self._hint(info, need, head, label, limiter, evd)
                if hint is not None:
                    return hint
            text = say or "No adjustable setting addresses it; holding."
            return self._result("hold", "no_knob", f"{head} The limiter is {label}. {text}",
                                limiter=limiter, evidence=evd), None
        cur = self.values[p.control]
        proposed = self._propose(p, need)
        ctl = self._control_dict(p, cur, proposed, need)
        if proposed is None:
            if p.strategy == "rebaseline":
                msg = f"{head} Re-baselining {p.label} would not make reward {need} right now."
            else:
                edge = "easiest" if need == "easier" else "hardest"
                msg = (f"{head} {p.label} is already at its {edge} allowed value "
                       f"({fmt_value(cur, p.decimals, p.units)}).")
            return self._result(
                "hold", "at_limit", msg,
                level="policy", limiter=limiter, control=ctl, evidence=evd), None
        eligible = self._eligible_at(p, need)
        if eligible is not None and self.now < eligible:
            return self._result(
                "hold", "cooldown",
                f"{head} {p.label} can change again in {mmss((eligible - self.now) / self.sr)}.",
                level="policy", limiter=limiter, control=ctl, evidence=evd,
                eligible_at=eligible), None
        verb = "Raise" if proposed > cur else "Lower"
        reason = "too_strict" if need == "easier" else "too_easy"
        msg = (f"{head} The limiter is {label}. {verb} {p.label} "
               f"{fmt_value(cur, p.decimals, p.units)} -> {fmt_value(proposed, p.decimals, p.units)}.")
        return (self._result("adjust", reason, msg, level="policy", limiter=limiter, control=ctl,
                             evidence=evd, eligible_at=self.now),
                ("adjust", reason, p.control, need, proposed))

    def _hint(self, info: CheckInfo, need, head, label, limiter, evd):
        if info.knob is None or info.knob in self.cfg.inhibit_controls:
            return None
        control = info.knob
        d = self.dismissed.get((control, need))
        if d is not None and self.now < d:
            return self._result(
                "hold", "cooldown",
                f"{head} The limiter is {label}. Hint dismissed; holding for "
                f"{mmss((d - self.now) / self.sr)}.",
                level="hint", limiter=limiter, evidence=evd, eligible_at=d), None
        up = (need == "easier") == (info.higher_is == "easier")
        name = self.cfg.labels.get(control, control)
        ctl = {
            "name": control, "label": name, "units": self.cfg.units.get(control, ""),
            "round_to": None,
            "current": r6(self.values[control]) if control in self.values else None,
            "proposed": None, "direction": need, "strategy": None, "auto_allowed": False,
        }
        verb = "easing" if need == "easier" else "tightening"
        word = "higher" if up else "lower"
        msg = f"{head} The limiter is {label}. Consider {verb} {name} ({word} is {need})."
        reason = "too_strict" if need == "easier" else "too_easy"
        return (self._result("hint", reason, msg, level="hint", limiter=limiter, control=ctl,
                             evidence=evd),
                ("hint", control, need))

    # ---- lifecycle ----

    def _emit(self, kind: str, **fields: Any) -> dict:
        ev = {"advisor_version": ADVISOR_VERSION, "t_s": r6(self.now / self.sr),
              "kind": kind, **fields}
        self.events.append(ev)
        return ev

    def _publish(self, res: dict, key: tuple | None) -> None:
        if key is not None:
            if self.standing is not None and self.standing[0] == key:
                res["id"] = self.standing[1]
            else:
                if self.standing is not None:
                    self._emit("superseded", id=self.standing[1], reason=res["reason"])
                self.counter += 1
                new_id = f"adv-{self.counter:04d}"
                self.standing = (key, new_id)
                res["id"] = new_id
                ctl = res["control"]
                extra = ({"from": ctl["current"], "to": ctl["proposed"]}
                         if res["state"] == "adjust" else {})
                self._emit("suggested", id=new_id, reason=res["reason"], control=ctl["name"],
                           **extra)
        elif self.standing is not None:
            kind = "blocked" if res["reason"] in _BLOCKING else "superseded"
            self._emit(kind, id=self.standing[1], reason=res["reason"])
            self.standing = None
        self.current = res
