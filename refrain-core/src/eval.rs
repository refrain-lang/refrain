//! Chunk-driven evaluator: compiles the baked IR-JSON into a stateful tree
//! and runs it per chunk, reproducing the Python evaluator's `last_streams()`
//! output. Filter design lives in Python; every rate-dependent coefficient is
//! baked into the IR, so the runtime only needs the channel layout.
//!
//! PoC scope: the micro_03/04/05 corpus — referential montage, the envelope
//! DSP pipeline, windowed-percentile / absolute thresholds, above/below/
//! all_of conditions, the dwell event machine, the sigmoid mapping, and the
//! `/` binop — plus multi-input protocols and the two-input `coherence`
//! primitive (streaming Welch MSC). (Control-ref args and the conditional
//! output remain Phase-B.)

use std::collections::{BTreeMap, HashMap};

use crate::dsp::{
    control_cell, smooth_alpha_from_tau_ms, AutoRange, Autocorr, Bandpower, Biquad, Coherence,
    ControlCell,
    Differentiate, Dwell, HilbertFir, Magnitude, Percentile, Signal, Smooth, Stage, TrackerState,
};
use crate::ir::{Coeffs, Expr, Protocol};

/// One unit of evaluator output, mirroring `eval_.Event`. `value` is the
/// per-chunk mean for value channels and `None` for discrete events.
#[derive(Clone, Debug)]
pub struct Event {
    pub timestamp_s: f64,
    pub channel: String,
    pub kind: String,
    pub value: Option<f64>,
}

/// Reshape a flat, row-major `(n_samples * n_channels)` buffer into the
/// `Vec<Vec<f64>>` of per-sample rows that `step_chunk` / `step_chunk_events`
/// consume. Bindings that can only carry a 1-D buffer (e.g. uniffi, which has
/// no 2-D type) call this so the reshape lives in exactly one place rather than
/// being duplicated per FFI layer. The pyo3 path receives an already-2-D numpy
/// array and reshapes via `outer_iter`, so it does not need this entry point.
pub fn rows_from_flat(flat: &[f64], n_channels: usize) -> Vec<Vec<f64>> {
    assert!(n_channels > 0, "rows_from_flat: n_channels must be > 0");
    assert!(
        flat.len() % n_channels == 0,
        "rows_from_flat: buffer length {} is not a multiple of n_channels {}",
        flat.len(),
        n_channels
    );
    flat.chunks(n_channels).map(<[f64]>::to_vec).collect()
}

/// Lifecycle state, mirroring `Evaluator.state` in `eval_.py`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Ready,
    Warmup,
    Run,
    Stopped,
}

/// A per-chunk stream value: float-valued or boolean.
#[derive(Clone)]
enum Val {
    F(Vec<f64>),
    B(Vec<bool>),
}

impl Val {
    fn as_f(&self) -> &[f64] {
        match self {
            Val::F(v) => v,
            Val::B(_) => panic!("expected a float stream, got boolean"),
        }
    }
    fn as_b(&self) -> &[bool] {
        match self {
            Val::B(v) => v,
            Val::F(_) => panic!("expected a boolean stream, got float"),
        }
    }
    fn into_f(self) -> Vec<f64> {
        match self {
            Val::F(v) => v,
            Val::B(v) => v.iter().map(|&b| if b { 1.0 } else { 0.0 }).collect(),
        }
    }
}

// --- Live control retuning -------------------------------------------------

/// One binding from a clinician control to a controllable stage parameter,
/// mirroring an entry in the Python evaluator's `_control_deps`. Each variant
/// holds a clone of the same shared `ControlCell` the stage reads, so applying
/// a value here is observed by the stage on its next chunk — WITHOUT resetting
/// any buffer/filter state. `apply` mirrors the corresponding Python
/// `update_control`.
enum Control {
    /// `PercentileImpl.update_control`: `target_pct = clamp(value, 0, 100)`.
    Percentile { target_pct: ControlCell },
    /// `SmoothImpl.update_control`: if `value > 0`, recompute `alpha` from the
    /// new tau (ms) at the stage's sample rate. The stage's `prev` state is
    /// preserved (warm-restart).
    Smooth { alpha: ControlCell, sample_rate_hz: f64 },
    /// `SigmoidImpl.update_control`: `midpoint = value`.
    Sigmoid { midpoint: ControlCell },
    /// A reward component's weight (v0.2 composite). `set_control` writes the
    /// new weight into the shared cell; `eval_chunk` reads it fresh each chunk,
    /// mirroring the Python evaluator reading `control_chunks[target]`.
    Weight { value: ControlCell },
    /// `AbsoluteThresholdImpl.update_control`: write the new constant into the
    /// shared cell that `CNode::ConstCell` / `InhibitThreshold::ConstCell`
    /// reads each chunk. Mirrors the Python `update_control` that sets
    /// `self.value`.
    Const { value: ControlCell },
}

impl Control {
    /// Forward a control change to the bound stage parameter, matching the
    /// Python impl's `update_control`. State other than the targeted parameter
    /// is untouched.
    fn apply(&self, value: f64) {
        match self {
            Control::Percentile { target_pct } => {
                *target_pct.lock().unwrap() = value.clamp(0.0, 100.0);
            }
            Control::Smooth { alpha, sample_rate_hz } => {
                if value > 0.0 {
                    *alpha.lock().unwrap() = smooth_alpha_from_tau_ms(value, *sample_rate_hz);
                }
            }
            Control::Sigmoid { midpoint } => {
                *midpoint.lock().unwrap() = value;
            }
            Control::Weight { value: cell } => {
                *cell.lock().unwrap() = value;
            }
            Control::Const { value: cell } => {
                *cell.lock().unwrap() = value;
            }
        }
    }
}

/// Build-time context threaded through the compilation helpers: the runtime
/// sample rate plus the control registry being assembled. As each controllable
/// stage is built, its binding is recorded under the control's canonical target
/// (`control/<name>`). Mirrors the Python evaluator populating `_control_deps`
/// during `_build_pipeline`.
struct BuildCtx {
    sample_rate_hz: f64,
    controls: HashMap<String, Vec<Control>>,
}

impl BuildCtx {
    fn new(sample_rate_hz: f64) -> Self {
        BuildCtx { sample_rate_hz, controls: HashMap::new() }
    }

    fn register(&mut self, target: &str, ctrl: Control) {
        self.controls.entry(target.to_string()).or_default().push(ctrl);
    }
}

/// Read a numeric argument by `name`, accepting either a literal `number` node
/// or a `control_ref` (using its baked `default`). When the arg is a
/// `control_ref`, the control's canonical `target` is also returned so the
/// caller can register a binding. Returns `(value, Option<target>)`.
fn num_named_controllable(args: &[crate::ir::Arg], name: &str) -> Option<(f64, Option<String>)> {
    args.iter().find_map(|a| match (&a.name, &a.value) {
        (Some(nm), Expr::Number { value }) if nm == name => Some((*value, None)),
        (Some(nm), Expr::ControlRef { target, default }) if nm == name => {
            Some((*default, Some(target.clone())))
        }
        _ => None,
    })
}

// --- Montage --------------------------------------------------------------

enum Montage {
    Referential(Referential),
    Bipolar { plus_idx: usize, minus_idx: usize }, // BipolarImpl: plus - minus
    Passthrough { idx: usize },                    // PassthroughImpl: identity, single channel
}

struct Referential {
    active_idx: usize,
    device: bool,                    // "device" => active as-recorded, no subtraction
    ref_indices: Option<Vec<usize>>, // None => common-average over all channels
}

impl Montage {
    /// `bipolar(plus, minus)` — `samples[:, plus] - samples[:, minus]`
    /// (mirrors `BipolarImpl`).
    fn bipolar(plus: &str, minus: &str, channels: &[String]) -> Self {
        let plus_idx = channels
            .iter()
            .position(|c| c == plus)
            .unwrap_or_else(|| panic!("bipolar: plus {plus:?} not in source"));
        let minus_idx = channels
            .iter()
            .position(|c| c == minus)
            .unwrap_or_else(|| panic!("bipolar: minus {minus:?} not in source"));
        Montage::Bipolar { plus_idx, minus_idx }
    }

    fn referential(active: &str, reference: &str, channels: &[String]) -> Self {
        Montage::Referential(Referential::new(active, reference, channels))
    }

    /// `passthrough()` — identity on a single-channel source (mirrors
    /// `PassthroughImpl`). Requires exactly one source channel.
    fn passthrough(channels: &[String]) -> Self {
        assert!(
            channels.len() == 1,
            "passthrough() requires a single-channel source (got {} channels: {:?})",
            channels.len(),
            channels
        );
        Montage::Passthrough { idx: 0 }
    }

    fn run(&self, chunk: &[Vec<f64>]) -> Vec<f64> {
        match self {
            Montage::Referential(r) => r.run(chunk),
            Montage::Bipolar { plus_idx, minus_idx } => {
                chunk.iter().map(|row| row[*plus_idx] - row[*minus_idx]).collect()
            }
            Montage::Passthrough { idx } => chunk.iter().map(|row| row[*idx]).collect(),
        }
    }
}

impl Referential {
    fn new(active: &str, reference: &str, channels: &[String]) -> Self {
        let active_idx = channels
            .iter()
            .position(|c| c == active)
            .unwrap_or_else(|| panic!("referential: active {active:?} not in source"));
        if reference == "device" {
            // Hardware reference baked into the channel values: no software
            // re-referencing (matches ReferentialImpl's `use_hardware_reference`).
            return Referential { active_idx, device: true, ref_indices: None };
        }
        let ref_indices = match reference {
            "linked_ears" => {
                let mut cand = Vec::new();
                for name in ["A1", "A2", "M1", "M2", "T9", "T10"] {
                    if let Some(i) = channels.iter().position(|c| c == name) {
                        cand.push(i);
                    }
                }
                if cand.len() < 2 {
                    None
                } else {
                    Some(cand)
                }
            }
            "common_average" => None,
            other => {
                let i = channels
                    .iter()
                    .position(|c| c == other)
                    .unwrap_or_else(|| panic!("referential: reference {other:?} not in source"));
                Some(vec![i])
            }
        };
        Referential { active_idx, device: false, ref_indices }
    }

    fn run(&self, chunk: &[Vec<f64>]) -> Vec<f64> {
        chunk
            .iter()
            .map(|row| {
                let active = row[self.active_idx];
                if self.device {
                    return active;
                }
                let refv = match &self.ref_indices {
                    None => row.iter().sum::<f64>() / row.len() as f64,
                    Some(idx) if idx.len() == 1 => row[idx[0]],
                    Some(idx) => idx.iter().map(|&i| row[i]).sum::<f64>() / idx.len() as f64,
                };
                active - refv
            })
            .collect()
    }
}

// --- Compiled expression node ---------------------------------------------

