// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
//! Protocol autopilot advisor (SPEC §7.10). A line-for-line port of
//! `src/refrain/advisor_trace.py` (the trace section) and
//! `src/refrain/advisor.py` (the rest): same names, same order of
//! operations, same message strings. Parity-gated by
//! `tests/advisor_parity.rs`. Change both or neither.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use crate::ir::{Arg, Expr, Protocol, Reward};

// ============================================================ trace

const ELIGIBLE_KINDS: [&str; 4] = ["number", "percent", "voltage", "frequency"];

fn bare(canonical: &str) -> &str {
    canonical.split_once('/').map(|(_, n)| n).unwrap_or(canonical)
}

fn arg<'a>(args: &'a [Arg], name: &str, position: usize) -> Option<&'a Expr> {
    for (i, a) in args.iter().enumerate() {
        if a.name.as_deref() == Some(name) || (a.name.is_none() && i == position) {
            return Some(&a.value);
        }
    }
    None
}

pub fn reward_checks(event: Option<&Expr>) -> (Vec<&Expr>, &'static str) {
    let Some(Expr::Call { callee, args, .. }) = event else { return (vec![], "all") };
    if callee != "dwell" {
        return (vec![], "all");
    }
    let Some(cond) = arg(args, "condition", 0) else { return (vec![], "all") };
    if let Expr::Call { callee: c, args: cargs, .. } = cond {
        if (c == "all_of" || c == "any_of") && !cargs.is_empty() {
            if let Expr::Array { elements } = &cargs[0].value {
                let combine = if c == "all_of" { "all" } else { "any" };
                return (elements.iter().collect(), combine);
            }
        }
    }
    (vec![cond], "all")
}

fn walk(e: &Expr, p: &Protocol, seen: &mut BTreeSet<String>, out: &mut BTreeSet<String>) {
    match e {
        Expr::ControlRef { target, .. } => {
            out.insert(bare(target).to_string());
        }
        Expr::StreamRef { target, .. } => {
            if target.starts_with("derive/") && seen.insert(target.clone()) {
                if let Some(d) = p.derives.get(bare(target)) {
                    walk(&d.expression, p, seen, out);
                }
            }
        }
        Expr::ThresholdRef { target, .. } => {
            if seen.insert(target.clone()) {
                if let Some(t) = p.thresholds.get(bare(target)) {
                    walk(&t.threshold_call, p, seen, out);
                }
            }
        }
        Expr::Call { args, .. } => {
            for a in args {
                walk(&a.value, p, seen, out);
            }
        }
        Expr::Array { elements } | Expr::Tuple { elements } => {
            for x in elements {
                walk(x, p, seen, out);
            }
        }
        Expr::Binop { left, right, .. } => {
            walk(left, p, seen, out);
            walk(right, p, seen, out);
        }
        Expr::Conditional { cond, then, els } => {
            walk(cond, p, seen, out);
            walk(then, p, seen, out);
            walk(els, p, seen, out);
        }
        Expr::Block { fields, .. } => {
            for v in fields.values() {
                walk(v, p, seen, out);
            }
        }
        _ => {}
    }
}

pub fn controls_in(expr: &Expr, p: &Protocol) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut seen = BTreeSet::new();
    walk(expr, p, &mut seen, &mut out);
    out
}

pub fn trace_check(check: &Expr, p: &Protocol) -> Option<(String, &'static str)> {
    let Expr::Call { callee, args, .. } = check else { return None };
    if callee != "above" && callee != "below" {
        return None;
    }
    let thr = arg(args, "threshold", 1)?;
    let base = if callee == "above" { "harder" } else { "easier" };
    let (cand, side) = match thr {
        Expr::ControlRef { target, .. } => {
            let c = bare(target).to_string();
            (c.clone(), BTreeSet::from([c]))
        }
        Expr::ThresholdRef { target, .. } => {
            let th = p.thresholds.get(bare(target))?;
            let Expr::Call { callee: tc, args: targs, .. } = &th.threshold_call else { return None };
            let key = match tc.as_str() {
                "absolute" => "value",
                "percentile" => "target_pct",
                _ => return None,
            };
            let Some(Expr::ControlRef { target: ct, .. }) = arg(targs, key, 0) else { return None };
            (bare(ct).to_string(), controls_in(&th.threshold_call, p))
        }
        _ => return None,
    };
    let live: BTreeSet<String> = side
        .into_iter()
        .filter(|c| p.controls.get(c).is_some_and(|d| d.live_tunable))
        .collect();
    let decl = p.controls.get(&cand)?;
    if live != BTreeSet::from([cand.clone()]) || !ELIGIBLE_KINDS.contains(&decl.type_kind.as_str()) {
        return None;
    }
    Some((cand, base))
}

pub struct TraceCheck {
    pub name: Option<String>,
    pub knob: Option<String>,
    pub higher_is: Option<String>,
    pub controls: Vec<String>,
}

pub struct TraceBundle {
    pub combine: String,
    pub checks: Vec<TraceCheck>,
}

pub struct Trace {
    pub bundles: BTreeMap<String, TraceBundle>,
    pub check_controls: Vec<String>,
    pub inhibit_controls: Vec<String>,
}

pub fn trace_protocol(p: &Protocol) -> Trace {
    let mut rewards: Vec<(String, &Reward)> = Vec::new();
    if let Some(r) = p.reward.as_ref() {
        rewards.push((String::new(), r));
    }
    for (k, r) in p.reward_bundles.iter() {
        rewards.push((k.clone(), r));
    }
    let mut bundles = BTreeMap::new();
    let mut check_controls = BTreeSet::new();
    for (key, r) in rewards {
        let (checks, combine) = reward_checks(r.event.as_ref());
        if checks.is_empty() {
            continue;
        }
        let mut entries = Vec::new();
        for (i, c) in checks.iter().enumerate() {
            let traced = trace_check(c, p);
            let feeds: Vec<String> = controls_in(c, p).into_iter().collect();
            check_controls.extend(feeds.iter().cloned());
            entries.push(TraceCheck {
                name: r.check_names.get(i).cloned().flatten(),
                knob: traced.as_ref().map(|t| t.0.clone()),
                higher_is: traced.map(|t| t.1.to_string()),
                controls: feeds,
            });
        }
        bundles.insert(key, TraceBundle { combine: combine.to_string(), checks: entries });
    }
    let mut inhibit_controls = BTreeSet::new();
    for ih in p.inhibits.values() {
        inhibit_controls.extend(controls_in(&ih.metric, p));
        inhibit_controls.extend(controls_in(&ih.threshold, p));
    }
    Trace {
        bundles,
        check_controls: check_controls.into_iter().collect(),
        inhibit_controls: inhibit_controls.into_iter().collect(),
    }
}

