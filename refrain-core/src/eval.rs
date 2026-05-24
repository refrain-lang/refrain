//! Chunk-driven evaluator: compiles the baked IR-JSON into a stateful tree
//! and runs it per chunk, reproducing the Python evaluator's `last_streams()`
//! output. Filter design lives in Python; every rate-dependent coefficient is
//! baked into the IR, so the runtime only needs the channel layout.
//!
//! PoC scope: the micro_03/04/05 corpus — referential montage, the envelope
//! DSP pipeline, windowed-percentile / absolute thresholds, above/below/
//! all_of conditions, the dwell event machine, the sigmoid mapping, and the
//! `/` binop. (Control-ref args, the conditional output, and coherence are
//! Phase-B.)

use std::collections::{BTreeMap, HashMap};

use crate::dsp::{Biquad, Dwell, HilbertFir, Magnitude, Percentile, Signal, Smooth, Stage};
use crate::ir::{Coeffs, Expr, Protocol};

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

struct Montage {
    active_idx: usize,
    ref_indices: Option<Vec<usize>>, // None => common-average over all channels
}

impl Montage {
    fn referential(active: &str, reference: &str, channels: &[String]) -> Self {
        let active_idx = channels
            .iter()
            .position(|c| c == active)
            .unwrap_or_else(|| panic!("referential: active {active:?} not in source"));
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
        Montage { active_idx, ref_indices }
    }