enum CNode {
    Const(f64),
    /// `absolute(value: <control_ref>)` — constant fed by a shared cell so
    /// `set_control` retunes the threshold in-place. The literal-`Number` path
    /// stays on `Const(f64)`.
    ConstCell(ControlCell),
    BoolConst(bool),
    Stream(String),       // bare-name lookup in env (inputs/derives/thresholds)
    Reward(String),       // "reward.<field_path>" lookup in env
    Pipeline(Vec<Box<dyn Stage>>, Box<CNode>), // contiguous DSP chain (handles complex internally)
    Pct(Percentile, Box<CNode>),
    Coherence { coh: Coherence, a: Box<CNode>, b: Box<CNode> }, // 2-input, per-chunk scalar
    Above(Box<CNode>, Box<CNode>),
    Below(Box<CNode>, Box<CNode>),
    Inside { input: Box<CNode>, low: f64, high: f64 }, // low <= x <= high
    AllOf(Vec<CNode>),
    AnyOf(Vec<CNode>),
    Sigmoid { midpoint: ControlCell, steepness: f64, input: Box<CNode> },
    Linear { midpoint: f64, slope: f64, input: Box<CNode> },
    Binop(String, Box<CNode>, Box<CNode>),
    Cond(Box<CNode>, Box<CNode>, Box<CNode>),
}

impl CNode {
    fn eval(&mut self, env: &HashMap<String, Val>, n: usize) -> Val {
        match self {
            CNode::Const(c) => Val::F(vec![*c; n]),
            CNode::ConstCell(cell) => {
                let v = *cell.lock().unwrap();
                Val::F(vec![v; n])
            }
            CNode::BoolConst(b) => Val::B(vec![*b; n]),
            CNode::Stream(name) => env
                .get(name)
                .unwrap_or_else(|| panic!("stream {name:?} not bound"))
                .clone(),
            CNode::Reward(field) => match env.get(&format!("reward.{field}")) {
                Some(v) => v.clone(),
                // Mirror Python's IRRewardField defaults: an unbound reward
                // field reads as typed zeros. This happens for a staged
                // protocol whose `output` references `reward.continuous`/
                // `reward.event` during a phase with no active block (warmup /
                // rest), where no bundle has bound it.
                None if field == "event" || field == "event.holds" => Val::B(vec![false; n]),
                None => Val::F(vec![0.0; n]),
            },
            CNode::Pipeline(stages, input) => {
                let mut sig = Signal::Real(input.eval(env, n).into_f());
                for s in stages.iter_mut() {
                    sig = s.process(sig);
                }
                Val::F(sig.into_real())
            }
            CNode::Pct(perc, input) => {
                let x = input.eval(env, n).into_f();
                Val::F(perc.step(&x))
            }
            CNode::Coherence { coh, a, b } => {
                let xa = a.eval(env, n).into_f();
                let xb = b.eval(env, n).into_f();
                Val::F(coh.step(&xa, &xb))
            }
            CNode::Above(l, r) => {
                let (a, b) = (l.eval(env, n), r.eval(env, n));
                Val::B(a.as_f().iter().zip(b.as_f()).map(|(x, y)| x > y).collect())
            }
            CNode::Below(l, r) => {
                let (a, b) = (l.eval(env, n), r.eval(env, n));
                Val::B(a.as_f().iter().zip(b.as_f()).map(|(x, y)| x < y).collect())
            }
            CNode::Inside { input, low, high } => {
                let x = input.eval(env, n).into_f();
                Val::B(x.iter().map(|v| *v >= *low && *v <= *high).collect())
            }
            CNode::AllOf(items) => {
                let parts: Vec<Val> = items.iter_mut().map(|c| c.eval(env, n)).collect();
                Val::B((0..n).map(|i| parts.iter().all(|p| p.as_b()[i])).collect())
            }
            CNode::AnyOf(items) => {
                let parts: Vec<Val> = items.iter_mut().map(|c| c.eval(env, n)).collect();
                Val::B((0..n).map(|i| parts.iter().any(|p| p.as_b()[i])).collect())
            }
            CNode::Sigmoid { midpoint, steepness, input } => {
                let x = input.eval(env, n).into_f();
                let midpoint = *midpoint.lock().unwrap();
                Val::F(
                    x.iter()
                        .map(|v| 1.0 / (1.0 + (-*steepness * (v - midpoint)).exp()))
                        .collect(),
                )
            }
            CNode::Linear { midpoint, slope, input } => {
                let x = input.eval(env, n).into_f();
                Val::F(x.iter().map(|v| *slope * (v - *midpoint)).collect())
            }
            CNode::Binop(op, l, r) => {
                let a = l.eval(env, n).into_f();
                let b = r.eval(env, n).into_f();
                Val::F(a.iter().zip(&b).map(|(x, y)| apply_binop(op, *x, *y)).collect())
            }
            CNode::Cond(c, t, e) => {
                let cb = c.eval(env, n);
                let tf = t.eval(env, n).into_f();
                let ef = e.eval(env, n).into_f();
                let cb = cb.as_b();
                Val::F((0..n).map(|i| if cb[i] { tf[i] } else { ef[i] }).collect())
            }
        }
    }

    /// Freeze (false) / resume (true) any windowed-percentile tracker in this
    /// node. Only a top-level `Pct` threshold node carries one; mirrors the
    /// Python evaluator driving `set_ingesting` on percentile threshold impls
    /// to freeze their window during mid-session muted rests (R6).
    fn set_ingesting(&mut self, ingesting: bool) {
        if let CNode::Pct(p, _) = self {
            p.set_ingesting(ingesting);
        }
    }
}

/// `/` mirrors refrain's binop: non-finite results (e.g. 0/0 during warm-up)
/// collapse to 0.0. Other ops are ordinary arithmetic.
fn apply_binop(op: &str, x: f64, y: f64) -> f64 {
    let r = match op {
        "+" => x + y,
        "-" => x - y,
        "*" => x * y,
        "/" => x / y,
        other => panic!("unsupported binop {other:?}"),
    };
    if r.is_finite() {
        r
    } else {
        0.0
    }
}

// --- Inhibit gate ---------------------------------------------------------

/// Output-mute hangover state machine, mirroring `MuteAction`/`FreezeAction`
/// in `primitive_impls.py` (both use the identical gate for output-muting).
/// Modeled on the stateful `Dwell` tracker: per sample, an active inhibit
/// arms the hangover to `release_samples`; thereafter the gate stays asserted
/// while the hangover counts down. State persists across chunks.
struct InhibitGate {
    release_samples: usize,
    hangover: usize,
}

impl InhibitGate {
    fn new(release_ms: f64, sample_rate_hz: f64) -> Self {
        // `max(0, round(...))` — matches MuteAction.__init__.
        let release_samples = (release_ms / 1000.0 * sample_rate_hz).round().max(0.0) as usize;
        InhibitGate { release_samples, hangover: 0 }
    }

    /// Given the per-sample `inhibit_active` stream, return the per-sample
    /// gate (`true` ⇒ output muted): `active OR within release hangover`.
    fn gate(&mut self, active: &[bool]) -> Vec<bool> {
        let n = active.len();
        let mut muted = vec![false; n];
        for i in 0..n {
            if active[i] {
                self.hangover = self.release_samples;
                muted[i] = true;
            } else if self.hangover > 0 {
                self.hangover -= 1;
                muted[i] = true;
            }
        }
        muted
    }
}

/// An inhibit's threshold. The metric chunk is computed once (in `eval_chunk`)
/// and the threshold is derived from it — mirroring the Python evaluator,
/// which feeds the already-computed `metric_chunk` to a percentile tracker
/// (and zeros to an absolute threshold). Holding the percentile tracker here
/// (rather than as a `CNode` wrapping the metric) avoids re-evaluating — and
/// thus double-advancing — the stateful metric pipeline.
enum InhibitThreshold {
    Const(f64),
    /// `absolute(value: <control_ref>)` form: the inhibit's constant fed by a
    /// shared cell so `set_control` retunes it without rebuilding the inhibit.
    ConstCell(ControlCell),
    Pct(Percentile),
}

impl InhibitThreshold {
    /// Threshold values for this chunk, given the metric chunk.
    fn eval(&mut self, metric: &[f64]) -> Vec<f64> {
        match self {
            InhibitThreshold::Const(v) => vec![*v; metric.len()],
            InhibitThreshold::ConstCell(cell) => vec![*cell.lock().unwrap(); metric.len()],
            InhibitThreshold::Pct(p) => p.step(metric),
        }
    }
}

/// One compiled inhibit: its canonical name (tap key, e.g. `inhibit/emg`), its
/// `metric` expression node, the threshold derived from the metric chunk, and
/// the output-gate state. `flag` inhibits set `gate = None` so they contribute
/// nothing to `muted` (telemetry-only, matching `FlagAction`).
struct CompiledInhibit {
    canonical_name: String,
    metric: CNode,
    threshold: InhibitThreshold,
    gate: Option<InhibitGate>,
}

// --- Staged-protocol phase cursor -----------------------------------------

/// One compiled session phase (mirrors `IRPhase`). `duration_samples` is the
/// phase's clock in samples (0 for `mode == "open"`).
struct PhaseInfo {
    name: String,
    duration_samples: usize,
    output_muted: bool,
    mode: String, // "timed" | "open" | "timed_with_floor"
    block: Option<String>,
}

/// A named activation block (mirrors `IRBlock`). `reward` selects a reward
/// bundle; empty `outputs`/`inhibits` mean "all live" (back-compat).
struct CompiledBlock {
    reward: Option<String>,
    outputs: Vec<String>,
    inhibits: Vec<String>,
}

/// A named, block-selectable reward bundle (continuous and/or event).
struct RewardBundle {
    continuous: Option<CNode>,
    event: Option<RewardEvent>,
}

/// Snapshot of the phase active during the most recently processed chunk,
/// mirroring `eval_.Evaluator.current_phase()`. Kept aligned with `last_taps`
/// (recorder seam #2). The binding layer maps this to a Python dict.
#[derive(Clone)]
pub struct PhaseSnapshot {
    pub index: i64, // -1 once terminal / before any phase
    pub name: Option<String>,
    pub mode: Option<String>,
    pub output_muted: bool,
    pub block: Option<String>,
    pub remaining_s: Option<f64>,
    pub clock_frozen: bool,
    pub held: bool,
}

impl PhaseSnapshot {
    fn terminal() -> Self {
        PhaseSnapshot {
            index: -1,
            name: None,
            mode: None,
            output_muted: false,
            block: None,
            remaining_s: None,
            clock_frozen: false,
            held: false,
        }
    }
}

/// Step a compiled `RewardEvent` for one chunk: evaluate each sub-condition,
/// recombine per `combine`, and advance the dwell. Returns the dwell `events`
/// and `holds` streams plus each sub-condition's last sample (for the
/// `reward/condition[i]` taps). Pure w.r.t. phase state — used for both the
/// default reward and every reward bundle (all bundles step every chunk so
/// their dwell timers stay warm).
fn step_reward_event(
    re: &mut RewardEvent,
    env: &HashMap<String, Val>,
    n: usize,
) -> (Vec<bool>, Vec<bool>, Vec<bool>) {
    let mut sub_streams: Vec<Vec<bool>> = Vec::with_capacity(re.sub_conditions.len());
    let mut sub_lasts: Vec<bool> = Vec::with_capacity(re.sub_conditions.len());
    for sub in re.sub_conditions.iter_mut() {
        let s = sub.eval(env, n).as_b().to_vec();
        sub_lasts.push(s.last().copied().unwrap_or(false));
        sub_streams.push(s);
    }
    let condition: Vec<bool> = (0..n)
        .map(|i| match re.combine {
            SubCombine::All => sub_streams.iter().all(|s| s[i]),
            SubCombine::Any => sub_streams.iter().any(|s| s[i]),
        })
        .collect();
    let (events, holds) = re.dwell.step(&condition);
    (events, holds, sub_lasts)
}