pub fn trace_to_json(t: &Trace) -> Value {
    let mut bundles = Map::new();
    for (k, b) in &t.bundles {
        let checks: Vec<Value> = b
            .checks
            .iter()
            .map(|c| json!({"name": c.name, "knob": c.knob, "higher_is": c.higher_is,
                            "controls": c.controls}))
            .collect();
        bundles.insert(k.clone(), json!({"combine": b.combine, "checks": checks}));
    }
    json!({"bundles": bundles, "check_controls": t.check_controls,
           "inhibit_controls": t.inhibit_controls})
}

// ============================================================ helpers

pub const ADVISOR_VERSION: &str = "1";
const DEFAULT_TARGET: (f64, f64) = (0.50, 0.75);
const DEFAULT_WATCH_S: f64 = 120.0;
const DEFAULT_BETWEEN_S: f64 = 180.0;
const DEFAULT_SETTLE_S: f64 = 60.0;
const DEFAULT_GUARD_MAX: f64 = 0.15;
const BLOCKING: [&str; 3] = ["not_training_phase", "equipment_settling", "guard"];
const NOT_TRAINING: &str = "Advice paused: not a training phase.";

pub fn r6(x: f64) -> f64 {
    if x < 0.0 {
        return -r6(-x);
    }
    (x * 1e6 + 0.5).floor() / 1e6
}

pub fn pct(x: f64) -> i64 {
    (x * 100.0 + 0.5).floor() as i64
}

pub fn mmss(seconds: f64) -> String {
    let s = seconds.floor() as i64;
    format!("{}:{:02}", s / 60, s % 60)
}

pub fn fmt_value(v: f64, decimals: usize, units: &str) -> String {
    let text = format!("{v:.decimals$}");
    if units == "%" {
        format!("{text}%")
    } else if units.is_empty() {
        text
    } else {
        format!("{text} {units}")
    }
}

pub fn percentile_of(values: &[f64], p: f64) -> f64 {
    let mut s = values.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n == 1 {
        return s[0];
    }
    let rank = p / 100.0 * (n as f64 - 1.0);
    let lo = rank.floor() as usize;
    let hi = (lo + 1).min(n - 1);
    s[lo] + (s[hi] - s[lo]) * (rank - lo as f64)
}

pub fn snap(v: f64, round_to: Option<f64>) -> f64 {
    match round_to {
        Some(r) => (v / r + 0.5).floor() * r,
        None => v,
    }
}

fn default_samples(seconds: f64, sr: f64) -> u64 {
    ((seconds * sr + 0.5).floor() as u64).max(1)
}

fn units_of(kind: &str) -> &'static str {
    match kind {
        "percent" => "%",
        "voltage" => "uV",
        "frequency" => "Hz",
        _ => "",
    }
}

// ============================================================ facts

/// One chunk's observations (mirrors Python `ChunkFacts`).
pub struct ChunkFacts<'a> {
    pub n: usize,
    pub running: bool,
    pub phase_index: i64,
    pub phase_name: Option<&'a str>,
    pub output_muted: bool,
    pub clock_frozen: bool,
    pub bundle: Option<&'a str>,
    pub muted: &'a [bool],
    pub inhibits: Vec<(&'a str, &'a [bool])>,
    pub checks: Vec<&'a [bool]>,
    pub events: &'a [bool],
    pub derive_samples: Vec<(&'a str, &'a [f64])>,
}

/// Owned facts decoded from the compact scenario encoding (`facts_from_json`).
pub struct OwnedFacts {
    n: usize,
    running: bool,
    phase_index: i64,
    phase_name: Option<String>,
    output_muted: bool,
    clock_frozen: bool,
    bundle: Option<String>,
    muted: Vec<bool>,
    inhibits: Vec<(String, Vec<bool>)>,
    checks: Vec<Vec<bool>>,
    events: Vec<bool>,
    derive_samples: Vec<(String, Vec<f64>)>,
}

impl OwnedFacts {
    pub fn from_json(op: &Value) -> OwnedFacts {
        let n = op["n"].as_u64().unwrap() as usize;
        let bools = |v: &Value| -> Vec<bool> {
            match v {
                Value::Array(a) => a.iter().map(|x| x.as_bool().unwrap()).collect(),
                other => vec![other.as_bool().unwrap(); n],
            }
        };
        let floats = |v: &Value| -> Vec<f64> {
            match v {
                Value::Array(a) => a.iter().map(|x| x.as_f64().unwrap()).collect(),
                other => vec![other.as_f64().unwrap(); n],
            }
        };
        let mut events = vec![false; n];
        let k = op.get("events").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        for e in events.iter_mut().take(k) {
            *e = true;
        }
        let obj = |key: &str| op.get(key).and_then(|v| v.as_object()).cloned().unwrap_or_default();
        OwnedFacts {
            n,
            running: op["running"].as_bool().unwrap(),
            phase_index: op["phase_index"].as_i64().unwrap(),
            phase_name: op["phase_name"].as_str().map(String::from),
            output_muted: op["output_muted"].as_bool().unwrap(),
            clock_frozen: op["clock_frozen"].as_bool().unwrap(),
            bundle: op["bundle"].as_str().map(String::from),
            muted: bools(&op["muted"]),
            inhibits: obj("inhibits").iter().map(|(k, v)| (k.clone(), bools(v))).collect(),
            checks: op["checks"].as_array().map(|a| a.iter().map(&bools).collect()).unwrap_or_default(),
            events,
            derive_samples: obj("derive_samples").iter().map(|(k, v)| (k.clone(), floats(v))).collect(),
        }
    }

    pub fn view(&self) -> ChunkFacts<'_> {
        ChunkFacts {
            n: self.n,
            running: self.running,
            phase_index: self.phase_index,
            phase_name: self.phase_name.as_deref(),
            output_muted: self.output_muted,
            clock_frozen: self.clock_frozen,
            bundle: self.bundle.as_deref(),
            muted: &self.muted,
            inhibits: self.inhibits.iter().map(|(k, v)| (k.as_str(), v.as_slice())).collect(),
            checks: self.checks.iter().map(|v| v.as_slice()).collect(),
            events: &self.events,
            derive_samples: self.derive_samples.iter().map(|(k, v)| (k.as_str(), v.as_slice())).collect(),
        }
    }
}

// ============================================================ config

