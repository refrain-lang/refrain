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
    AutoRange, Bandpower, Biquad, Coherence, Differentiate, Dwell, HilbertFir, Magnitude,
    Percentile, Signal, Smooth, Stage,
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

// --- Montage --------------------------------------------------------------

enum Montage {
    Referential(Referential),
    Bipolar { plus_idx: usize, minus_idx: usize }, // BipolarImpl: plus - minus
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

    fn run(&self, chunk: &[Vec<f64>]) -> Vec<f64> {
        match self {
            Montage::Referential(r) => r.run(chunk),
            Montage::Bipolar { plus_idx, minus_idx } => {
                chunk.iter().map(|row| row[*plus_idx] - row[*minus_idx]).collect()
            }
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
    Sigmoid { midpoint: f64, steepness: f64, input: Box<CNode> },
    Linear { midpoint: f64, slope: f64, input: Box<CNode> },
    Binop(String, Box<CNode>, Box<CNode>),
    Cond(Box<CNode>, Box<CNode>, Box<CNode>),
}

impl CNode {
    fn eval(&mut self, env: &HashMap<String, Val>, n: usize) -> Val {
        match self {
            CNode::Const(c) => Val::F(vec![*c; n]),
            CNode::BoolConst(b) => Val::B(vec![*b; n]),
            CNode::Stream(name) => env
                .get(name)
                .unwrap_or_else(|| panic!("stream {name:?} not bound"))
                .clone(),
            CNode::Reward(field) => env
                .get(&format!("reward.{field}"))
                .unwrap_or_else(|| panic!("reward.{field} not bound"))
                .clone(),
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
                Val::F(
                    x.iter()
                        .map(|v| 1.0 / (1.0 + (-*steepness * (v - *midpoint)).exp()))
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
    Pct(Percentile),
}

impl InhibitThreshold {
    /// Threshold values for this chunk, given the metric chunk.
    fn eval(&mut self, metric: &[f64]) -> Vec<f64> {
        match self {
            InhibitThreshold::Const(v) => vec![*v; metric.len()],
            InhibitThreshold::Pct(p) => p.step(metric),
        }
    }
}

/// One compiled inhibit: its `metric` expression node, the threshold derived
/// from the metric chunk, and the output-gate state. `flag` inhibits set
/// `gate = None` so they contribute nothing to `muted` (telemetry-only,
/// matching `FlagAction`).
struct CompiledInhibit {
    metric: CNode,
    threshold: InhibitThreshold,
    gate: Option<InhibitGate>,
}

// --- Evaluator ------------------------------------------------------------

pub struct Evaluator {
    inputs: Vec<(String, Montage)>, // (bare input name, montage)
    derives: Vec<(String, CNode)>,
    thresholds: Vec<(String, CNode)>,
    inhibits: Vec<CompiledInhibit>,
    reward_continuous: Option<CNode>,
    reward_event: Option<(Dwell, CNode)>,
    outputs: Vec<(String, CNode)>,
    sample_rate_hz: f64,
    state: State,
    samples_pushed: usize,
    warmup_samples: usize,
}

impl Evaluator {
    pub fn new(p: &Protocol, sample_rate_hz: f64, channels: &[String]) -> Self {
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
        let derives = order_derives(p, sample_rate_hz);

        let thresholds = p
            .thresholds
            .iter()
            .map(|(name, t)| (name.clone(), build_threshold(t, sample_rate_hz)))
            .collect();

        // Inhibits (BTreeMap order, matching the Python evaluator's dict
        // iteration / `_compute_muted` OR-fold — order is immaterial for OR
        // but kept deterministic). Each pairs a metric node with a threshold
        // node (built via the SHARED `build_threshold_call`, with the metric
        // as the threshold's tracked signal) and an action gate.
        let inhibits = p
            .inhibits
            .values()
            .map(|ih| build_inhibit(ih, sample_rate_hz))
            .collect();

        let (mut reward_continuous, mut reward_event) = (None, None);
        if let Some(r) = &p.reward {
            reward_continuous = r.continuous.as_ref().map(|e| build_node(e, sample_rate_hz));
            reward_event = r.event.as_ref().map(|e| build_dwell(e, sample_rate_hz));
        }

        let outputs = p
            .output
            .iter()
            .map(|(ch, e)| (ch.clone(), build_node(e, sample_rate_hz)))
            .collect();

        let warmup_samples = compute_warmup_samples(p, sample_rate_hz);

        Evaluator {
            inputs,
            derives,
            thresholds,
            inhibits,
            reward_continuous,
            reward_event,
            outputs,
            sample_rate_hz,
            state: State::Ready,
            samples_pushed: 0,
            warmup_samples,
        }
    }

    /// `eval_.Evaluator.start`: enter `warmup` (or directly `run` if the
    /// protocol has no warmup-muted phase). `skip_warmup` jumps straight to
    /// `run`. Call before the first event-emitting chunk.
    pub fn start(&mut self, skip_warmup: bool) {
        self.samples_pushed = 0;
        self.state = if skip_warmup || self.warmup_samples == 0 {
            State::Run
        } else {
            State::Warmup
        };
    }

    /// `eval_.Evaluator.stop`: end the session.
    pub fn stop(&mut self) {
        self.state = State::Stopped;
    }

    pub fn state(&self) -> State {
        self.state
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
        for (name, montage) in self.inputs.iter() {
            env.insert(name.clone(), Val::F(montage.run(chunk)));
        }

        for (name, node) in self.derives.iter_mut() {
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
        }
        for (name, node) in self.thresholds.iter_mut() {
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
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
            // flag actions (`gate == None`) contribute nothing to `muted`.
            if let Some(gate) = ih.gate.as_mut() {
                let g = gate.gate(&active);
                for i in 0..n {
                    muted[i] |= g[i];
                }
            }
        }

        if let Some(node) = self.reward_continuous.as_mut() {
            let v = node.eval(&env, n);
            env.insert("reward.continuous".to_string(), v);
        }
        if let Some((dwell, cond)) = self.reward_event.as_mut() {
            let c = cond.eval(&env, n);
            let (events, holds) = dwell.step(c.as_b());
            env.insert("reward.event".to_string(), Val::B(events));
            env.insert("reward.event.holds".to_string(), Val::B(holds));
        }

        let mut outs: Vec<(String, Val)> = Vec::new();
        for (ch, node) in self.outputs.iter_mut() {
            let v = node.eval(&env, n);
            outs.push((ch.clone(), v));
        }
        (env, muted, outs)
    }

    /// Process one chunk and return `{stream: values}`, matching the Python
    /// evaluator's `last_streams()` contract. Output channels are coerced to
    /// floats (booleans → 0/1, floats clamped to [0, 1]); the streams API is
    /// stateless w.r.t. the warmup lifecycle, mirroring tap capture which
    /// runs in every state.
    pub fn step_chunk(&mut self, chunk: &[Vec<f64>]) -> BTreeMap<String, Vec<f64>> {
        let (env, muted, outs) = self.eval_chunk(chunk);

        let mut result: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        for (k, v) in env {
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

    /// Process one chunk and emit feedback `Event`s, mirroring
    /// `eval_.Evaluator.step_chunk` / `_process_chunk`. The same per-chunk
    /// computation runs in every state (so primitive state stays current),
    /// but during `warmup` events are suppressed. Advances `samples_pushed`
    /// and transitions `warmup → run` once the warmup window is satisfied.
    pub fn step_chunk_events(&mut self, chunk: &[Vec<f64>]) -> Vec<Event> {
        if self.state == State::Ready {
            self.start(false);
        }
        if self.state == State::Stopped {
            panic!("step_chunk_events called after stop()");
        }

        let n = chunk.len();
        let t0_s = self.samples_pushed as f64 / self.sample_rate_hz;
        let suppress_output = self.state == State::Warmup;

        // `muted` is the combined inhibit gate (`_compute_muted`), computed in
        // `eval_chunk`. Inhibit state advances even during warmup; only event
        // emission below is warmup-suppressed.
        let (_env, muted, outs) = self.eval_chunk(chunk);

        let mut events: Vec<Event> = Vec::new();
        if suppress_output {
            // Still advance the cursor / transition, but emit nothing.
            self.advance(n);
            return events;
        }

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

    /// Advance the sample cursor and transition `warmup → run` once enough
    /// samples have been pushed (mirrors the tail of `step_chunk`).
    fn advance(&mut self, n: usize) {
        self.samples_pushed += n;
        if self.state == State::Warmup && self.samples_pushed >= self.warmup_samples {
            self.state = State::Run;
        }
    }
}

/// `eval_.Evaluator._compute_warmup_samples`: if the first session phase is
/// `output_muted`, its duration becomes the warmup window; otherwise 0.
fn compute_warmup_samples(p: &Protocol, sample_rate_hz: f64) -> usize {
    let Some(session) = p.session.as_ref() else {
        return 0;
    };
    let Some(first) = session.phases.first() else {
        return 0;
    };
    if !first.output_muted {
        return 0;
    }
    (first.duration_ms / 1000.0 * sample_rate_hz).round() as usize
}

/// Compile every derive into a `(bare_name, CNode)` list ordered so each
/// derive's upstreams are bound before it runs. Follows the resolver's
/// `topological_order` (entries like `"derive/<name>"`); derives absent from
/// the topo list are appended in BTreeMap order as a fallback.
fn order_derives(p: &Protocol, sample_rate_hz: f64) -> Vec<(String, CNode)> {
    let mut emitted: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut out: Vec<(String, CNode)> = Vec::new();
    for entry in &p.topological_order {
        let name = bare(entry);
        if let Some(d) = p.derives.get(&name) {
            if emitted.insert(name.clone()) {
                out.push((name.clone(), build_node(&d.expression, sample_rate_hz)));
            }
        }
    }
    for (name, d) in p.derives.iter() {
        if emitted.insert(name.clone()) {
            out.push((name.clone(), build_node(&d.expression, sample_rate_hz)));
        }
    }
    out
}

// --- Compilation helpers --------------------------------------------------

fn bare(canonical: &str) -> String {
    canonical.split_once('/').map(|(_, n)| n).unwrap_or(canonical).to_string()
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
        _ => panic!("PoC supports only referential and bipolar montages"),
    }
}

fn build_stage(callee: &str, args: &[crate::ir::Arg], coeffs: Option<&Coeffs>) -> Box<dyn Stage> {
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
        "smooth" => Box::new(Smooth::new(need().alpha.expect("alpha"))),
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
        other => panic!("PoC: unsupported DSP primitive {other:?}"),
    }
}

fn build_node(e: &Expr, sample_rate_hz: f64) -> CNode {
    match e {
        Expr::Number { value } => CNode::Const(*value),
        Expr::Bool { value } => CNode::BoolConst(*value),
        Expr::StreamRef { target } => CNode::Stream(bare(target)),
        Expr::ThresholdRef { target } => CNode::Stream(bare(target)),
        Expr::RewardField { field_path } => CNode::Reward(field_path.clone()),
        Expr::Binop { op, left, right } => CNode::Binop(
            op.clone(),
            Box::new(build_node(left, sample_rate_hz)),
            Box::new(build_node(right, sample_rate_hz)),
        ),
        Expr::Conditional { cond, then, els } => CNode::Cond(
            Box::new(build_node(cond, sample_rate_hz)),
            Box::new(build_node(then, sample_rate_hz)),
            Box::new(build_node(els, sample_rate_hz)),
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
                stages.push(build_stage(callee, args, coeffs.as_ref()));
                cur = dsp_input(callee, args);
            }
            let _ = (args, coeffs);
            stages.reverse();
            CNode::Pipeline(stages, Box::new(build_node(cur, sample_rate_hz)))
        }
        Expr::Call { callee, args, coeffs } => {
            build_compute_call(callee, args, coeffs.as_ref(), sample_rate_hz)
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
fn coherence_input(args: &[crate::ir::Arg], name: &str, pos: usize, sample_rate_hz: f64) -> CNode {
    let expr = args
        .iter()
        .find(|a| a.name.as_deref() == Some(name))
        .map(|a| &a.value)
        .unwrap_or_else(|| positional(args, pos));
    build_node(expr, sample_rate_hz)
}

fn build_compute_call(
    callee: &str,
    args: &[crate::ir::Arg],
    coeffs: Option<&Coeffs>,
    sample_rate_hz: f64,
) -> CNode {
    match callee {
        "above" => CNode::Above(
            Box::new(build_node(positional(args, 0), sample_rate_hz)),
            Box::new(build_node(positional(args, 1), sample_rate_hz)),
        ),
        "below" => CNode::Below(
            Box::new(build_node(positional(args, 0), sample_rate_hz)),
            Box::new(build_node(positional(args, 1), sample_rate_hz)),
        ),
        "inside" => CNode::Inside {
            input: Box::new(build_node(positional(args, 0), sample_rate_hz)),
            low: num_named(args, "low").expect("inside needs `low`"),
            high: num_named(args, "high").expect("inside needs `high`"),
        },
        "all_of" | "any_of" => {
            let arr = positional(args, 0);
            let elements = match arr {
                Expr::Array { elements } => elements,
                _ => panic!("{callee}: expected an array of conditions"),
            };
            let nodes: Vec<CNode> =
                elements.iter().map(|e| build_node(e, sample_rate_hz)).collect();
            if callee == "all_of" {
                CNode::AllOf(nodes)
            } else {
                CNode::AnyOf(nodes)
            }
        }
        "sigmoid" => CNode::Sigmoid {
            midpoint: num_named(args, "midpoint").unwrap_or(0.0),
            steepness: num_named(args, "steepness").unwrap_or(1.0),
            input: Box::new(build_node(positional(args, 0), sample_rate_hz)),
        },
        "linear" => CNode::Linear {
            midpoint: num_named(args, "midpoint").unwrap_or(0.0),
            slope: num_named(args, "slope").unwrap_or(1.0),
            input: Box::new(build_node(positional(args, 0), sample_rate_hz)),
        },
        "coherence" => {
            let c = coeffs.unwrap_or_else(|| panic!("coherence: missing baked coeffs"));
            let nperseg = c.nperseg.expect("coherence: missing baked nperseg");
            let noverlap = c.noverlap.expect("coherence: missing baked noverlap");
            let window_samples =
                c.window_samples.expect("coherence: missing baked window_samples");
            let band = band_arg(args);
            CNode::Coherence {
                coh: Coherence::new(sample_rate_hz, nperseg, noverlap, window_samples, band),
                a: Box::new(coherence_input(args, "input_a", 0, sample_rate_hz)),
                b: Box::new(coherence_input(args, "input_b", 1, sample_rate_hz)),
            }
        }
        other => panic!("PoC: unsupported compute primitive {other:?}"),
    }
}

fn build_threshold(t: &crate::ir::Threshold, sample_rate_hz: f64) -> CNode {
    let signal = CNode::Stream(bare(&t.signal));
    build_threshold_call(&t.threshold_call, signal, sample_rate_hz)
}

/// Compile one `inhibit` block: a metric node, a threshold node (REUSES
/// `build_threshold_call`, feeding the metric as the threshold's tracked
/// signal — exactly as the Python evaluator does), and an action gate.
/// `flag` → `gate = None` (telemetry-only). `action_release_ms` defaults to
/// 200 ms when null (matching `_build_inhibit_actions`).
fn build_inhibit(ih: &crate::ir::Inhibit, sample_rate_hz: f64) -> CompiledInhibit {
    let metric = build_node(&ih.metric, sample_rate_hz);
    let threshold = build_inhibit_threshold(&ih.threshold);
    let release_ms = ih.action_release_ms.unwrap_or(200.0);
    let gate = match ih.action_kind.as_str() {
        "mute" | "freeze" => Some(InhibitGate::new(release_ms, sample_rate_hz)),
        "flag" => None,
        other => panic!("PoC: unsupported inhibit action {other:?}"),
    };
    CompiledInhibit { metric, threshold, gate }
}

/// Parse an inhibit's threshold-constructor call (`absolute(...)` /
/// `percentile(...)`) into an `InhibitThreshold` fed by the metric chunk.
/// Shares the exact constructor parsing (`absolute` value, `percentile`
/// target_pct + baked window_samples) with `build_threshold_call`.
fn build_inhibit_threshold(call: &Expr) -> InhibitThreshold {
    match call {
        Expr::Call { callee, args, coeffs } if callee == "percentile" => {
            let target_pct = num_named(args, "target_pct")
                .expect("percentile threshold needs a literal target_pct (control-ref is Phase-B)");
            let window_samples = coeffs
                .as_ref()
                .and_then(|c| c.window_samples)
                .expect("percentile: missing baked window_samples");
            InhibitThreshold::Pct(Percentile::new(target_pct, window_samples))
        }
        Expr::Call { callee, args, .. } if callee == "absolute" => {
            let v = absolute_value(args);
            InhibitThreshold::Const(v)
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
fn build_threshold_call(call: &Expr, signal: CNode, sample_rate_hz: f64) -> CNode {
    match call {
        Expr::Call { callee, args, coeffs } if callee == "percentile" => {
            let target_pct = num_named(args, "target_pct")
                .expect("percentile threshold needs a literal target_pct (control-ref is Phase-B)");
            let window_samples = coeffs
                .as_ref()
                .and_then(|c| c.window_samples)
                .expect("percentile: missing baked window_samples");
            let _ = sample_rate_hz;
            CNode::Pct(Percentile::new(target_pct, window_samples), Box::new(signal))
        }
        Expr::Call { callee, args, .. } if callee == "absolute" => {
            CNode::Const(absolute_value(args))
        }
        _ => panic!("PoC: unsupported threshold constructor"),
    }
}

/// `absolute(value)` — read the constant threshold value (named `value:` or
/// the first positional number). Shared by threshold blocks and inhibits.
fn absolute_value(args: &[crate::ir::Arg]) -> f64 {
    num_named(args, "value")
        .or_else(|| match positional(args, 0) {
            Expr::Number { value } => Some(*value),
            _ => None,
        })
        .expect("absolute: numeric value")
}

fn build_dwell(event_expr: &Expr, sample_rate_hz: f64) -> (Dwell, CNode) {
    match event_expr {
        Expr::Call { callee, args, coeffs } if callee == "dwell" => {
            let dwell_samples = coeffs
                .as_ref()
                .and_then(|c| c.dwell_samples)
                .expect("dwell: missing baked dwell_samples");
            let cond = args
                .iter()
                .find(|a| a.name.as_deref() == Some("condition"))
                .map(|a| build_node(&a.value, sample_rate_hz))
                .expect("dwell needs a `condition` arg");
            (Dwell::new(dwell_samples), cond)
        }
        _ => panic!("reward.event must be a dwell(...) call"),
    }
}