// --- Evaluator ------------------------------------------------------------

// --- Adaptive-tracker seed/export walk (Ask 2) ----------------------------

fn callee_of(st: &TrackerState) -> &'static str {
    match st {
        TrackerState::AutoRange { .. } => "auto_range",
        TrackerState::Percentile { .. } => "percentile",
    }
}

/// `"<entity>.<callee>"`, with a `#<n>` suffix on the 2nd+ same-keyed tracker —
/// the identical scheme as Python's `_collect_stateful_impls`.
fn next_tracker_key(name: &str, callee: &str, counts: &mut HashMap<String, u32>) -> String {
    let base = format!("{name}.{callee}");
    let c = counts.entry(base.clone()).or_insert(0);
    *c += 1;
    if *c == 1 {
        base
    } else {
        format!("{base}#{c}")
    }
}

/// Collect tracker exports under `name`, depth-first (mirrors the Python walk
/// order: derive/threshold entities, stages then upstream input).
fn export_node(
    name: &str,
    node: &CNode,
    counts: &mut HashMap<String, u32>,
    out: &mut BTreeMap<String, TrackerState>,
) {
    match node {
        CNode::Pipeline(stages, input) => {
            for s in stages {
                if let Some(st) = s.tracker_export() {
                    let key = next_tracker_key(name, callee_of(&st), counts);
                    out.insert(key, st);
                }
            }
            export_node(name, input, counts, out);
        }
        CNode::Pct(perc, input) => {
            let st = perc.export_state();
            let key = next_tracker_key(name, "percentile", counts);
            out.insert(key, st);
            export_node(name, input, counts, out);
        }
        CNode::Coherence { a, b, .. } => {
            export_node(name, a, counts, out);
            export_node(name, b, counts, out);
        }
        CNode::Above(l, r) | CNode::Below(l, r) => {
            export_node(name, l, counts, out);
            export_node(name, r, counts, out);
        }
        CNode::Binop(_, l, r) => {
            export_node(name, l, counts, out);
            export_node(name, r, counts, out);
        }
        CNode::Inside { input, .. }
        | CNode::Sigmoid { input, .. }
        | CNode::Linear { input, .. } => export_node(name, input, counts, out),
        CNode::AllOf(items) | CNode::AnyOf(items) => {
            for c in items {
                export_node(name, c, counts, out);
            }
        }
        CNode::Cond(a, b, c) => {
            export_node(name, a, counts, out);
            export_node(name, b, counts, out);
            export_node(name, c, counts, out);
        }
        _ => {}
    }
}

/// Apply seeds under `name`, walking in the same order/key scheme as
/// `export_node` so keys line up with the host's `export_state()`.
fn seed_node(
    name: &str,
    node: &mut CNode,
    seeds: &HashMap<String, TrackerState>,
    counts: &mut HashMap<String, u32>,
) {
    match node {
        CNode::Pipeline(stages, input) => {
            for s in stages.iter_mut() {
                if let Some(st) = s.tracker_export() {
                    let key = next_tracker_key(name, callee_of(&st), counts);
                    if let Some(seed) = seeds.get(&key) {
                        s.tracker_seed(seed);
                    }
                }
            }
            seed_node(name, input, seeds, counts);
        }
        CNode::Pct(perc, input) => {
            let key = next_tracker_key(name, "percentile", counts);
            if let Some(TrackerState::Percentile { value, n_eff, .. }) = seeds.get(&key) {
                perc.seed(*value, *n_eff);
            }
            seed_node(name, input, seeds, counts);
        }
        CNode::Coherence { a, b, .. } => {
            seed_node(name, a, seeds, counts);
            seed_node(name, b, seeds, counts);
        }
        CNode::Above(l, r) | CNode::Below(l, r) => {
            seed_node(name, l, seeds, counts);
            seed_node(name, r, seeds, counts);
        }
        CNode::Binop(_, l, r) => {
            seed_node(name, l, seeds, counts);
            seed_node(name, r, seeds, counts);
        }
        CNode::Inside { input, .. }
        | CNode::Sigmoid { input, .. }
        | CNode::Linear { input, .. } => seed_node(name, input, seeds, counts),
        CNode::AllOf(items) | CNode::AnyOf(items) => {
            for c in items {
                seed_node(name, c, seeds, counts);
            }
        }
        CNode::Cond(a, b, c) => {
            seed_node(name, a, seeds, counts);
            seed_node(name, b, seeds, counts);
            seed_node(name, c, seeds, counts);
        }
        _ => {}
    }
}

pub struct Evaluator {
    inputs: Vec<(String, Montage)>, // (bare input name, montage)
    derives: Vec<(String, CNode)>,
    thresholds: Vec<(String, CNode)>,
    inhibits: Vec<CompiledInhibit>,
    reward_continuous: Option<CNode>,
    reward_event: Option<RewardEvent>,
    /// Compiled v0.2 reward components (empty for v0.1 single-reward protocols).
    /// When non-empty, `eval_chunk` computes the weighted composite and binds
    /// `reward.composite` / `reward.component.<name>` into the env.
    reward_components: Vec<CompiledComponent>,
    outputs: Vec<(String, CNode)>,
    /// Canonical tap names per stream entity, parallel to the env's bare names.
    /// `input/<name>`, `derive/<name>`, `threshold/<name>` — the prefixed keys
    /// `last_taps` exposes (the env / streams maps use the bare names).
    input_canon: Vec<String>,
    derive_canon: Vec<String>,
    threshold_canon: Vec<String>,
    sample_rate_hz: f64,
    state: State,
    samples_pushed: usize,
    // --- Staged-protocol N-phase cursor (mirrors the Python evaluator) ---
    /// Compiled session phases in order; empty for protocols with no session
    /// (e.g. the bench micro corpus), where the cursor is inert.
    phases: Vec<PhaseInfo>,
    /// Named activation blocks, keyed by block name.
    blocks: BTreeMap<String, CompiledBlock>,
    /// Named, block-selectable reward bundles, keyed by bundle name. Every
    /// bundle is evaluated each chunk (warm dwell timers); the active block's
    /// bundle drives `reward.continuous` / `reward.event`.
    reward_bundles: BTreeMap<String, RewardBundle>,
    phase_index: usize,
    phase_elapsed: usize,
    held: bool,
    clock_frozen: bool,
    /// `start(skip_warmup=true)` bypasses the leading warmup phase's output
    /// mute (offline/tests); later muted phases still mute. Mirrors the Python
    /// `_skip_leading_warmup_mute` flag.
    skip_leading_warmup_mute: bool,
    /// Snapshot of the phase the most recent chunk ran under (aligned with
    /// `last_taps`); `index == -1` before any chunk / once terminal.
    phase_snapshot: PhaseSnapshot,
    /// Clinician-observation snapshot from the most recent chunk, mirroring
    /// `Evaluator.last_taps()` / `_capture_taps`. Booleans are stored as
    /// 0.0/1.0 in this single map for the golden-vector compare. Empty before
    /// the first chunk; repopulated by `eval_chunk` in every lifecycle state.
    last_taps: BTreeMap<String, f64>,
    /// Per-chunk stream snapshot cached by `step_chunk_events`, mirroring the
    /// Python `last_streams()` convention. Keys: bare names for inputs/derives/
    /// thresholds (prefix stripped), `reward.continuous`, `reward.event`,
    /// `reward.event.holds`, `output/<channel>`. Empty before the first
    /// `step_chunk_events` call. Repopulated after every call via `coerce_streams`.
    last_streams: BTreeMap<String, Vec<f64>>,
    /// Live-control registry: `control/<name>` → the bound stage parameters
    /// that consumed it at build time. Mirrors `Evaluator._control_deps`;
    /// `set_control` forwards a change to every binding here. Each `Control`
    /// shares the stage's parameter cell, so applying a value retunes the stage
    /// in place without disturbing its buffer/filter state.
    controls: HashMap<String, Vec<Control>>,
    /// Every declared `control/<name>` target (from the IR `controls` block),
    /// so `set_control` can reject an unknown name with the same semantics as
    /// the Python evaluator (KeyError) while still treating a known control
    /// with no bound stages as a no-op success.
    declared_controls: std::collections::HashSet<String>,
}

/// One compiled v0.2 reward component: the bare `name` (tap/stream key), its
/// `signal` node (a sigmoid/linear/pipeline producing a [0,1] success metric),
/// the `role` (reward vs suppress), and the weight cell read each chunk. A
/// `control_ref` weight registers a `Control::Weight` binding so `set_control`
/// retunes it; a literal weight is a fixed cell; an absent weight is 1.0.
struct CompiledComponent {
    name: String,
    role: ComponentRole,
    signal: CNode,
    weight: ControlCell,
}

#[derive(Clone, Copy)]
enum ComponentRole {
    Reward,   // contributes `signal`
    Suppress, // contributes `1 - signal`
}

/// Compiled reward-event source. Mirrors `_eval_reward_event`: a `dwell`
/// machine fed a recombined condition, plus the per-sub-condition nodes the
/// `all_of` / `any_of` expands to (single-condition dwells keep one element,
/// exposed as `reward/condition[0]`). `combine` says how the sub-conditions
/// recombine into the dwell's input.
struct RewardEvent {
    dwell: Dwell,
    sub_conditions: Vec<CNode>,
    combine: SubCombine,
}

/// How `RewardEvent` recombines its sub-conditions into the dwell's input
/// (matching `all_of` → AND, `any_of` → OR; a lone condition is `All` over the
/// single element, semantically identical).
#[derive(Clone, Copy)]
enum SubCombine {
    All,
    Any,
}