struct KnobPolicy {
    control: String,
    label: String,
    units: String,
    strategy: String,
    fixes: String,
    higher_is: String,
    apply: String,
    lo: f64,
    hi: f64,
    round_to: Option<f64>,
    decimals: usize,
    between: u64,
    step: Option<f64>,
    from_entity: Option<String>,
    window: Option<u64>,
    percentile: Option<f64>,
    citations: Vec<String>,
}

struct CheckInfo {
    name: Option<String>,
    knob: Option<String>,
    higher_is: Option<String>,
}

struct BundleInfo {
    combine: String,
    checks: Vec<CheckInfo>,
}

struct Config {
    target: (f64, f64),
    phases: Option<BTreeSet<String>>,
    watch: u64,
    between: u64,
    settle: u64,
    guards: BTreeMap<String, (f64, Option<String>)>,
    limiters: BTreeMap<String, String>,
    tighten_first: Vec<String>,
    knobs: BTreeMap<String, KnobPolicy>,
    knob_for_check: BTreeMap<String, String>,
    bundles: BTreeMap<String, BundleInfo>,
    relevant: BTreeSet<String>,
    inhibit_controls: BTreeSet<String>,
    labels: BTreeMap<String, String>,
    units: BTreeMap<String, String>,
    defaults: BTreeMap<String, f64>,
    provenance: Value,
}

fn build_config(p: &Protocol, sr: f64) -> Config {
    let ap = p.autopilot.as_ref();
    let tr = trace_protocol(p);
    let mut labels = BTreeMap::new();
    let mut units = BTreeMap::new();
    let mut defaults = BTreeMap::new();
    for (name, c) in &p.controls {
        let label = c.label.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| name.clone());
        labels.insert(name.clone(), label);
        units.insert(name.clone(), units_of(&c.type_kind).to_string());
        if let Some(Expr::Number { value, .. }) = &c.default {
            defaults.insert(name.clone(), *value);
        }
    }
    let phase_list = p.session.as_ref().map(|s| s.phases.as_slice()).unwrap_or(&[]);
    let phases: Option<BTreeSet<String>> = if let Some(ph) = ap.and_then(|a| a.phases.clone()) {
        Some(ph.into_iter().collect())
    } else if !phase_list.is_empty() {
        Some(phase_list.iter().filter(|x| !x.output_muted).map(|x| x.name.clone()).collect())
    } else {
        None
    };
    let samples = |v: Option<u64>, d: f64| v.unwrap_or_else(|| default_samples(d, sr));
    let between = samples(ap.and_then(|a| a.between_moves_samples), DEFAULT_BETWEEN_S);
    let mut guards = BTreeMap::new();
    for name in p.inhibits.keys() {
        let g = ap.and_then(|a| a.guards.get(name));
        guards.insert(
            name.clone(),
            match g {
                Some(g) => (g.max, g.say.clone()),
                None => (DEFAULT_GUARD_MAX, None),
            },
        );
    }
    let mut knobs = BTreeMap::new();
    for (name, c) in &p.controls {
        let Some(pol) = &c.autopilot else { continue };
        knobs.insert(
            name.clone(),
            KnobPolicy {
                control: name.clone(),
                label: pol.say.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| labels[name].clone()),
                units: units[name].clone(),
                strategy: pol.strategy.clone(),
                fixes: pol.fixes.clone(),
                higher_is: pol.higher_is.clone(),
                apply: pol.apply.clone(),
                lo: pol.limits[0],
                hi: pol.limits[1],
                round_to: pol.round_to,
                decimals: pol.decimals,
                between: pol.between_moves_samples.unwrap_or(between),
                step: pol.step,
                from_entity: pol.from.clone(),
                window: pol.window_samples,
                percentile: pol.percentile,
                citations: pol.citation.clone(),
            },
        );
    }
    let knob_for_check = knobs.iter().map(|(n, k)| (k.fixes.clone(), n.clone())).collect();
    let bundles = tr
        .bundles
        .iter()
        .map(|(k, b)| {
            (
                k.clone(),
                BundleInfo {
                    combine: b.combine.clone(),
                    checks: b
                        .checks
                        .iter()
                        .map(|c| CheckInfo { name: c.name.clone(), knob: c.knob.clone(), higher_is: c.higher_is.clone() })
                        .collect(),
                },
            )
        })
        .collect();
    let provenance = match ap {
        Some(a) => json!({"evidence": a.evidence, "citation": a.citation,
                          "rationale": a.rationale, "reviewed": a.reviewed}),
        None => Value::Null,
    };
    let target = match ap.and_then(|a| a.reward_target.clone()) {
        Some(v) if v.len() == 2 => (v[0], v[1]),
        _ => DEFAULT_TARGET,
    };
    let mut relevant: BTreeSet<String> = tr.check_controls.iter().cloned().collect();
    relevant.extend(tr.inhibit_controls.iter().cloned());
    Config {
        target,
        phases,
        watch: samples(ap.and_then(|a| a.watch_samples), DEFAULT_WATCH_S),
        between,
        settle: samples(ap.and_then(|a| a.equipment_settle_samples), DEFAULT_SETTLE_S),
        guards,
        limiters: ap.map(|a| a.limiters.iter().map(|(k, v)| (k.clone(), v.say.clone())).collect()).unwrap_or_default(),
        tighten_first: ap.map(|a| a.tighten_first.clone()).unwrap_or_default(),
        knobs,
        knob_for_check,
        bundles,
        relevant,
        inhibit_controls: tr.inhibit_controls.iter().cloned().collect(),
        labels,
        units,
        defaults,
        provenance,
    }
}

// ============================================================ advisor

struct Bucket {
    start: u64,
    end: u64,
    n_train: u64,
    n_clean: u64,
    n_cond: u64,
    checks: Vec<u64>,
    guards: BTreeMap<String, u64>,
    events: u64,
}

struct Evidence {
    start: u64,
    end: u64,
    n_clean: u64,
    reward_rate: Option<f64>,
    check_rates: Vec<f64>,
    guard_rates: BTreeMap<String, f64>,
    chimes_per_min: f64,
}

struct Reversal {
    control: String,
    from: f64,
    direction: String,
    checked: bool,
    fire: bool,
}

type Decision = (Value, Option<String>);

pub struct Advisor {
    sr: f64,
    cfg: Config,
    pub values: BTreeMap<String, f64>,
    now: u64,
    buckets: Vec<Bucket>,
    rebase: BTreeMap<String, Vec<f64>>,
    last_phase_index: Option<i64>,
    in_training: bool,
    bundle_key: String,
    settle_until: u64,
    last_move_at: Option<u64>,
    dismissed: BTreeMap<(String, String), u64>,
    reversal: Option<Reversal>,
    counter: u64,
    standing: Option<(String, String)>,
    events: Vec<Value>,
    current: Value,
}