    fn run(&self, chunk: &[Vec<f64>]) -> Vec<f64> {
        chunk
            .iter()
            .map(|row| {
                let active = row[self.active_idx];
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
    Above(Box<CNode>, Box<CNode>),
    Below(Box<CNode>, Box<CNode>),
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
            CNode::Above(l, r) => {
                let (a, b) = (l.eval(env, n), r.eval(env, n));
                Val::B(a.as_f().iter().zip(b.as_f()).map(|(x, y)| x > y).collect())
            }
            CNode::Below(l, r) => {
                let (a, b) = (l.eval(env, n), r.eval(env, n));
                Val::B(a.as_f().iter().zip(b.as_f()).map(|(x, y)| x < y).collect())
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

// --- Evaluator ------------------------------------------------------------

pub struct Evaluator {
    montage: Montage,
    derives: Vec<(String, CNode)>,
    thresholds: Vec<(String, CNode)>,
    reward_continuous: Option<CNode>,
    reward_event: Option<(Dwell, CNode)>,
    outputs: Vec<(String, CNode)>,
}

impl Evaluator {
    pub fn new(p: &Protocol, _sample_rate_hz: f64, channels: &[String]) -> Self {
        let inp = p.inputs.get("raw").expect("PoC expects an input named 'raw'");
        let montage = build_montage(&inp.montage, channels);

        let derives = p
            .derives
            .iter()
            .map(|(name, d)| (name.clone(), build_node(&d.expression)))
            .collect();

        let thresholds = p
            .thresholds
            .iter()
            .map(|(name, t)| (name.clone(), build_threshold(t)))
            .collect();

        let (mut reward_continuous, mut reward_event) = (None, None);
        if let Some(r) = &p.reward {
            reward_continuous = r.continuous.as_ref().map(build_node);
            reward_event = r.event.as_ref().map(build_dwell);
        }

        let outputs = p
            .output
            .iter()
            .map(|(ch, e)| (ch.clone(), build_node(e)))
            .collect();

        Evaluator { montage, derives, thresholds, reward_continuous, reward_event, outputs }
    }

    pub fn step_chunk(&mut self, chunk: &[Vec<f64>]) -> BTreeMap<String, Vec<f64>> {
        let n = chunk.len();
        let mut env: HashMap<String, Val> = HashMap::new();
        env.insert("raw".to_string(), Val::F(self.montage.run(chunk)));

        for (name, node) in self.derives.iter_mut() {
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
        }
        for (name, node) in self.thresholds.iter_mut() {
            let v = node.eval(&env, n);
            env.insert(name.clone(), v);
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

        let mut outs: Vec<(String, Vec<f64>)> = Vec::new();
        for (ch, node) in self.outputs.iter_mut() {
            let v = node.eval(&env, n);
            let f = match v {
                Val::B(b) => b.iter().map(|&x| if x { 1.0 } else { 0.0 }).collect(),
                Val::F(mut f) => {
                    for x in f.iter_mut() {
                        *x = x.clamp(0.0, 1.0); // value channels clamp to [0, 1]
                    }
                    f
                }
            };
            outs.push((format!("output/{ch}"), f));
        }

        let mut result: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        for (k, v) in env {
            result.insert(k, v.into_f());
        }
        for (k, f) in outs {
            result.insert(k, f);
        }
        result
    }
}

// --- Compilation helpers --------------------------------------------------

fn bare(canonical: &str) -> String {
    canonical.split_once('/').map(|(_, n)| n).unwrap_or(canonical).to_string()
}

fn is_dsp(callee: &str) -> bool {
    matches!(
        callee,
        "bandpass" | "hilbert" | "magnitude" | "rectify" | "smooth" | "differentiate" | "bandpower"
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

fn build_montage(expr: &Expr, channels: &[String]) -> Montage {
    match expr {
        Expr::Call { callee, args, .. } if callee == "referential" => Montage::referential(
            string_arg(args, "active").expect("referential needs `active`"),
            string_arg(args, "reference").expect("referential needs `reference`"),
            channels,
        ),
        _ => panic!("PoC supports only a referential montage"),
    }
}

fn build_stage(callee: &str, coeffs: Option<&Coeffs>) -> Box<dyn Stage> {
    let need = || coeffs.unwrap_or_else(|| panic!("{callee}: missing baked coeffs"));
    match callee {
        "bandpass" | "bandpower" => Box::new(Biquad::new(need().sos.as_ref().expect("sos"))),
        "hilbert" => {
            let c = need();
            Box::new(HilbertFir::new(
                c.fir_taps.as_ref().expect("fir_taps"),
                c.group_delay.expect("group_delay"),
            ))
        }
        "magnitude" | "rectify" => Box::new(Magnitude),
        "smooth" => Box::new(Smooth::new(need().alpha.expect("alpha"))),
        other => panic!("PoC: unsupported DSP primitive {other:?}"),
    }
}

fn build_node(e: &Expr) -> CNode {
    match e {
        Expr::Number { value } => CNode::Const(*value),
        Expr::Bool { value } => CNode::BoolConst(*value),
        Expr::StreamRef { target } => CNode::Stream(bare(target)),
        Expr::ThresholdRef { target } => CNode::Stream(bare(target)),
        Expr::RewardField { field_path } => CNode::Reward(field_path.clone()),
        Expr::Binop { op, left, right } => {
            CNode::Binop(op.clone(), Box::new(build_node(left)), Box::new(build_node(right)))
        }
        Expr::Conditional { cond, then, els } => CNode::Cond(
            Box::new(build_node(cond)),
            Box::new(build_node(then)),
            Box::new(build_node(els)),
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
                stages.push(build_stage(callee, coeffs.as_ref()));
                cur = positional(args, 0);
            }
            let _ = (args, coeffs);
            stages.reverse();
            CNode::Pipeline(stages, Box::new(build_node(cur)))
        }
        Expr::Call { callee, args, .. } => build_compute_call(callee, args),
        _ => panic!("unexpected node in expression"),
    }
}

fn build_compute_call(callee: &str, args: &[crate::ir::Arg]) -> CNode {
    match callee {
        "above" => CNode::Above(
            Box::new(build_node(positional(args, 0))),
            Box::new(build_node(positional(args, 1))),
        ),
        "below" => CNode::Below(
            Box::new(build_node(positional(args, 0))),
            Box::new(build_node(positional(args, 1))),
        ),
        "all_of" | "any_of" => {
            let arr = positional(args, 0);
            let elements = match arr {
                Expr::Array { elements } => elements,
                _ => panic!("{callee}: expected an array of conditions"),
            };
            let nodes: Vec<CNode> = elements.iter().map(build_node).collect();
            if callee == "all_of" {
                CNode::AllOf(nodes)
            } else {
                CNode::AnyOf(nodes)
            }
        }
        "sigmoid" => CNode::Sigmoid {
            midpoint: num_named(args, "midpoint").unwrap_or(0.0),
            steepness: num_named(args, "steepness").unwrap_or(1.0),
            input: Box::new(build_node(positional(args, 0))),
        },
        "linear" => CNode::Linear {
            midpoint: num_named(args, "midpoint").unwrap_or(0.0),
            slope: num_named(args, "slope").unwrap_or(1.0),
            input: Box::new(build_node(positional(args, 0))),
        },
        other => panic!("PoC: unsupported compute primitive {other:?}"),
    }
}

fn build_threshold(t: &crate::ir::Threshold) -> CNode {
    let signal = CNode::Stream(bare(&t.signal));
    match &t.threshold_call {
        Expr::Call { callee, args, coeffs } if callee == "percentile" => {
            let target_pct = num_named(args, "target_pct")
                .expect("percentile threshold needs a literal target_pct (control-ref is Phase-B)");
            let window_samples = coeffs
                .as_ref()
                .and_then(|c| c.window_samples)
                .expect("percentile: missing baked window_samples");
            CNode::Pct(Percentile::new(target_pct, window_samples), Box::new(signal))
        }
        Expr::Call { callee, args, .. } if callee == "absolute" => {
            // absolute(value) — constant threshold.
            let v = num_named(args, "value")
                .or_else(|| match positional(args, 0) {
                    Expr::Number { value } => Some(*value),
                    _ => None,
                })
                .expect("absolute: numeric value");
            CNode::Const(v)
        }
        _ => panic!("PoC: unsupported threshold constructor"),
    }
}

fn build_dwell(event_expr: &Expr) -> (Dwell, CNode) {
    match event_expr {
        Expr::Call { callee, args, coeffs } if callee == "dwell" => {
            let dwell_samples = coeffs
                .as_ref()
                .and_then(|c| c.dwell_samples)
                .expect("dwell: missing baked dwell_samples");
            let cond = args
                .iter()
                .find(|a| a.name.as_deref() == Some("condition"))
                .map(|a| build_node(&a.value))
                .expect("dwell needs a `condition` arg");
            (Dwell::new(dwell_samples), cond)
        }
        _ => panic!("reward.event must be a dwell(...) call"),
    }
}