impl Evaluator {
    pub fn new(p: &Protocol, sample_rate_hz: f64, channels: &[String]) -> Self {
        // Build-time context: carries the sample rate and accumulates the
        // control → stage-parameter bindings as each controllable stage is
        // compiled (mirrors the Python evaluator building `_control_deps`
        // during `_build_pipeline`).
        let mut ctx = BuildCtx::new(sample_rate_hz);

        // One montage per declared input (keyed by its bare name). The single-
        // input protocols use `raw`; coherence uses two referential inputs.
        let inputs: Vec<(String, Montage)> = p
            .inputs
            .iter()
            .map(|(name, inp)| (name.clone(), build_montage(&inp.montage, channels)))
            .collect();

        // Build derives in dependency order. A derive may consume another
        // derive's output (e.g. `auto_range` over a `bandpower` derive), so we
        // honor the resolver's `topological_order` rather than the BTreeMap's
        // alphabetical order; otherwise a downstream derive can be evaluated
        // before its upstream is bound. Any derive missing from the topo list
        // (defensive) is appended in map order.
        let derives = order_derives(p, &mut ctx);

        let thresholds: Vec<(String, CNode)> = p
            .thresholds
            .iter()
            .map(|(name, t)| (name.clone(), build_threshold(t, &mut ctx)))
            .collect();

        // Inhibits (BTreeMap order, matching the Python evaluator's dict
        // iteration / `_compute_muted` OR-fold — order is immaterial for OR
        // but kept deterministic). Each pairs a metric node with a threshold
        // node (built via the SHARED `build_threshold_call`, with the metric
        // as the threshold's tracked signal) and an action gate.
        let inhibits = p.inhibits.values().map(|ih| build_inhibit(ih, &mut ctx)).collect();

        let (mut reward_continuous, mut reward_event) = (None, None);
        let mut reward_components: Vec<CompiledComponent> = Vec::new();
        if let Some(r) = &p.reward {
            // Components first so any control bindings register before the
            // continuous/event nodes (order is immaterial for correctness; this
            // mirrors the Python `_build_pipeline` instantiating component
            // signals alongside the reward expressions).
            reward_components =
                r.components.iter().map(|c| build_component(c, &mut ctx)).collect();
            reward_continuous = r.continuous.as_ref().map(|e| build_node(e, &mut ctx));
            reward_event = r.event.as_ref().map(|e| build_reward_event(e, &mut ctx));
        }

        let outputs = p
            .output
            .iter()
            .map(|(ch, e)| (ch.clone(), build_node(e, &mut ctx)))
            .collect();

        // Canonical tap names, parallel to each entity's compiled list above
        // (same iteration order — BTreeMap / topo for derives). These are the
        // prefixed keys `last_taps` exposes; the env uses the bare names.
        let input_canon: Vec<String> =
            inputs.iter().map(|(n, _)| p.inputs[n].canonical_name.clone()).collect();
        let derive_canon: Vec<String> =
            derives.iter().map(|(n, _)| p.derives[n].canonical_name.clone()).collect();
        let threshold_canon: Vec<String> =
            thresholds.iter().map(|(n, _)| p.thresholds[n].canonical_name.clone()).collect();

        // Staged-protocol phases (empty for protocols with no session). Each
        // phase's clock is in samples; `open` phases have no clock (0).
        let phases: Vec<PhaseInfo> = p
            .session
            .as_ref()
            .map(|s| {
                s.phases
                    .iter()
                    .map(|ph| PhaseInfo {
                        name: ph.name.clone(),
                        duration_samples: (ph.duration_ms / 1000.0 * sample_rate_hz).round()
                            as usize,
                        output_muted: ph.output_muted,
                        mode: ph.mode.clone(),
                        block: ph.block.clone(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        // Named activation blocks (declarative thresholds are not needed at
        // runtime; bundle selection pulls the block's threshold transitively).
        let blocks: BTreeMap<String, CompiledBlock> = p
            .blocks
            .iter()
            .map(|(name, b)| {
                (
                    name.clone(),
                    CompiledBlock {
                        reward: b.reward.clone(),
                        outputs: b.output.clone(),
                        inhibits: b.inhibits.clone(),
                    },
                )
            })
            .collect();

        // Named reward bundles, compiled like the top-level reward (all bundles
        // are evaluated every chunk so their dwell timers stay warm; the active
        // block selects which drives reward.continuous/reward.event).
        let reward_bundles: BTreeMap<String, RewardBundle> = p
            .reward_bundles
            .iter()
            .map(|(name, rb)| {
                (
                    name.clone(),
                    RewardBundle {
                        continuous: rb.continuous.as_ref().map(|e| build_node(e, &mut ctx)),
                        event: rb.event.as_ref().map(|e| build_reward_event(e, &mut ctx)),
                    },
                )
            })
            .collect();

        Evaluator {
            inputs,
            derives,
            thresholds,
            inhibits,
            reward_continuous,
            reward_event,
            reward_components,
            outputs,
            input_canon,
            derive_canon,
            threshold_canon,
            sample_rate_hz,
            state: State::Ready,
            samples_pushed: 0,
            phases,
            blocks,
            reward_bundles,
            phase_index: 0,
            phase_elapsed: 0,
            held: false,
            clock_frozen: false,
            skip_leading_warmup_mute: false,
            phase_snapshot: PhaseSnapshot::terminal(),
            last_taps: BTreeMap::new(),
            last_streams: BTreeMap::new(),
            controls: ctx.controls,
            declared_controls: p
                .controls
                .values()
                .map(|c| c.canonical_name.clone())
                .collect(),
        }
    }

    /// Compact adaptive-tracker state for cross-session persistence (Ask 2).
    /// Walks derives then thresholds (the same entities the Python
    /// `Evaluator.export_state` collects), keying each `auto_range`/`percentile`
    /// tracker by `"<entity>.<callee>"`. Parity with Python is structural: the
    /// same key scheme and the same `percentile_linear` math.
    pub fn export_state(&self) -> BTreeMap<String, TrackerState> {
        let mut out = BTreeMap::new();
        let mut counts: HashMap<String, u32> = HashMap::new();
        for (name, node) in self.derives.iter() {
            export_node(name, node, &mut counts, &mut out);
        }
        for (name, node) in self.thresholds.iter() {
            export_node(name, node, &mut counts, &mut out);
        }
        out
    }

    /// Re-prime stateful trackers from a prior session's `export_state()`,
    /// passed as the JSON object the Python host serializes. Unknown keys are
    /// ignored (the protocol may have changed). Applied at construction.
    pub fn seed_from_json(&mut self, json: &str) {
        let raw: HashMap<String, serde_json::Value> = match serde_json::from_str(json) {
            Ok(m) => m,
            Err(_) => return,
        };
        let seeds: HashMap<String, TrackerState> = raw
            .iter()
            .filter_map(|(k, v)| {
                let obj = v.as_object()?;
                let n_eff = obj.get("n_eff").and_then(|x| x.as_u64()).unwrap_or(0);
                if obj.contains_key("low") {
                    Some((
                        k.clone(),
                        TrackerState::AutoRange {
                            low: obj.get("low")?.as_f64()?,
                            high: obj.get("high")?.as_f64()?,
                            n_eff,
                        },
                    ))
                } else if obj.contains_key("value") {
                    Some((
                        k.clone(),
                        TrackerState::Percentile {
                            value: obj.get("value")?.as_f64()?,
                            target_pct: obj.get("target_pct").and_then(|x| x.as_f64()).unwrap_or(0.0),
                            n_eff,
                        },
                    ))
                } else {
                    None
                }
            })
            .collect();
        if seeds.is_empty() {
            return;
        }
        let mut counts: HashMap<String, u32> = HashMap::new();
        for (name, node) in self.derives.iter_mut() {
            seed_node(name, node, &seeds, &mut counts);
        }
        for (name, node) in self.thresholds.iter_mut() {
            seed_node(name, node, &seeds, &mut counts);
        }
    }

    /// Live-retune a clinician control in place, mirroring
    /// `eval_.Evaluator.set_control`. Resolves `name` to its canonical target
    /// `control/<name>` and forwards `value` to every stage parameter that was
    /// built from a call referencing that control. Buffer/filter state is
    /// PRESERVED across the change — only the targeted parameter moves.
    ///
    /// Errors on an unknown control name (`Err(String)`), mirroring the Python
    /// `set_control` raising `KeyError`. A known control with no bound stages
    /// (e.g. only consumed by coefficient baking) is a no-op success, matching
    /// Python updating `_controls` even when `_control_deps` has no entry.
    pub fn set_control(&mut self, name: &str, value: f64) -> Result<(), String> {
        let target = format!("control/{name}");
        if !self.declared_controls.contains(&target) {
            return Err(format!("no control named {name:?}"));
        }
        if let Some(bindings) = self.controls.get(&target) {
            for ctrl in bindings {
                ctrl.apply(value);
            }
        }
        Ok(())
    }

    /// `eval_.Evaluator.start`: enter `warmup` (or directly `run` if the
    /// protocol has no warmup-muted phase). `skip_warmup` jumps straight to
    /// `run`. Call before the first event-emitting chunk.
    pub fn start(&mut self, skip_warmup: bool) {
        self.samples_pushed = 0;
        self.phase_index = 0;
        self.phase_elapsed = 0;
        self.held = false;
        self.clock_frozen = false;
        self.skip_leading_warmup_mute = skip_warmup;
        let first_muted = self.phases.first().map(|p| p.output_muted).unwrap_or(false);
        self.state = if skip_warmup || self.phases.is_empty() || !first_muted {
            State::Run
        } else {
            State::Warmup
        };
        self.snapshot_current_phase();
    }

    /// `eval_.Evaluator.stop`: end the session.
    pub fn stop(&mut self) {
        self.state = State::Stopped;
    }

    pub fn state(&self) -> State {
        self.state
    }

    /// Seconds of warmup remaining, mirroring `eval_.Evaluator.warmup_remaining_s`.
    /// Returns 0.0 unless the evaluator is in `Warmup` state.
    pub fn warmup_remaining_s(&self) -> f64 {
        if self.state != State::Warmup {
            return 0.0;
        }
        let dur = self.phases.get(self.phase_index).map(|p| p.duration_samples).unwrap_or(0);
        dur.saturating_sub(self.phase_elapsed) as f64 / self.sample_rate_hz
    }

    /// End the current phase now and enter the next; advancing past the last
    /// phase transitions to `stopped`. Returns false if already stopped.
    /// Mirrors `eval_.Evaluator.advance_phase`.
    pub fn advance_phase(&mut self) -> bool {
        if self.state == State::Stopped {
            return false;
        }
        self.goto_next_phase();
        self.snapshot_current_phase();
        true
    }

    /// Extend a `timed_with_floor` phase past its floor (suppress auto-advance);
    /// `hold(false)` re-arms the countdown. Returns true if it took effect
    /// (current phase is timed_with_floor), else false. Mirrors `Evaluator.hold`.
    pub fn hold(&mut self, held: bool) -> bool {
        match self.phases.get(self.phase_index) {
            Some(p) if p.mode == "timed_with_floor" => {
                self.held = held;
                self.snapshot_current_phase();
                true
            }
            _ => false,
        }
    }

    /// Freeze/resume the phase clock on any phase type (transport pause).
    /// Orthogonal to output muting. Mirrors `Evaluator.set_clock_frozen`.
    pub fn set_clock_frozen(&mut self, frozen: bool) {
        self.clock_frozen = frozen;
        self.snapshot_current_phase();
    }

    /// Snapshot of the phase the most recent chunk ran under (aligned with
    /// `last_taps`). Mirrors `eval_.Evaluator.current_phase`.
    pub fn current_phase(&self) -> PhaseSnapshot {
        self.phase_snapshot.clone()
    }

    fn snapshot_current_phase(&mut self) {
        self.phase_snapshot = match self.phases.get(self.phase_index) {
            None => PhaseSnapshot::terminal(),
            Some(p) => {
                let has_clock = (p.mode == "timed" || p.mode == "timed_with_floor")
                    && !self.held
                    && !self.clock_frozen;
                let remaining = if has_clock {
                    Some(
                        p.duration_samples.saturating_sub(self.phase_elapsed) as f64
                            / self.sample_rate_hz,
                    )
                } else {
                    None
                };
                PhaseSnapshot {
                    index: self.phase_index as i64,
                    name: Some(p.name.clone()),
                    mode: Some(p.mode.clone()),
                    output_muted: p.output_muted,
                    block: p.block.clone(),
                    remaining_s: remaining,
                    clock_frozen: self.clock_frozen,
                    held: self.held,
                }
            }
        };
    }

    /// Whether the current phase suppresses patient-facing output (any
    /// `output_muted` phase, except the leading warmup phase when
    /// `skip_warmup` bypassed it). Mirrors `_phase_mutes_output`.
    fn phase_mutes_output(&self) -> bool {
        match self.phases.get(self.phase_index) {
            Some(p) if p.output_muted => {
                !(self.skip_leading_warmup_mute && self.phase_index == 0)
            }
            _ => false,
        }
    }

    fn goto_next_phase(&mut self) {
        if self.state == State::Warmup {
            self.state = State::Run;
        }
        self.phase_index += 1;
        self.phase_elapsed = 0;
        self.held = false;
        if self.phase_index >= self.phases.len() {
            self.state = State::Stopped;
        }
    }

    /// Shared per-chunk computation: runs inputs/derives/thresholds/inhibits/
    /// reward and every output channel, returning the populated `env`, the
    /// combined per-sample `muted` gate, and the per-output channel `Val`
    /// (boolean → event channel, float → value channel). Both `step_chunk`
    /// (streams) and `step_chunk_events` (events) build on this so the
    /// interpreter runs exactly once per output and is never duplicated.
    fn eval_chunk(
        &mut self,
        chunk: &[Vec<f64>],
    ) -> (HashMap<String, Val>, Vec<bool>, Vec<(String, Val)>) {
        let n = chunk.len();
        let mut env: HashMap<String, Val> = HashMap::new();

        // Taps snapshot for this chunk (mirrors `_capture_taps`). Booleans
        // stored as 0.0/1.0; populated identically in warmup and run.
        // Like the Python `_capture_taps`, this UPDATES the previous snapshot
        // in place rather than rebuilding it, so a conditionally-written key
        // (e.g. `reward/continuous`, tapped only while a reward bundle is
        // active) persists with its last value once any chunk has written it.
        // For stable-keyset (blockless) protocols this is identical to a fresh
        // map; for staged protocols it keeps the Rust keyset matching Python's.
        let mut taps: BTreeMap<String, f64> = std::mem::take(&mut self.last_taps);

        // Staged-protocol phase context for this chunk. All values are owned so
        // nothing borrows `self` across the mutable stage iterations below.
        // For blockless protocols (empty phases / no blocks) these are all
        // inert: no muting, no masking, no freeze, no bundle selection.
        let mutes_output = self.phase_mutes_output();
        let freeze_ingest = self
            .phases
            .get(self.phase_index)
            .map(|p| p.output_muted && self.phase_index > 0)
            .unwrap_or(false);
        let active_block = self
            .phases
            .get(self.phase_index)
            .and_then(|p| p.block.as_ref())
            .and_then(|bn| self.blocks.get(bn));
        let active_bundle: Option<String> = active_block.and_then(|b| b.reward.clone());
        let output_filter: Option<std::collections::HashSet<String>> = active_block
            .filter(|b| !b.outputs.is_empty())
            .map(|b| b.outputs.iter().cloned().collect());
        let inhibit_filter: Option<std::collections::HashSet<String>> = active_block
            .filter(|b| !b.inhibits.is_empty())
            .map(|b| b.inhibits.iter().cloned().collect());

        for (name, montage) in self.inputs.iter() {
            env.insert(name.clone(), Val::F(montage.run(chunk)));
        }

        for (name, node) in self.derives.iter_mut() {
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
        }
        for (name, node) in self.thresholds.iter_mut() {
            // R6: freeze a percentile threshold's window during mid-session
            // muted rests (no-op for absolute thresholds / non-frozen phases).
            node.set_ingesting(!freeze_ingest);
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
        }

        // input/derive/threshold taps — last sample of each canonical stream.
        for (i, (name, _)) in self.inputs.iter().enumerate() {
            last_f(&mut taps, &self.input_canon[i], env.get(name));
        }
        for (i, (name, _)) in self.derives.iter().enumerate() {
            last_f(&mut taps, &self.derive_canon[i], env.get(name));
        }
        for (i, (name, _)) in self.thresholds.iter().enumerate() {
            last_f(&mut taps, &self.threshold_canon[i], env.get(name));
        }

        // Inhibits → combined `muted` gate (`_compute_muted`). Evaluated every
        // chunk regardless of warmup so gate/threshold state stays current
        // (only event *emission* is warmup-suppressed). Empty → all-false.
        let mut muted = vec![false; n];
        for ih in self.inhibits.iter_mut() {
            let metric = ih.metric.eval(&env, n).into_f();
            let thresh = ih.threshold.eval(&metric);
            let active: Vec<bool> =
                metric.iter().zip(&thresh).map(|(m, t)| m > t).collect();
            // `inhibit/<name>` tap: last-sample active boolean.
            if let Some(&last) = active.last() {
                taps.insert(ih.canonical_name.clone(), bool_f(last));
            }
            // Active-block inhibit masking: an inhibit not in the active block's
            // (non-empty) inhibit set does not contribute to the mute gate
            // (its gate state is left untouched, mirroring `_compute_muted`'s
            // `continue`). Empty set / no block ⇒ all inhibits gate.
            let allowed = {
                let short = ih
                    .canonical_name
                    .split_once('/')
                    .map(|(_, n)| n)
                    .unwrap_or(ih.canonical_name.as_str());
                inhibit_filter.as_ref().map_or(true, |f| f.contains(short))
            };
            // flag actions (`gate == None`) contribute nothing to `muted`.
            if allowed {
                if let Some(gate) = ih.gate.as_mut() {
                    let g = gate.gate(&active);
                    for i in 0..n {
                        muted[i] |= g[i];
                    }
                }
            }
        }
        // `muted` tap: combined gate last sample; false when there are no
        // inhibits (the all-false vector's last sample).
        taps.insert("muted".to_string(), bool_f(muted.last().copied().unwrap_or(false)));

        // Weighted composite (v0.2). Computed BEFORE reward.continuous /
        // reward.event so that `continuous = reward.composite` and a
        // `dwell(condition: above(reward.composite, …))` can reference it.
        // Mirrors `_process_chunk`'s composite block EXACTLY: per-component
        // [0,1]-clipped signal, role rule (reward → signal, suppress → 1-signal),
        // weighted average with the all-zero-weight guard → 0.0.
        if !self.reward_components.is_empty() {
            let mut num = vec![0.0_f64; n];
            let mut weight_sum = vec![0.0_f64; n];
            // Collect (name, signal) so we can bind component streams after the
            // borrow of `self.reward_components` ends.
            let mut component_signals: Vec<(String, Vec<f64>)> =
                Vec::with_capacity(self.reward_components.len());
            for comp in self.reward_components.iter_mut() {
                let signal: Vec<f64> = comp
                    .signal
                    .eval(&env, n)
                    .into_f()
                    .iter()
                    .map(|v| v.clamp(0.0, 1.0))
                    .collect();
                let w = *comp.weight.lock().unwrap();
                for i in 0..n {
                    let success = match comp.role {
                        ComponentRole::Reward => signal[i],
                        ComponentRole::Suppress => 1.0 - signal[i],
                    };
                    num[i] += w * success;
                    weight_sum[i] += w;
                }
                component_signals.push((comp.name.clone(), signal));
            }
            let composite: Vec<f64> = (0..n)
                .map(|i| if weight_sum[i] > 0.0 { num[i] / weight_sum[i] } else { 0.0 })
                .collect();

            // Taps: `reward/composite` + `reward/component[<name>]` (last sample),
            // matching `_capture_taps`.
            if let Some(&last) = composite.last() {
                taps.insert("reward/composite".to_string(), last);
            }
            for (name, sig) in component_signals.iter() {
                if let Some(&last) = sig.last() {
                    taps.insert(format!("reward/component[{name}]"), last);
                }
            }

            // env bindings: `reward.composite` (the CNode::Reward("composite")
            // lookup key AND the Python stream key), `reward.<name>.signal`
            // (the CNode::Reward("<name>.signal") lookup key), and
            // `reward.component.<name>` (the Python *stream* key — a value-only
            // binding for `coerce_streams`, never read by a CNode).
            env.insert("reward.composite".to_string(), Val::F(composite));
            for (name, sig) in component_signals {
                env.insert(format!("reward.{name}.signal"), Val::F(sig.clone()));
                env.insert(format!("reward.component.{name}"), Val::F(sig));
            }
        }

        if let Some(node) = self.reward_continuous.as_mut() {
            let v = node.eval(&env, n);
            // `reward/continuous` tap: pre-gating last sample.
            last_f(&mut taps, "reward/continuous", Some(&v));
            env.insert("reward.continuous".to_string(), v);
        }
        if let Some(re) = self.reward_event.as_mut() {
            // Evaluate each sub-condition (the `all_of`/`any_of` elements, or a
            // single condition exposed as `[0]`) and tap its last sample, then
            // recombine into the dwell's input — identical to `_eval_reward_event`.
            let mut sub_streams: Vec<Vec<bool>> = Vec::with_capacity(re.sub_conditions.len());
            for (i, sub) in re.sub_conditions.iter_mut().enumerate() {
                let s = sub.eval(&env, n).as_b().to_vec();
                if let Some(&last) = s.last() {
                    taps.insert(format!("reward/condition[{i}]"), bool_f(last));
                }
                sub_streams.push(s);
            }
            let condition: Vec<bool> = (0..n)
                .map(|i| match re.combine {
                    SubCombine::All => sub_streams.iter().all(|s| s[i]),
                    SubCombine::Any => sub_streams.iter().any(|s| s[i]),
                })
                .collect();
            let (events, holds) = re.dwell.step(&condition);
            // `reward/event`: did the dwell fire ANY sample this chunk.
            taps.insert("reward/event".to_string(), bool_f(events.iter().any(|&b| b)));
            // `reward/event.holds`: last sample.
            if let Some(&last) = holds.last() {
                taps.insert("reward/event.holds".to_string(), bool_f(last));
            }
            env.insert("reward.event".to_string(), Val::B(events));
            env.insert("reward.event.holds".to_string(), Val::B(holds));
        }

        // Reward bundles (staged protocols): evaluate EVERY bundle each chunk
        // so their dwell timers stay warm; the ACTIVE block's bundle drives
        // reward.continuous / reward.event (overriding the default reward, which
        // is empty for bundle-only protocols). Inert when no bundles exist.
        for (bname, bundle) in self.reward_bundles.iter_mut() {
            let is_active = active_bundle.as_deref() == Some(bname.as_str());
            if let Some(cont) = bundle.continuous.as_mut() {
                let v = cont.eval(&env, n);
                if is_active {
                    last_f(&mut taps, "reward/continuous", Some(&v));
                    env.insert("reward.continuous".to_string(), v);
                }
            }
            if let Some(re) = bundle.event.as_mut() {
                let (events, holds, sub_lasts) = step_reward_event(re, &env, n);
                if is_active {
                    for (i, &last) in sub_lasts.iter().enumerate() {
                        taps.insert(format!("reward/condition[{i}]"), bool_f(last));
                    }
                    taps.insert("reward/event".to_string(), bool_f(events.iter().any(|&b| b)));
                    if let Some(&last) = holds.last() {
                        taps.insert("reward/event.holds".to_string(), bool_f(last));
                    }
                    env.insert("reward.event".to_string(), Val::B(events));
                    env.insert("reward.event.holds".to_string(), Val::B(holds));
                }
            }
        }

        let mut outs: Vec<(String, Val)> = Vec::new();
        for (ch, node) in self.outputs.iter_mut() {
            let v = node.eval(&env, n);
            outs.push((ch.clone(), v));
        }

        // Output activation gate (mirrors `_process_chunk`): silence any channel
        // not in the active block's output set, and silence ALL channels during
        // an output-muted phase — so taps/streams/emission reflect what the
        // patient actually received. Inert for blockless / non-muted phases.
        for (ch, v) in outs.iter_mut() {
            let silence =
                mutes_output || output_filter.as_ref().is_some_and(|f| !f.contains(ch));
            if silence {
                *v = match v {
                    Val::B(b) => Val::B(vec![false; b.len()]),
                    Val::F(f) => Val::F(vec![0.0; f.len()]),
                };
            }
        }

        // output taps — post-gate/clamp. Event channels: `any fired this chunk`
        // (after the `& ~muted` gate). Value channels: last sample of the
        // clamped/muted value. Mirrors `_capture_taps`'s per-channel block.
        for (ch, v) in outs.iter() {
            let key = format!("output/{ch}");
            match v {
                Val::B(b) => {
                    if !b.is_empty() {
                        let any = b.iter().enumerate().any(|(i, &x)| x && !muted[i]);
                        taps.insert(key, bool_f(any));
                    }
                }
                Val::F(f) => {
                    if let Some((i, &last)) = f.iter().enumerate().last() {
                        let gated = if muted[i] { 0.0 } else { last.clamp(0.0, 1.0) };
                        taps.insert(key, gated);
                    }
                }
            }
        }

        // Numeric phase taps (recorder per-block telemetry / tap-frame phase
        // stamping). Plain f64 — never coerced to bool.
        taps.insert("phase/index".to_string(), self.phase_index as f64);
        taps.insert(
            "phase/output_muted".to_string(),
            bool_f(self.phases.get(self.phase_index).map(|p| p.output_muted).unwrap_or(false)),
        );

        self.last_taps = taps;
        // Capture the phase snapshot at the SAME point as the taps, reflecting
        // the phase THIS chunk ran under (before `advance` moves the cursor) —
        // keeps current_phase() aligned with last_taps (recorder seam #2).
        self.snapshot_current_phase();
        (env, muted, outs)
    }

    /// Clinician-observation snapshot from the most recent `step_chunk` /
    /// `step_chunk_events`, mirroring `Evaluator.last_taps()`. Keys are the
    /// canonical prefixed names (`input/raw`, `derive/<name>`,
    /// `threshold/<name>`, `inhibit/<name>`, `muted`, `reward/...`,
    /// `output/<channel>`); booleans are represented as 0.0/1.0. Empty before
    /// the first chunk. Returns a copy so callers may persist it.
    pub fn last_taps(&self) -> BTreeMap<String, f64> {
        self.last_taps.clone()
    }

    /// Per-chunk stream snapshot cached by `step_chunk_events`, mirroring the
    /// Python `last_streams()` contract. Keys follow the same convention as
    /// `step_chunk`: bare names for inputs/derives/thresholds (prefix stripped),
    /// `reward.continuous`, `reward.event`, `reward.event.holds`, and
    /// `output/<channel>`. Empty before the first `step_chunk_events` call.
    /// Returns a copy so callers may persist it.
    pub fn last_streams(&self) -> BTreeMap<String, Vec<f64>> {
        self.last_streams.clone()
    }

    /// Coerce the `eval_chunk` results into the `last_streams` format and
    /// cache them in `self.last_streams`. Shared by `step_chunk` (which
    /// returns immediately) and `step_chunk_events` (which caches for
    /// `last_streams()` access). Mirrors the Python `_process_chunk`'s
    /// `self._last_streams` population block.
    ///
    /// Keys: bare stream names (prefix stripped via `split_once('/').map(…)`),
    /// `reward.continuous`, `reward.event`, `reward.event.holds`,
    /// `output/<channel>`. Output vals are gated+clamped exactly like
    /// `step_chunk` so both callers see identical per-sample values.
    fn coerce_streams(
        env: HashMap<String, Val>,
        muted: &[bool],
        outs: Vec<(String, Val)>,
    ) -> BTreeMap<String, Vec<f64>> {
        let mut result: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        for (k, v) in env {
            // `reward.<name>.signal` is an internal CNode lookup key (the
            // member-access form). It is NOT part of the last_streams contract:
            // Python exposes a component's signal as `reward.component.<name>`.
            // Skip it so the Rust stream key set matches the Python backend.
            if k.starts_with("reward.") && k.ends_with(".signal") {
                continue;
            }
            result.insert(k, v.into_f());
        }
        for (ch, v) in outs {
            // Gating mirrors `_process_chunk`'s `per_channel_output` (and thus
            // the recorded `output/<channel>` streams): event channels are
            // `values & ~muted`; value channels are `where(muted, 0, clip)`.
            let f = match v {
                Val::B(b) => b
                    .iter()
                    .enumerate()
                    .map(|(i, &x)| if x && !muted[i] { 1.0 } else { 0.0 })
                    .collect(),
                Val::F(f) => f
                    .iter()
                    .enumerate()
                    .map(|(i, &x)| if muted[i] { 0.0 } else { x.clamp(0.0, 1.0) })
                    .collect(),
            };
            result.insert(format!("output/{ch}"), f);
        }
        result
    }

    /// Process one chunk and return `{stream: values}`, matching the Python
    /// evaluator's `last_streams()` contract. Output channels are coerced to
    /// floats (booleans → 0/1, floats clamped to [0, 1]); the streams API is
    /// stateless w.r.t. the warmup lifecycle, mirroring tap capture which
    /// runs in every state.
    ///
    /// NOTE: The bench `ChunkedRunner` uses this method directly. Do NOT remove.
    /// It does NOT advance the lifecycle cursor/state machine and does NOT
    /// cache `last_streams` — call `step_chunk_events` for the
    /// lifecycle-aware path (events + cursor advance + last_streams cache).
    pub fn step_chunk(&mut self, chunk: &[Vec<f64>]) -> BTreeMap<String, Vec<f64>> {
        let (env, muted, outs) = self.eval_chunk(chunk);
        Self::coerce_streams(env, &muted, outs)
    }

    /// Process one chunk and emit feedback `Event`s, mirroring
    /// `eval_.Evaluator.step_chunk` / `_process_chunk`. The same per-chunk
    /// computation runs in every state (so primitive state stays current),
    /// but during `warmup` events are suppressed. Advances `samples_pushed`
    /// and transitions `warmup → run` once the warmup window is satisfied.
    ///
    /// Also caches the per-chunk streams map into `self.last_streams` (via
    /// `coerce_streams`) so `last_streams()` can surface it without a second
    /// `eval_chunk` call — which would double-step DSP state.
    ///
    /// IMPORTANT: events are emitted in IR output-channel declaration order,
    /// interleaving event-kind and value-kind channels exactly as the Python
    /// `_process_chunk` does — do NOT split into separate event/value passes.
    ///
    /// NOTE: pure-Rust callers must not call this after `stop()` — the
    /// Python/FFI layer converts the panic to a RuntimeError before it
    /// reaches user code.
    pub fn step_chunk_events(&mut self, chunk: &[Vec<f64>]) -> Vec<Event> {
        if self.state == State::Ready {
            self.start(false);
        }
        if self.state == State::Stopped {
            panic!("step_chunk_events called after stop()");
        }

        let n = chunk.len();
        let t0_s = self.samples_pushed as f64 / self.sample_rate_hz;
        // Generalized from "is warmup" to "current phase mutes output", so
        // mid-session muted rests suppress emission too (not just the first
        // phase). Output channels are already silenced in eval_chunk for muted
        // phases; this skips the per-channel event emission below.
        let suppress_output = self.phase_mutes_output();

        // `muted` is the combined inhibit gate (`_compute_muted`), computed in
        // `eval_chunk`. Inhibit state advances even during warmup; only event
        // emission below is warmup-suppressed.
        let (env, muted, outs) = self.eval_chunk(chunk);

        // Clone outs for stream coercion so we can still iterate outs below
        // in IR declaration order for event emission. No second eval_chunk =
        // no double-stepping of DSP state.
        let outs_for_streams: Vec<(String, Val)> =
            outs.iter().map(|(ch, v)| (ch.clone(), v.clone())).collect();
        self.last_streams = Self::coerce_streams(env, &muted, outs_for_streams);

        let mut events: Vec<Event> = Vec::new();
        if suppress_output {
            // Still advance the cursor / transition, but emit nothing.
            self.advance(n);
            return events;
        }

        // Iterate outs in IR declaration order — mirrors Python's
        // `for channel, (out_chunk, is_event) in per_channel_output.items()`.
        for (channel, v) in outs {
            match v {
                Val::B(values) => {
                    // Event channel: gated bool = values & ~muted; one Event
                    // per true sample.
                    for (i, &on) in values.iter().enumerate() {
                        if on && !muted[i] {
                            events.push(Event {
                                timestamp_s: t0_s + i as f64 / self.sample_rate_hz,
                                channel: channel.clone(),
                                kind: "event".to_string(),
                                value: None,
                            });
                        }
                    }
                }
                Val::F(values) => {
                    // Value channel: clamp to [0, 1], zero where muted, emit
                    // one Event per chunk carrying the mean.
                    let gated: Vec<f64> = values
                        .iter()
                        .enumerate()
                        .map(|(i, &x)| if muted[i] { 0.0 } else { x.clamp(0.0, 1.0) })
                        .collect();
                    let mean = if gated.is_empty() {
                        0.0
                    } else {
                        gated.iter().sum::<f64>() / gated.len() as f64
                    };
                    events.push(Event {
                        timestamp_s: t0_s,
                        channel: channel.clone(),
                        kind: "value".to_string(),
                        value: Some(mean),
                    });
                }
            }
        }

        self.advance(n);
        events
    }

    /// Advance the sample + phase cursor at the end of a chunk. Accumulates the
    /// current phase's elapsed clock (unless frozen) and auto-advances per the
    /// phase mode rule (mirrors `_advance_if_due`/`_goto_next_phase`): `timed`
    /// and un-held `timed_with_floor` auto-advance at their duration; `open`
    /// and frozen/held phases never auto-advance.
    fn advance(&mut self, n: usize) {
        self.samples_pushed += n;
        let Some(p) = self.phases.get(self.phase_index) else {
            return;
        };
        if !self.clock_frozen {
            self.phase_elapsed += n;
        }
        let auto = p.mode == "timed" || (p.mode == "timed_with_floor" && !self.held);
        if auto
            && !self.clock_frozen
            && p.mode != "open"
            && self.phase_elapsed >= p.duration_samples
        {
            self.goto_next_phase();
        }
    }
}

/// Compile every derive into a `(bare_name, CNode)` list ordered so each
/// derive's upstreams are bound before it runs. Follows the resolver's
/// `topological_order` (entries like `"derive/<name>"`); derives absent from
/// the topo list are appended in BTreeMap order as a fallback.
fn order_derives(p: &Protocol, ctx: &mut BuildCtx) -> Vec<(String, CNode)> {
    let mut emitted: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut out: Vec<(String, CNode)> = Vec::new();
    for entry in &p.topological_order {
        let name = bare(entry);
        if let Some(d) = p.derives.get(&name) {
            if emitted.insert(name.clone()) {
                out.push((name.clone(), build_node(&d.expression, ctx)));
            }
        }
    }
    for (name, d) in p.derives.iter() {
        if emitted.insert(name.clone()) {
            out.push((name.clone(), build_node(&d.expression, ctx)));
        }
    }
    out
}

// --- Compilation helpers --------------------------------------------------

fn bare(canonical: &str) -> String {
    canonical.split_once('/').map(|(_, n)| n).unwrap_or(canonical).to_string()
}

/// bool → tap float (`0.0`/`1.0`), matching the Python taps' `float(bool)`.
fn bool_f(b: bool) -> f64 {
    if b {
        1.0
    } else {
        0.0
    }
}

/// Insert the last sample of `val` (as f64) under `key`, mirroring
/// `_capture_taps`'s `if chunk is not None and chunk.size`. No-op when the
/// stream is absent or empty.
fn last_f(taps: &mut BTreeMap<String, f64>, key: &str, val: Option<&Val>) {
    if let Some(v) = val {
        match v {
            Val::F(f) => {
                if let Some(&last) = f.last() {
                    taps.insert(key.to_string(), last);
                }
            }
            Val::B(b) => {
                if let Some(&last) = b.last() {
                    taps.insert(key.to_string(), bool_f(last));
                }
            }
        }
    }
}

fn is_dsp(callee: &str) -> bool {
    matches!(
        callee,
        "bandpass"
            | "hilbert"
            | "magnitude"
            | "rectify"
            | "smooth"
            | "differentiate"
            | "auto_range"
            | "bandpower"
            | "autocorr"
    )
}

fn positional(args: &[crate::ir::Arg], idx: usize) -> &Expr {
    args.iter()
        .filter(|a| a.name.is_none())
        .nth(idx)
        .map(|a| &a.value)
        .unwrap_or_else(|| panic!("missing positional arg {idx}"))
}

fn num_named(args: &[crate::ir::Arg], name: &str) -> Option<f64> {
    args.iter().find_map(|a| match (&a.name, &a.value) {
        (Some(nm), Expr::Number { value }) if nm == name => Some(*value),
        _ => None,
    })
}

fn string_arg<'a>(args: &'a [crate::ir::Arg], name: &str) -> Option<&'a str> {
    args.iter().find_map(|a| match (&a.name, &a.value) {
        (Some(nm), Expr::Str { value }) if nm == name => Some(value.as_str()),
        _ => None,
    })
}

/// Read a named 2-tuple/2-array of numbers (e.g. `auto_range`'s
/// `percentile: (low, high)`). Returns None when the arg is absent.
fn tuple2_named(args: &[crate::ir::Arg], name: &str) -> Option<(f64, f64)> {
    let val = args.iter().find(|a| a.name.as_deref() == Some(name)).map(|a| &a.value)?;
    match val {
        Expr::Tuple { elements } | Expr::Array { elements } if elements.len() == 2 => {
            let num = |e: &Expr| match e {
                Expr::Number { value } => *value,
                _ => panic!("{name}: tuple bounds must be numbers"),
            };
            Some((num(&elements[0]), num(&elements[1])))
        }
        _ => panic!("{name}: must be a 2-tuple of numbers"),
    }
}

/// Resolve the upstream input expression for a DSP-pipeline call. Most
/// pipeline stages thread the previous stage as positional arg 0; `bandpower`
/// (used standalone as a derive formula) takes its input as a named `input:`
/// arg.
fn dsp_input<'a>(callee: &str, args: &'a [crate::ir::Arg]) -> &'a Expr {
    if callee == "bandpower" {
        return args
            .iter()
            .find(|a| a.name.as_deref() == Some("input"))
            .map(|a| &a.value)
            .unwrap_or_else(|| panic!("bandpower: missing `input` arg"));
    }
    positional(args, 0)
}

fn build_montage(expr: &Expr, channels: &[String]) -> Montage {
    match expr {
        Expr::Call { callee, args, .. } if callee == "referential" => Montage::referential(
            string_arg(args, "active").expect("referential needs `active`"),
            string_arg(args, "reference").expect("referential needs `reference`"),
            channels,
        ),
        Expr::Call { callee, args, .. } if callee == "bipolar" => Montage::bipolar(
            string_arg(args, "plus").expect("bipolar needs `plus`"),
            string_arg(args, "minus").expect("bipolar needs `minus`"),
            channels,
        ),
        Expr::Call { callee, .. } if callee == "passthrough" => Montage::passthrough(channels),
        _ => panic!("PoC supports only referential, bipolar, and passthrough montages"),
    }
}

fn build_stage(
    callee: &str,
    args: &[crate::ir::Arg],
    coeffs: Option<&Coeffs>,
    ctx: &mut BuildCtx,
) -> Box<dyn Stage> {
    let need = || coeffs.unwrap_or_else(|| panic!("{callee}: missing baked coeffs"));
    match callee {
        "bandpass" => Box::new(Biquad::new(need().sos.as_ref().expect("sos"))),
        "hilbert" => {
            let c = need();
            Box::new(HilbertFir::new(
                c.fir_taps.as_ref().expect("fir_taps"),
                c.group_delay.expect("group_delay"),
            ))
        }
        "magnitude" | "rectify" => Box::new(Magnitude),
        "smooth" => {
            // Initial alpha is baked (from the default tau). If `tau` is a
            // control-ref, bind it so a live retune recomputes alpha from the
            // new tau-ms (preserving the running `prev`), matching SmoothImpl.
            let alpha = control_cell(need().alpha.expect("alpha"));
            if let Some((_, Some(target))) = num_named_controllable(args, "tau") {
                ctx.register(
                    &target,
                    Control::Smooth {
                        alpha: alpha.clone(),
                        sample_rate_hz: ctx.sample_rate_hz,
                    },
                );
            }
            Box::new(Smooth::from_cell(alpha))
        }
        "differentiate" => Box::new(Differentiate::new(need().dt.expect("dt"))),
        "auto_range" => {
            // window_samples is baked; low/high percentiles come from the
            // `percentile: (low, high)` call arg (defaults 5/95).
            let window_samples = need().window_samples.expect("auto_range window_samples");
            let (low_pct, high_pct) = tuple2_named(args, "percentile").unwrap_or((5.0, 95.0));
            Box::new(AutoRange::new(window_samples, low_pct, high_pct))
        }
        "bandpower" => {
            // Biquad bandpass (REUSE) + rolling mean-of-squares over window.
            let c = need();
            Box::new(Bandpower::new(
                c.sos.as_ref().expect("sos"),
                c.window_samples.expect("bandpower window_samples"),
            ))
        }
        "autocorr" => {
            // Rolling lag-k Pearson autocorrelation; window + lag baked at rate.
            let c = need();
            Box::new(Autocorr::new(
                c.window_samples.expect("autocorr window_samples"),
                c.lag_samples.expect("autocorr lag_samples"),
            ))
        }
        other => panic!("PoC: unsupported DSP primitive {other:?}"),
    }
}

fn build_node(e: &Expr, ctx: &mut BuildCtx) -> CNode {
    match e {
        Expr::Number { value } => CNode::Const(*value),
        // A `control_ref` in a plain value position (not a recognised tunable
        // parameter slot) evaluates to its baked default — identical to the
        // literal `number` the emitter previously baked here. Live retuning is
        // wired only where a build helper recognises the parameter (percentile
        // `target_pct`, smooth `tau`, sigmoid `midpoint`).
        Expr::ControlRef { default, .. } => CNode::Const(*default),
        Expr::Bool { value } => CNode::BoolConst(*value),
        Expr::StreamRef { target } => CNode::Stream(bare(target)),
        Expr::ThresholdRef { target } => CNode::Stream(bare(target)),
        Expr::RewardField { field_path } => CNode::Reward(field_path.clone()),
        Expr::Binop { op, left, right } => CNode::Binop(
            op.clone(),
            Box::new(build_node(left, ctx)),
            Box::new(build_node(right, ctx)),
        ),
        Expr::Conditional { cond, then, els } => CNode::Cond(
            Box::new(build_node(cond, ctx)),
            Box::new(build_node(then, ctx)),
            Box::new(build_node(els, ctx)),
        ),
        Expr::Call { callee, args, coeffs } if is_dsp(callee) => {
            // Flatten a contiguous DSP chain into one Pipeline; the complex
            // intermediate (hilbert -> magnitude) stays inside the chain.
            let mut stages: Vec<Box<dyn Stage>> = Vec::new();
            let mut cur = e;
            while let Expr::Call { callee, args, coeffs } = cur {
                if !is_dsp(callee) {
                    break;
                }
                stages.push(build_stage(callee, args, coeffs.as_ref(), ctx));
                cur = dsp_input(callee, args);
            }
            let _ = (args, coeffs);
            stages.reverse();
            CNode::Pipeline(stages, Box::new(build_node(cur, ctx)))
        }
        Expr::Call { callee, args, coeffs } => {
            build_compute_call(callee, args, coeffs.as_ref(), ctx)
        }
        _ => panic!("unexpected node in expression"),
    }
}

/// Read a `band: (low, high)` arg — a tuple of two `number` nodes. The band is
/// not baked into `coeffs` (it is cheap to read here), matching the IR-JSON.
fn band_arg(args: &[crate::ir::Arg]) -> (f64, f64) {
    let tuple = args
        .iter()
        .find(|a| a.name.as_deref() == Some("band"))
        .map(|a| &a.value)
        .expect("coherence: missing `band` arg");
    match tuple {
        Expr::Tuple { elements } | Expr::Array { elements } if elements.len() == 2 => {
            let num = |e: &Expr| match e {
                Expr::Number { value } => *value,
                _ => panic!("coherence: band bounds must be numbers"),
            };
            (num(&elements[0]), num(&elements[1]))
        }
        _ => panic!("coherence: `band` must be a 2-tuple of numbers"),
    }
}

/// Resolve a coherence input arg by name (`input_a`/`input_b`) or, failing
/// that, the i-th positional arg.
fn coherence_input(args: &[crate::ir::Arg], name: &str, pos: usize, ctx: &mut BuildCtx) -> CNode {
    let expr = args
        .iter()
        .find(|a| a.name.as_deref() == Some(name))
        .map(|a| &a.value)
        .unwrap_or_else(|| positional(args, pos));
    build_node(expr, ctx)
}

fn build_compute_call(
    callee: &str,
    args: &[crate::ir::Arg],
    coeffs: Option<&Coeffs>,
    ctx: &mut BuildCtx,
) -> CNode {
    match callee {
        "above" => CNode::Above(
            Box::new(build_node(positional(args, 0), ctx)),
            Box::new(build_node(positional(args, 1), ctx)),
        ),
        "below" => CNode::Below(
            Box::new(build_node(positional(args, 0), ctx)),
            Box::new(build_node(positional(args, 1), ctx)),
        ),
        "inside" => CNode::Inside {
            input: Box::new(build_node(positional(args, 0), ctx)),
            low: num_named(args, "low").expect("inside needs `low`"),
            high: num_named(args, "high").expect("inside needs `high`"),
        },
        "all_of" | "any_of" => {
            let arr = positional(args, 0);
            let elements = match arr {
                Expr::Array { elements } => elements,
                _ => panic!("{callee}: expected an array of conditions"),
            };
            let nodes: Vec<CNode> = elements.iter().map(|e| build_node(e, ctx)).collect();
            if callee == "all_of" {
                CNode::AllOf(nodes)
            } else {
                CNode::AnyOf(nodes)
            }
        }
        "sigmoid" => {
            // `midpoint` is the live-tunable parameter (SigmoidImpl). Accept a
            // literal or a control-ref; bind the latter so set_control updates
            // the shared midpoint cell. `steepness` stays a literal here
            // (matches SigmoidImpl picking midpoint as the controllable slot).
            let (midpoint_v, target) =
                num_named_controllable(args, "midpoint").unwrap_or((0.0, None));
            let midpoint = control_cell(midpoint_v);
            if let Some(target) = target {
                ctx.register(&target, Control::Sigmoid { midpoint: midpoint.clone() });
            }
            CNode::Sigmoid {
                midpoint,
                steepness: num_named(args, "steepness").unwrap_or(1.0),
                input: Box::new(build_node(positional(args, 0), ctx)),
            }
        }
        "linear" => CNode::Linear {
            midpoint: num_named(args, "midpoint").unwrap_or(0.0),
            slope: num_named(args, "slope").unwrap_or(1.0),
            input: Box::new(build_node(positional(args, 0), ctx)),
        },
        "coherence" => {
            let c = coeffs.unwrap_or_else(|| panic!("coherence: missing baked coeffs"));
            let nperseg = c.nperseg.expect("coherence: missing baked nperseg");
            let noverlap = c.noverlap.expect("coherence: missing baked noverlap");
            let window_samples =
                c.window_samples.expect("coherence: missing baked window_samples");
            let band = band_arg(args);
            let sample_rate_hz = ctx.sample_rate_hz;
            CNode::Coherence {
                coh: Coherence::new(sample_rate_hz, nperseg, noverlap, window_samples, band),
                a: Box::new(coherence_input(args, "input_a", 0, ctx)),
                b: Box::new(coherence_input(args, "input_b", 1, ctx)),
            }
        }
        other => panic!("PoC: unsupported compute primitive {other:?}"),
    }
}

fn build_threshold(t: &crate::ir::Threshold, ctx: &mut BuildCtx) -> CNode {
    let signal = CNode::Stream(bare(&t.signal));
    build_threshold_call(&t.threshold_call, signal, ctx)
}

/// Compile one `inhibit` block: a metric node, a threshold node (REUSES
/// `build_threshold_call`, feeding the metric as the threshold's tracked
/// signal — exactly as the Python evaluator does), and an action gate.
/// `flag` → `gate = None` (telemetry-only). `action_release_ms` defaults to
/// 200 ms when null (matching `_build_inhibit_actions`).
fn build_inhibit(ih: &crate::ir::Inhibit, ctx: &mut BuildCtx) -> CompiledInhibit {
    let metric = build_node(&ih.metric, ctx);
    let threshold = build_inhibit_threshold(&ih.threshold, ctx);
    let release_ms = ih.action_release_ms.unwrap_or(200.0);
    let gate = match ih.action_kind.as_str() {
        "mute" | "freeze" => Some(InhibitGate::new(release_ms, ctx.sample_rate_hz)),
        "flag" => None,
        other => panic!("PoC: unsupported inhibit action {other:?}"),
    };
    CompiledInhibit { canonical_name: ih.canonical_name.clone(), metric, threshold, gate }
}

/// Parse an inhibit's threshold-constructor call (`absolute(...)` /
/// `percentile(...)`) into an `InhibitThreshold` fed by the metric chunk.
/// Shares the exact constructor parsing (`absolute` value, `percentile`
/// target_pct + baked window_samples) with `build_threshold_call`.
fn build_inhibit_threshold(call: &Expr, ctx: &mut BuildCtx) -> InhibitThreshold {
    match call {
        Expr::Call { callee, args, coeffs } if callee == "percentile" => {
            let (target_pct, target) = num_named_controllable(args, "target_pct")
                .expect("percentile threshold needs a numeric or control-ref target_pct");
            let window_samples = coeffs
                .as_ref()
                .and_then(|c| c.window_samples)
                .expect("percentile: missing baked window_samples");
            let cell = control_cell(target_pct);
            if let Some(target) = target {
                ctx.register(&target, Control::Percentile { target_pct: cell.clone() });
            }
            InhibitThreshold::Pct(Percentile::from_cell(cell, window_samples))
        }
        Expr::Call { callee, args, .. } if callee == "absolute" => {
            let (v, target) = absolute_value(args);
            if let Some(target) = target {
                let cell = control_cell(v);
                ctx.register(&target, Control::Const { value: cell.clone() });
                InhibitThreshold::ConstCell(cell)
            } else {
                InhibitThreshold::Const(v)
            }
        }
        _ => panic!("PoC: unsupported inhibit threshold constructor"),
    }
}

/// Compile a threshold-constructor call (`absolute(...)` / `percentile(...)`)
/// over a pre-built `signal` node, for threshold blocks (signal = the tracked
/// stream). Inhibits parse the same constructors via `build_inhibit_threshold`
/// (sharing `absolute_value` + the same percentile fields) but bind the
/// percentile tracker to the metric chunk rather than re-evaluating a signal
/// node, so a stateful metric pipeline is not double-advanced.
fn build_threshold_call(call: &Expr, signal: CNode, ctx: &mut BuildCtx) -> CNode {
    match call {
        Expr::Call { callee, args, coeffs } if callee == "percentile" => {
            // `target_pct` is the clinician's live-tunable knob. Accept a
            // literal or a control-ref; for a control-ref, register a binding
            // sharing the percentile tracker's `target_pct` cell so set_control
            // retunes it in place (buffer state preserved).
            let (target_pct, target) = num_named_controllable(args, "target_pct")
                .expect("percentile threshold needs a numeric or control-ref target_pct");
            let window_samples = coeffs
                .as_ref()
                .and_then(|c| c.window_samples)
                .expect("percentile: missing baked window_samples");
            let cell = control_cell(target_pct);
            if let Some(target) = target {
                ctx.register(&target, Control::Percentile { target_pct: cell.clone() });
            }
            CNode::Pct(Percentile::from_cell(cell, window_samples), Box::new(signal))
        }
        Expr::Call { callee, args, .. } if callee == "absolute" => {
            let (v, target) = absolute_value(args);
            if let Some(target) = target {
                let cell = control_cell(v);
                ctx.register(&target, Control::Const { value: cell.clone() });
                CNode::ConstCell(cell)
            } else {
                CNode::Const(v)
            }
        }
        _ => panic!("PoC: unsupported threshold constructor"),
    }
}

/// `absolute(value)` — read the constant threshold value (named `value:` or
/// the first positional number). Shared by threshold blocks and inhibits.
///
/// Returns `(value, Option<target>)`: when `value:` is a `control_ref` (the
/// live-tunable form), the canonical control target is returned so the caller
/// can register a `Control::Const` binding sharing the threshold's cell.
fn absolute_value(args: &[crate::ir::Arg]) -> (f64, Option<String>) {
    for a in args.iter() {
        if a.name.as_deref() == Some("value") {
            return match &a.value {
                Expr::Number { value } => (*value, None),
                Expr::ControlRef { target, default } => (*default, Some(target.clone())),
                _ => panic!("absolute: `value:` must be a number or control_ref"),
            };
        }
    }
    // Positional fallback: `absolute(8 uV)` form (literal only; the front-end
    // emits `value:` for control-ref bindings).
    match positional(args, 0) {
        Expr::Number { value } => (*value, None),
        _ => panic!("absolute: numeric value"),
    }
}

/// Compile a v0.2 reward component (mirrors `_resolve_reward_component` +
/// `_component_weight_chunk`). The `signal` reuses `build_node`; the `weight`
/// reuses the literal-or-control-ref read used for sigmoid `midpoint`. An
/// absent weight is the implicit 1.0.
fn build_component(c: &crate::ir::RewardComponent, ctx: &mut BuildCtx) -> CompiledComponent {
    let role = match c.role.as_str() {
        "reward" => ComponentRole::Reward,
        "suppress" => ComponentRole::Suppress,
        other => panic!("unknown reward component role {other:?}"),
    };
    let signal = build_node(&c.signal, ctx);
    // Weight: a `control_ref` registers a live binding; a literal `number` is a
    // fixed cell; absent ⇒ 1.0.
    let weight = match &c.weight {
        Some(Expr::ControlRef { target, default }) => {
            let cell = control_cell(*default);
            ctx.register(target, Control::Weight { value: cell.clone() });
            cell
        }
        Some(Expr::Number { value }) => control_cell(*value),
        Some(other) => {
            panic!("reward component weight must be a control_ref or number, got {other:?}")
        }
        None => control_cell(1.0),
    };
    CompiledComponent { name: c.name.clone(), role, signal, weight }
}

/// Compile `reward.event` (a `dwell(...)` call) into a `RewardEvent`. Mirrors
/// `_eval_reward_event`: the dwell's `condition` is expanded into its `all_of`
/// / `any_of` sub-conditions (each exposed as a `reward/condition[i]` tap and
/// recombined for the dwell input); a lone condition becomes a single-element
/// `All` (exposed as `reward/condition[0]`). REUSES `build_node` for the
/// sub-condition CNodes — same compiler path as every other expression.
fn build_reward_event(event_expr: &Expr, ctx: &mut BuildCtx) -> RewardEvent {
    let Expr::Call { callee, args, coeffs } = event_expr else {
        panic!("reward.event must be a dwell(...) call");
    };
    assert_eq!(callee, "dwell", "reward.event must be a dwell(...) call");
    let dwell_samples = coeffs
        .as_ref()
        .and_then(|c| c.dwell_samples)
        .expect("dwell: missing baked dwell_samples");
    let condition = args
        .iter()
        .find(|a| a.name.as_deref() == Some("condition"))
        .map(|a| &a.value)
        .expect("dwell needs a `condition` arg");

    // Sub-condition expansion (matches the Python tap API): an `all_of` /
    // `any_of` over an array exposes each element; otherwise the single
    // condition is exposed as `reward/condition[0]`.
    let (sub_conditions, combine) = match condition {
        Expr::Call { callee, args, .. } if callee == "all_of" || callee == "any_of" => {
            if let Expr::Array { elements } = positional(args, 0) {
                let nodes = elements.iter().map(|e| build_node(e, ctx)).collect();
                let combine =
                    if callee == "all_of" { SubCombine::All } else { SubCombine::Any };
                (nodes, combine)
            } else {
                (vec![build_node(condition, ctx)], SubCombine::All)
            }
        }
        _ => (vec![build_node(condition, ctx)], SubCombine::All),
    };

    RewardEvent { dwell: Dwell::new(dwell_samples), sub_conditions, combine }
}