fn set(v: &mut Value, key: &str, x: Value) {
    v.as_object_mut().unwrap().insert(key.to_string(), x);
}

impl Advisor {
    pub fn new(p: &Protocol, sample_rate_hz: f64) -> Advisor {
        let cfg = build_config(p, sample_rate_hz);
        let rebase = cfg
            .knobs
            .iter()
            .filter(|(_, k)| k.strategy == "rebaseline")
            .map(|(n, _)| (n.clone(), Vec::new()))
            .collect();
        let values = cfg.defaults.clone();
        let mut adv = Advisor {
            sr: sample_rate_hz,
            cfg,
            values,
            now: 0,
            buckets: Vec::new(),
            rebase,
            last_phase_index: None,
            in_training: false,
            bundle_key: String::new(),
            settle_until: 0,
            last_move_at: None,
            dismissed: BTreeMap::new(),
            reversal: None,
            counter: 0,
            standing: None,
            events: Vec::new(),
            current: Value::Null,
        };
        adv.current = adv.result("hold", "not_training_phase", NOT_TRAINING.to_string(), "observation",
                                 Value::Null, Value::Null, Value::Null, None);
        adv
    }

    // ---- host-facing ----

    pub fn rebaseline_sources(&self) -> Vec<String> {
        let set: BTreeSet<String> =
            self.rebase.keys().filter_map(|c| self.cfg.knobs[c].from_entity.clone()).collect();
        set.into_iter().collect()
    }

    pub fn advice(&self) -> Value {
        self.current.clone()
    }

