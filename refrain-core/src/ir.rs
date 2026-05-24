//! Serde structs mirroring the IR-JSON wire format (`refrain.ir_json`).
//!
//! Only the fields the runtime needs are declared; serde ignores the rest
//! (source locations, dimensions, CRED-nf metadata, ...). Expression nodes
//! are an internally-tagged enum keyed by the `"node"` discriminator.

use std::collections::BTreeMap;

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct Protocol {
    pub sample_rate_hz: f64,
    pub channels: Vec<String>,
    pub inputs: BTreeMap<String, Input>,
    pub derives: BTreeMap<String, Derive>,
    #[serde(default)]
    pub thresholds: BTreeMap<String, Threshold>,
    #[serde(default)]
    pub reward: Option<Reward>,
    #[serde(default)]
    pub output: BTreeMap<String, Expr>,
    #[serde(default)]
    pub session: Option<Session>,
    #[serde(default)]
    pub topological_order: Vec<String>,
}

/// Session timeline (`_emit_session`): an ordered list of phases. The first
/// phase, if `output_muted`, defines the warmup window (see `Evaluator`).
#[derive(Debug, Deserialize)]
pub struct Session {
    #[serde(default)]
    pub phases: Vec<Phase>,
}

#[derive(Debug, Deserialize)]
pub struct Phase {
    pub name: String,
    pub duration_ms: f64,
    #[serde(default)]
    pub output_muted: bool,
}

#[derive(Debug, Deserialize)]
pub struct Threshold {
    pub signal: String, // canonical name of the source stream
    pub threshold_call: Expr,
}

#[derive(Debug, Deserialize)]
pub struct Reward {
    #[serde(default)]
    pub continuous: Option<Expr>,
    #[serde(default)]
    pub event: Option<Expr>,
}

#[derive(Debug, Deserialize)]
pub struct Input {
    pub canonical_name: String,
    pub montage: Expr,
}

#[derive(Debug, Deserialize)]
pub struct Derive {
    pub canonical_name: String,
    pub expression: Expr,
    #[serde(default)]
    pub upstream: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct Arg {
    pub name: Option<String>,
    pub value: Expr,
}

/// DSP coefficients baked by the Python front-end so the core never
/// reimplements SciPy filter *design*. Every field is optional; which ones
/// are present depends on the primitive.
#[derive(Debug, Default, Deserialize, Clone)]
pub struct Coeffs {
    #[serde(default)]
    pub sos: Option<Vec<Vec<f64>>>,
    #[serde(default)]
    pub fir_taps: Option<Vec<f64>>,
    #[serde(default)]
    pub group_delay: Option<usize>,
    #[serde(default)]
    pub alpha: Option<f64>,
    #[serde(default)]
    pub dt: Option<f64>,
    #[serde(default)]
    pub window_samples: Option<usize>,
    #[serde(default)]
    pub dwell_samples: Option<usize>,
    #[serde(default)]
    pub nperseg: Option<usize>,
    #[serde(default)]
    pub noverlap: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "node")]
pub enum Expr {
    #[serde(rename = "number")]
    Number { value: f64 },
    #[serde(rename = "string")]
    Str { value: String },
    #[serde(rename = "bool")]
    Bool { value: bool },
    #[serde(rename = "stream_ref")]
    StreamRef { target: String },
    #[serde(rename = "threshold_ref")]
    ThresholdRef { target: String },
    #[serde(rename = "control_ref")]
    ControlRef { target: String },
    #[serde(rename = "reward_field")]
    RewardField { field_path: String },
    #[serde(rename = "call")]
    Call {
        callee: String,
        #[serde(default)]
        args: Vec<Arg>,
        #[serde(default)]
        coeffs: Option<Coeffs>,
    },
    #[serde(rename = "array")]
    Array { elements: Vec<Expr> },
    #[serde(rename = "tuple")]
    Tuple { elements: Vec<Expr> },
    #[serde(rename = "binop")]
    Binop {
        op: String,
        left: Box<Expr>,
        right: Box<Expr>,
    },
    #[serde(rename = "conditional")]
    Conditional {
        cond: Box<Expr>,
        then: Box<Expr>,
        #[serde(rename = "else")]
        els: Box<Expr>,
    },
    #[serde(rename = "block")]
    Block {
        kind: Option<String>,
        fields: BTreeMap<String, Expr>,
    },
}