    pub fn drain_events(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.events)
    }

    pub fn feed(&mut self, f: &ChunkFacts) {
        let start = self.now;
        self.now += f.n as u64;
        let phase_ok = match &self.cfg.phases {
            None => true,
            Some(set) => f.phase_name.is_some_and(|n| set.contains(n)),
        };
        if Some(f.phase_index) != self.last_phase_index {
            self.last_phase_index = Some(f.phase_index);
            if phase_ok {
                self.reset();
            }
        }
        self.in_training = f.running && phase_ok && !f.output_muted && !f.clock_frozen;
        self.bundle_key = f.bundle.unwrap_or("").to_string();
        if self.in_training && start >= self.settle_until {
            self.ingest(f, start);
        }
        self.evaluate();
    }

    pub fn apply(&mut self, advice_id: &str, by: &str) -> Result<(String, f64, Value), String> {
        if by != "clinician" && by != "autopilot" {
            return Err(format!("by must be 'clinician' or 'autopilot', got '{by}'"));
        }
        let res = self.current.clone();
        if res["id"].as_str() != Some(advice_id) || res["state"] != "adjust" {
            return Err(format!("advice '{advice_id}' is not the current suggestion"));
        }
        let ctl = &res["control"];
        let control = ctl["name"].as_str().unwrap().to_string();
        if by == "autopilot" && !ctl["auto_allowed"].as_bool().unwrap() {
            return Err(format!(
                "advice '{advice_id}' changes '{control}', which this protocol allows only as a suggestion"
            ));
        }
        let to = ctl["proposed"].as_f64().unwrap();
        let frm = self.values.get(&control).copied();
        self.values.insert(control.clone(), to);
        let ev = self.emit("applied", vec![
            ("id", json!(advice_id)), ("control", json!(control)),
            ("from", json!(frm.map(r6))), ("to", json!(to)), ("by", json!(by)),
        ]);
        if res["reason"] == "reversal" {
            self.reversal = None;
        } else {
            self.reversal = Some(Reversal {
                control: control.clone(),
                from: frm.unwrap_or(to),
                direction: ctl["direction"].as_str().unwrap().to_string(),
                checked: false,
                fire: false,
            });
        }
        self.last_move_at = Some(self.now);
        self.standing = None;
        self.reset();
        self.evaluate();
        Ok((control, to, ev))
    }

    pub fn dismiss(&mut self, advice_id: &str) -> Result<Value, String> {
        let res = self.current.clone();
        let state = res["state"].as_str().unwrap_or("");
        if res["id"].as_str() != Some(advice_id) || (state != "adjust" && state != "hint") {
            return Err(format!("advice '{advice_id}' is not the current suggestion"));
        }
        let name = res["control"]["name"].as_str().unwrap().to_string();
        let direction = res["control"]["direction"].as_str().unwrap().to_string();
        let between = self.cfg.knobs.get(&name).map_or(self.cfg.between, |k| k.between);
        self.dismissed.insert((name.clone(), direction), self.now + between);
        if res["reason"] == "reversal" {
            self.reversal = None;
        }
        let ev = self.emit("dismissed", vec![("id", json!(advice_id)), ("control", json!(name))]);
        self.standing = None;
        self.evaluate();
        Ok(ev)
    }

    /// Validates `source` before touching any state (controller ruling): an
    /// invalid source must leave `self.values` and everything else untouched.
    pub fn note_control(&mut self, control: &str, value: f64, source: &str) -> Result<(), String> {
        if source != "manual" && source != "seed" {
            return Err(format!("source must be 'manual' or 'seed', got '{source}'"));
        }
        let frm = self.values.get(control).copied();
        self.values.insert(control.to_string(), value);
        if !self.cfg.relevant.contains(control) {
            return Ok(());
        }
        if source == "manual" {
            self.emit("changed_manually", vec![
                ("control", json!(control)), ("from", json!(frm.map(r6))), ("to", json!(r6(value))),
            ]);
            if let Some((_, id)) = self.standing.take() {
                self.emit("superseded", vec![("id", json!(id)), ("reason", json!("changed_manually"))]);
            }
            if self.reversal.as_ref().is_some_and(|r| r.control == control) {
                self.reversal = None;
            }
            self.last_move_at = Some(self.now);
        }
        self.reset();
        self.evaluate();
        Ok(())
    }

    pub fn mark_equipment_change(&mut self) {
        self.emit("equipment_change", vec![]);
        self.settle_until = self.now + self.cfg.settle;
        self.reset();
        self.evaluate();
    }

    pub fn policy(&self) -> Value {
        let c = &self.cfg;
        let mut guards = Map::new();
        for (g, (m, s)) in &c.guards {
            guards.insert(g.clone(), json!({"max": r6(*m), "say": s}));
        }
        let mut controls = Map::new();
        for (name, p) in &c.knobs {
            controls.insert(name.clone(), json!({
                "label": p.label, "units": p.units, "strategy": p.strategy, "fixes": p.fixes,
                "higher_is": p.higher_is, "apply": p.apply, "limits": [r6(p.lo), r6(p.hi)],
                "round_to": p.round_to, "step": p.step, "percentile": p.percentile,
                "between_moves_s": r6(p.between as f64 / self.sr), "citation": p.citations,
            }));
        }
        json!({
            "advisor_version": ADVISOR_VERSION,
            "provenance": c.provenance,
            "reward_target": [r6(c.target.0), r6(c.target.1)],
            "phases": c.phases.as_ref().map(|s| s.iter().cloned().collect::<Vec<_>>()),
            "watch_s": r6(c.watch as f64 / self.sr),
            "between_moves_s": r6(c.between as f64 / self.sr),
            "equipment_settle_s": r6(c.settle as f64 / self.sr),
            "tighten_first": c.tighten_first,
            "guards": guards,
            "limiters": c.limiters,
            "controls": controls,
        })
    }

    // ---- evidence ----

    fn bundle(&self) -> Option<&BundleInfo> {
        self.cfg.bundles.get(&self.bundle_key)
    }

    fn reset(&mut self) {
        self.buckets.clear();
        for buf in self.rebase.values_mut() {
            buf.clear();
        }
    }

    fn ingest(&mut self, f: &ChunkFacts, start: u64) {
        let n = f.n;
        let clean: Vec<bool> = f.muted.iter().map(|m| !m).collect();
        let any = self.bundle().is_some_and(|b| b.combine == "any");
        let mut n_cond = 0u64;
        if !f.checks.is_empty() {
            for i in 0..n {
                let cond = if any {
                    f.checks.iter().any(|c| c[i])
                } else {
                    f.checks.iter().all(|c| c[i])
                };
                if cond && clean[i] {
                    n_cond += 1;
                }
            }
        }
        let count = |xs: &[bool]| xs.iter().zip(&clean).filter(|(x, c)| **x && **c).count() as u64;
        let bucket = Bucket {
            start,
            end: self.now,
            n_train: n as u64,
            n_clean: clean.iter().filter(|c| **c).count() as u64,
            n_cond,
            checks: f.checks.iter().map(|c| count(c)).collect(),
            guards: f.inhibits.iter().map(|(k, v)| (k.to_string(), v.iter().filter(|x| **x).count() as u64)).collect(),
            events: count(f.events),
        };
        self.buckets.push(bucket);
        let controls: Vec<String> = self.rebase.keys().cloned().collect();
        for control in controls {
            let (from, window) = {
                let k = &self.cfg.knobs[&control];
                (k.from_entity.clone(), k.window.unwrap_or(0) as usize)
            };
            let Some(src) = f.derive_samples.iter().find(|(k, _)| Some(*k) == from.as_deref()).map(|(_, v)| *v) else {
                continue;
            };
            let buf = self.rebase.get_mut(&control).unwrap();
            for (v, c) in src.iter().zip(&clean) {
                if *c && v.is_finite() {
                    buf.push(*v);
                }
            }
            if buf.len() > window {
                let drop = buf.len() - window;
                buf.drain(..drop);
            }
        }
        // Python: `horizon = self.now - 2 * self.cfg.watch` (signed, can go
        // negative) then `b.end > horizon`. Samples are u64 here, so the
        // subtraction is moved to the other side to avoid underflow instead
        // of clamping the horizon at 0 (which would silently drop buckets
        // Python still keeps, e.g. a zero-length chunk fed at time 0).
        self.buckets.retain(|b| b.end + 2 * self.cfg.watch > self.now);
    }

    fn evidence(&self) -> Option<Evidence> {
        // Same non-underflowing rewrite as `ingest`: `b.end <= self.now - 2 *
        // self.cfg.watch` becomes `b.end + 2 * self.cfg.watch <= self.now`.
        let mut sel: Vec<&Bucket> = Vec::new();
        let mut clean = 0u64;
        for b in self.buckets.iter().rev() {
            if b.end + 2 * self.cfg.watch <= self.now {
                break;
            }
            sel.push(b);
            clean += b.n_clean;
            if clean >= self.cfg.watch {
                break;
            }
        }
        if sel.is_empty() {
            return None;
        }
        sel.reverse();
        let n_train: u64 = sel.iter().map(|b| b.n_train).sum();
        let n_clean: u64 = sel.iter().map(|b| b.n_clean).sum();
        let k = self.bundle().map_or(0, |b| b.checks.len());
        let has = k > 0 && n_clean > 0 && sel.iter().all(|b| b.checks.len() == k);
        let nc = n_clean as f64;
        let mut guard_rates = BTreeMap::new();
        for g in self.cfg.guards.keys() {
            let a: u64 = sel.iter().map(|b| b.guards.get(g).copied().unwrap_or(0)).sum();
            guard_rates.insert(g.clone(), if n_train > 0 { a as f64 / n_train as f64 } else { 0.0 });
        }
        let events: u64 = sel.iter().map(|b| b.events).sum();
        Some(Evidence {
            start: sel[0].start,
            end: sel[sel.len() - 1].end,
            n_clean,
            reward_rate: if has { Some(sel.iter().map(|b| b.n_cond).sum::<u64>() as f64 / nc) } else { None },
            check_rates: if has {
                (0..k).map(|i| sel.iter().map(|b| b.checks[i]).sum::<u64>() as f64 / nc).collect()
            } else {
                vec![]
            },
            guard_rates,
            chimes_per_min: if n_clean > 0 { events as f64 / (nc / self.sr / 60.0) } else { 0.0 },
        })
    }

    fn check_label(&self, i: usize) -> String {
        let name = self.bundle().and_then(|b| b.checks.get(i)).and_then(|c| c.name.clone());
        name.filter(|s| !s.is_empty()).unwrap_or_else(|| format!("check {i}"))
    }

    fn evidence_dict(&self, ev: &Evidence) -> Value {
        let mut checks = Map::new();
        for (i, r) in ev.check_rates.iter().enumerate() {
            checks.insert(self.check_label(i), json!(r6(*r)));
        }
        let mut guards = Map::new();
        for (g, r) in &ev.guard_rates {
            guards.insert(g.clone(), json!(r6(*r)));
        }
        json!({
            "window_start_s": r6(ev.start as f64 / self.sr),
            "window_end_s": r6(ev.end as f64 / self.sr),
            "clean_s": r6(ev.n_clean as f64 / self.sr),
            "required_s": r6(self.cfg.watch as f64 / self.sr),
            "reward_rate": ev.reward_rate.map(r6),
            "target": [r6(self.cfg.target.0), r6(self.cfg.target.1)],
            "checks": checks,
            "guards": guards,
            "chimes_per_min": r6(ev.chimes_per_min),
        })
    }

    // ---- decision ----

    #[allow(clippy::too_many_arguments)]
    fn result(&self, state: &str, reason: &str, message: String, level: &str, limiter: Value,
              control: Value, evidence: Value, eligible_at: Option<u64>) -> Value {
        json!({
            "advisor_version": ADVISOR_VERSION, "id": Value::Null, "state": state, "level": level,
            "reason": reason, "message": message, "t_s": r6(self.now as f64 / self.sr),
            "limiter": limiter, "control": control, "evidence": evidence,
            "eligible_at_s": eligible_at.map(|e| r6(e as f64 / self.sr)),
        })
    }

    fn evaluate(&mut self) {
        let (res, key) = self.decide();
        self.publish(res, key);
    }

    fn decide(&mut self) -> Decision {
        let null = Value::Null;
        if !self.in_training {
            return (self.result("hold", "not_training_phase", NOT_TRAINING.to_string(), "observation",
                                null.clone(), null.clone(), null, None), None);
        }
        if self.now < self.settle_until {
            let left = ((self.settle_until - self.now) as f64 / self.sr).ceil() as i64;
            return (self.result("hold", "equipment_settling",
                                format!("Settling after equipment change ({left} s left)."),
                                "observation", null.clone(), null.clone(), null, None), None);
        }
        let ev = self.evidence();
        let evd = ev.as_ref().map_or(Value::Null, |e| self.evidence_dict(e));
        if let Some(e) = &ev {
            let mut worst: Option<&String> = None;
            for (g, (mx, _)) in &self.cfg.guards {
                let r = e.guard_rates[g];
                if r > *mx && worst.is_none_or(|w| r > e.guard_rates[w]) {
                    worst = Some(g);
                }
            }
            if let Some(w) = worst {
                let msg = match &self.cfg.guards[w].1 {
                    Some(say) => format!("{say} ({w} guard active {}% of training time).", pct(e.guard_rates[w])),
                    None => format!("{w} guard active {}% of training time.", pct(e.guard_rates[w])),
                };
                return (self.result("hold", "guard", msg, "observation", null.clone(), null, evd, None), None);
            }
        }
        let clean = ev.as_ref().map_or(0, |e| e.n_clean);
        if clean < self.cfg.watch {
            let msg = format!("Collecting clean signal ({} of {}).",
                              mmss(clean as f64 / self.sr), mmss(self.cfg.watch as f64 / self.sr));
            return (self.result("collecting", "collecting", msg, "observation", null.clone(), null, evd, None), None);
        }
        let ev = ev.unwrap();
        let Some(rate) = ev.reward_rate else {
            return (self.result("hold", "observing",
                                "Observing: this protocol has no reward condition to judge.".to_string(),
                                "observation", null.clone(), null, evd, None), None);
        };
        let (lo, hi) = self.cfg.target;
        let head = format!("Reward met {}% of clean time (target {}-{}%).", pct(rate), pct(lo), pct(hi));
        if let Some(rev) = self.reversal_step(rate, &evd) {
            return rev;
        }
        if lo <= rate && rate <= hi {
            return (self.result("hold", "on_track", format!("On track. {head}"), "observation",
                                null.clone(), null, evd, None), None);
        }
        let need = if rate < lo { "easier" } else { "harder" };
        self.outside_band(&ev, evd, need, &head)
    }

    fn reversal_step(&mut self, rate: f64, evd: &Value) -> Option<Decision> {
        let (lo, hi) = self.cfg.target;
        let r = self.reversal.as_mut()?;
        if !r.checked {
            r.checked = true;
            let worse = (r.direction == "harder" && rate < lo) || (r.direction == "easier" && rate > hi);
            if !worse {
                self.reversal = None;
                return None;
            }
            r.fire = true;
        }
        if !r.fire {
            return None;
        }
        let (control, from, direction) = (r.control.clone(), r.from, r.direction.clone());
        let p = &self.cfg.knobs[&control];
        let cur = self.values[&control];
        let back = if direction == "harder" { "easier" } else { "harder" };
        let prev = r6(from);
        let msg = format!(
            "The last change made reward worse ({}% of clean time). Return {} {} -> {}.",
            pct(rate), p.label, fmt_value(cur, p.decimals, &p.units), fmt_value(prev, p.decimals, &p.units));
        let ctl = self.control_dict(p, cur, Some(prev), back);
        let res = self.result("adjust", "reversal", msg, "policy", Value::Null, ctl, evd.clone(), Some(self.now));
        // Normalise -0.0 to 0.0 before formatting: Python compares the float
        // inside a tuple key with `==` (where 0.0 == -0.0), so the standing
        // suggestion must not change identity just because r6() produced a
        // negative zero.
        let key_val = prev + 0.0;
        Some((res, Some(format!("adjust|reversal|{control}|{back}|{key_val:?}"))))
    }

    fn pick_check(&self, ev: &Evidence, need: &str) -> usize {
        let rates = &ev.check_rates;
        if need == "easier" {
            let mut best = 0;
            for i in 1..rates.len() {
                if rates[i] < rates[best] {
                    best = i;
                }
            }
            return best;
        }
        let names: Vec<Option<String>> = self.bundle().unwrap().checks.iter().map(|c| c.name.clone()).collect();
        for name in &self.cfg.tighten_first {
            if let Some(idx) = names.iter().position(|n| n.as_deref() == Some(name.as_str())) {
                if let Some(control) = self.cfg.knob_for_check.get(name) {
                    if self.propose(&self.cfg.knobs[control], "harder").is_some() {
                        return idx;
                    }
                }
            }
        }
        let mut best = 0;
        for i in 1..rates.len() {
            if rates[i] > rates[best] {
                best = i;
            }
        }
        best
    }

    fn propose(&self, p: &KnobPolicy, need: &str) -> Option<f64> {
        let cur = self.values[&p.control];
        let up = (need == "easier") == (p.higher_is == "easier");
        let v = match p.strategy.as_str() {
            "fixed_step" => {
                let s = p.step.unwrap();
                if up { cur + s } else { cur - s }
            }
            "proportional_step" => {
                let s = p.step.unwrap();
                if up { cur * (1.0 + s) } else { cur * (1.0 - s) }
            }
            _ => {
                let buf = self.rebase.get(&p.control)?;
                if (buf.len() as u64) < p.window.unwrap_or(0) {
                    return None;
                }
                percentile_of(buf, p.percentile.unwrap())
            }
        };
        let v = r6(snap(v, p.round_to).max(p.lo).min(p.hi));
        if (v - cur).abs() < 1e-9 || (v > cur) != up {
            return None;
        }
        Some(v)
    }

    fn control_dict(&self, p: &KnobPolicy, cur: f64, proposed: Option<f64>, need: &str) -> Value {
        json!({
            "name": p.control, "label": p.label, "units": p.units, "round_to": p.round_to,
            "current": r6(cur), "proposed": proposed, "direction": need, "strategy": p.strategy,
            "auto_allowed": p.apply == "auto" && p.strategy != "rebaseline",
        })
    }

    fn eligible_at(&self, p: &KnobPolicy, need: &str) -> Option<u64> {
        let mut t = self.last_move_at.map(|m| m + p.between);
        if let Some(d) = self.dismissed.get(&(p.control.clone(), need.to_string())) {
            if t.is_none_or(|t0| *d > t0) {
                t = Some(*d);
            }
        }
        t
    }

    fn outside_band(&self, ev: &Evidence, evd: Value, need: &str, head: &str) -> Decision {
        let idx = self.pick_check(ev, need);
        let info = &self.bundle().unwrap().checks[idx];
        let label = self.check_label(idx);
        let limiter = json!({"check": label, "pass_rate": r6(ev.check_rates[idx])});
        let control = info.name.as_ref().and_then(|n| self.cfg.knob_for_check.get(n));
        let p = control.and_then(|c| self.cfg.knobs.get(c));
        let Some(p) = p else {
            let say = info.name.as_ref().and_then(|n| self.cfg.limiters.get(n));
            if say.is_none() && self.cfg.knobs.is_empty() {
                if let Some(h) = self.hint(info, need, head, &label, &limiter, &evd) {
                    return h;
                }
            }
            let text = say
                .filter(|s| !s.is_empty())
                .cloned()
                .unwrap_or_else(|| "No adjustable setting addresses it; holding.".to_string());
            return (self.result("hold", "no_knob", format!("{head} The limiter is {label}. {text}"),
                                "observation", limiter, Value::Null, evd, None), None);
        };
        let cur = self.values[&p.control];
        let proposed = self.propose(p, need);
        let ctl = self.control_dict(p, cur, proposed, need);
        let Some(proposed) = proposed else {
            let msg = if p.strategy == "rebaseline" {
                format!("{head} Re-baselining {} would not make reward {need} right now.", p.label)
            } else {
                let edge = if need == "easier" { "easiest" } else { "hardest" };
                format!("{head} {} is already at its {edge} allowed value ({}).",
                        p.label, fmt_value(cur, p.decimals, &p.units))
            };
            return (self.result("hold", "at_limit", msg, "policy", limiter, ctl, evd, None), None);
        };
        if let Some(eligible) = self.eligible_at(p, need) {
            if self.now < eligible {
                let msg = format!("{head} {} can change again in {}.", p.label,
                                  mmss((eligible - self.now) as f64 / self.sr));
                return (self.result("hold", "cooldown", msg, "policy", limiter, ctl, evd, Some(eligible)), None);
            }
        }
        let verb = if proposed > cur { "Raise" } else { "Lower" };
        let reason = if need == "easier" { "too_strict" } else { "too_easy" };
        let msg = format!("{head} The limiter is {label}. {verb} {} {} -> {}.", p.label,
                          fmt_value(cur, p.decimals, &p.units), fmt_value(proposed, p.decimals, &p.units));
        // See the same normalisation in `reversal_step`: avoid -0.0 changing
        // the standing suggestion's identity.
        let key_val = proposed + 0.0;
        let key = format!("adjust|{reason}|{}|{need}|{key_val:?}", p.control);
        (self.result("adjust", reason, msg, "policy", limiter, ctl, evd, Some(self.now)), Some(key))
    }

    fn hint(&self, info: &CheckInfo, need: &str, head: &str, label: &str, limiter: &Value,
            evd: &Value) -> Option<Decision> {
        let control = info.knob.as_ref()?;
        if self.cfg.inhibit_controls.contains(control) {
            return None;
        }
        if let Some(d) = self.dismissed.get(&(control.clone(), need.to_string())) {
            if self.now < *d {
                let msg = format!("{head} The limiter is {label}. Hint dismissed; holding for {}.",
                                  mmss((*d - self.now) as f64 / self.sr));
                return Some((self.result("hold", "cooldown", msg, "hint", limiter.clone(), Value::Null,
                                         evd.clone(), Some(*d)), None));
            }
        }
        let up = (need == "easier") == (info.higher_is.as_deref() == Some("easier"));
        let name = self.cfg.labels.get(control).cloned().unwrap_or_else(|| control.clone());
        let ctl = json!({
            "name": control, "label": name, "units": self.cfg.units.get(control).cloned().unwrap_or_default(),
            "round_to": Value::Null, "current": self.values.get(control).map(|v| r6(*v)),
            "proposed": Value::Null, "direction": need, "strategy": Value::Null, "auto_allowed": false,
        });
        let verb = if need == "easier" { "easing" } else { "tightening" };
        let word = if up { "higher" } else { "lower" };
        let msg = format!("{head} The limiter is {label}. Consider {verb} {name} ({word} is {need}).");
        let reason = if need == "easier" { "too_strict" } else { "too_easy" };
        Some((self.result("hint", reason, msg, "hint", limiter.clone(), ctl, evd.clone(), None),
              Some(format!("hint|{control}|{need}"))))
    }

    // ---- lifecycle ----

    fn emit(&mut self, kind: &str, fields: Vec<(&str, Value)>) -> Value {
        let mut ev = Map::new();
        ev.insert("advisor_version".to_string(), json!(ADVISOR_VERSION));
        ev.insert("t_s".to_string(), json!(r6(self.now as f64 / self.sr)));
        ev.insert("kind".to_string(), json!(kind));
        for (k, v) in fields {
            ev.insert(k.to_string(), v);
        }
        let ev = Value::Object(ev);
        self.events.push(ev.clone());
        ev
    }

    fn publish(&mut self, mut res: Value, key: Option<String>) {
        if let Some(key) = key {
            if let Some((k, id)) = &self.standing {
                if *k == key {
                    let id = id.clone();
                    set(&mut res, "id", json!(id));
                    self.current = res;
                    return;
                }
            }
            if let Some((_, old)) = self.standing.take() {
                let reason = res["reason"].clone();
                self.emit("superseded", vec![("id", json!(old)), ("reason", reason)]);
            }
            self.counter += 1;
            let new_id = format!("adv-{:04}", self.counter);
            self.standing = Some((key, new_id.clone()));
            set(&mut res, "id", json!(new_id));
            let mut fields = vec![
                ("id", json!(new_id)),
                ("reason", res["reason"].clone()),
                ("control", res["control"]["name"].clone()),
            ];
            if res["state"] == "adjust" {
                fields.push(("from", res["control"]["current"].clone()));
                fields.push(("to", res["control"]["proposed"].clone()));
            }
            self.emit("suggested", fields);
        } else if let Some((_, old)) = self.standing.take() {
            let reason = res["reason"].as_str().unwrap_or("").to_string();
            let kind = if BLOCKING.contains(&reason.as_str()) { "blocked" } else { "superseded" };
            self.emit(kind, vec![("id", json!(old)), ("reason", json!(reason))]);
        }
        self.current = res;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers_match_python() {
        assert_eq!(r6(0.1234566), 0.123457);
        assert_eq!(r6(-0.1234566), -0.123457);
        assert_eq!(pct(0.125), 13);
        assert_eq!(mmss(65.9), "1:05");
        assert_eq!(fmt_value(0.55, 2, ""), "0.55");
        assert_eq!(fmt_value(15.0, 0, "%"), "15%");
        assert_eq!(fmt_value(7.2, 1, "uV"), "7.2 uV");
        assert!((percentile_of(&[5.0, 1.0, 3.0, 2.0, 4.0], 60.0) - 3.4).abs() < 1e-12);
        assert_eq!(snap(0.55, Some(0.01)), 0.55);
    }

    fn proto() -> Protocol {
        serde_json::from_value(serde_json::json!({
            "refrain_ir_version": "0.4", "sample_rate_hz": 10.0, "channels": ["Cz"],
            "inputs": {}, "derives": {}, "thresholds": {}, "inhibits": {},
            "reward": {"continuous": null, "check_names": ["a"],
                "event": {"node": "call", "callee": "dwell", "args": [
                    {"name": "condition", "value": {"node": "call", "callee": "all_of", "args": [
                        {"name": null, "value": {"node": "array", "elements": [
                            {"node": "call", "callee": "above", "args": [
                                {"name": null, "value": {"node": "stream_ref", "target": "input/raw"}},
                                {"name": null, "value": {"node": "control_ref", "target": "control/k", "default": 1.0}}]}]}}]}}]}},
            "controls": {"k": {"canonical_name": "control/k", "type_kind": "number",
                "live_tunable": true, "default": {"node": "number", "value": 1.0},
                "autopilot": {"strategy": "fixed_step", "fixes": "a", "higher_is": "harder",
                    "apply": "auto", "limits": [0.5, 2.0], "round_to": 0.1, "decimals": 1,
                    "step": 0.1, "citation": []}}},
            "autopilot": {"evidence": "experimental", "citation": ["t"], "rationale": "r",
                "reward_target": [0.4, 0.6], "watch_samples": 20},
            "output": {}, "topological_order": []
        })).unwrap()
    }

    #[test]
    fn trace_finds_knob_and_direction() {
        let t = trace_protocol(&proto());
        let c = &t.bundles[""].checks[0];
        assert_eq!((c.knob.as_deref(), c.higher_is.as_deref()), (Some("k"), Some("harder")));
    }

    #[test]
    fn too_strict_then_apply() {
        // watch = 20 samples; 2 of 20 clean samples meet the check (10%),
        // below the (40%, 60%) target -> ease k (higher is harder): 1.0 -> 0.9.
        let mut adv = Advisor::new(&proto(), 10.0);
        let mut first = vec![false; 10];
        first[0] = true;
        first[1] = true;
        let no = vec![false; 10];
        for check in [&first, &no] {
            adv.feed(&ChunkFacts {
                n: 10, running: true, phase_index: -1, phase_name: None,
                output_muted: false, clock_frozen: false, bundle: None,
                muted: &no, inhibits: vec![], checks: vec![check.as_slice()], events: &no,
                derive_samples: vec![],
            });
        }
        let a = adv.advice();
        assert_eq!(a["reason"], "too_strict", "{a}");
        assert_eq!(a["control"]["proposed"], 0.9);
        assert_eq!(a["id"], "adv-0001");
        let (control, value, ev) = adv.apply("adv-0001", "autopilot").unwrap();
        assert_eq!((control.as_str(), value), ("k", 0.9));
        assert_eq!(ev["kind"], "applied");
        assert!(adv.apply("adv-0001", "autopilot").is_err());
    }

    #[test]
    fn note_control_validates_source_before_mutating_state() {
        let mut adv = Advisor::new(&proto(), 10.0);
        // An invalid source is rejected, and rejected *before* any state changes.
        assert_eq!(
            adv.note_control("k", 1.5, "bogus"),
            Err("source must be 'manual' or 'seed', got 'bogus'".to_string())
        );
        assert_eq!(adv.values.get("k"), Some(&1.0));
        // A valid source is accepted and does update the value.
        assert!(adv.note_control("k", 1.5, "manual").is_ok());
        assert_eq!(adv.values.get("k"), Some(&1.5));
    }

    #[test]
    fn zero_length_chunk_at_time_zero_still_has_evidence() {
        // Regression for a horizon-underflow bug: Python computes
        // `horizon = self.now - 2 * self.cfg.watch` as a signed int (goes
        // negative here), so `b.end (0) > horizon (-40)` keeps the bucket and
        // `_evidence()` returns a non-null evidence dict. A naive Rust port
        // using `self.now.saturating_sub(...)` clamps the horizon at 0, so
        // `b.end (0) > horizon (0)` is false and the bucket (and therefore
        // the evidence) is dropped. Verified against a real run of the
        // committed src/refrain/advisor.py with the equivalent ChunkFacts:
        // `advice()["evidence"]` is not None there either.
        let mut adv = Advisor::new(&proto(), 10.0);
        let empty: Vec<bool> = vec![];
        adv.feed(&ChunkFacts {
            n: 0, running: true, phase_index: -1, phase_name: None,
            output_muted: false, clock_frozen: false, bundle: None,
            muted: &empty, inhibits: vec![], checks: vec![empty.as_slice()], events: &empty,
            derive_samples: vec![],
        });
        let a = adv.advice();
        assert!(!a["evidence"].is_null(), "{a}");
    }
}
