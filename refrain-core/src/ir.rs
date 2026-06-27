//! Serde structs mirroring the IR-JSON wire format (`refrain.ir_json`).
//!
//! Only the fields the runtime needs are declared; serde ignores the rest
//! (source locations, dimensions, CRED-nf metadata, ...). Expression nodes
//! are an internally-tagged enum keyed by the `"node"` discriminator.

use std::collections::BTreeMap;

use indexmap::IndexMap;
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
    pub inhibits: BTreeMap<String, Inhibit>,
    #[serde(default)]
    pub reward: Option<Reward>,
    /// Output channels in IR declaration order. Serde deserializes JSON
    /// object keys in their wire order; IndexMap preserves that order so
    /// `eval_chunk` emits events in the same sequence as the Python evaluator.
    #[serde(default)]
    pub output: IndexMap<String, Expr>,
    #[serde(default)]
    pub session: Option<Session>,
    /// Named activation blocks (staged protocols), keyed by block name.
    #[serde(default)]
    pub blocks: BTreeMap<String, Block>,
    /// Named, block-selectable reward bundles, keyed by bundle name. Each is a
    /// `Reward` shape (continuous/event); the active block selects which drives
    /// `reward.continuous`/`reward.event`.
    #[serde(default)]
    pub reward_bundles: BTreeMap<String, Reward>,
    #[serde(default)]
    pub topological_order: Vec<String>,
    /// Declared clinician controls, keyed by bare name (`smr_target_pct`).
    /// Only the canonical name is read; the runtime uses the key set to mirror
    /// the Python `set_control`'s "is this a declared control?" check (the live
    /// value/default lives inline as a `control_ref` at each use site).
    #[serde(default)]
    pub controls: BTreeMap<String, ControlDecl>,
}

/// A `controls.<name>` declaration. Only the canonical name is needed by the
/// runtime; the rest of the block (default/range/label) is authoring metadata
/// serde ignores.
#[derive(Debug, Deserialize)]
pub struct ControlDecl {
    pub canonical_name: String,
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
    #[serde(default)]
    pub duration_ms: f64,
    #[serde(default)]
    pub output_muted: bool,
    /// Transition rule: "timed" (auto-advance after duration), "open" (no
    /// clock; host advances), or "timed_with_floor" (auto-advance unless held).
    #[serde(default = "default_phase_mode")]
    pub mode: String,
    /// Name of the block active during this phase, or None (warmup/rest).
    #[serde(default)]
    pub block: Option<String>,
}

fn default_phase_mode() -> String {
    "timed".to_string()
}

/// A named activation set referenced by `phase.block` (`_emit_block`). Selects
/// which named reward bundle / output channels / inhibits are live during a
/// phase. `thresholds` is declarative (telemetry/validation). Empty `output`
/// ⇒ all channels live; empty `inhibits` ⇒ all inhibits gate.
#[derive(Debug, Deserialize)]
pub struct Block {
    pub name: String,
    #[serde(default)]
    pub thresholds: Vec<String>,
    #[serde(default)]
    pub reward: Option<String>,
    #[serde(default)]
    pub output: Vec<String>,
    #[serde(default)]
    pub inhibits: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct Threshold {
    /// Canonical prefixed name (e.g. `threshold/smr_t`) — used as the tap key.
    pub canonical_name: String,
    pub signal: String, // canonical name of the source stream
    pub threshold_call: Expr,
}

/// `inhibit` block (`_emit_inhibit`): a `metric` expression (typically a
/// `bandpower(...)` call) compared `>` a `threshold` constructor call
/// (`absolute(...)`/`percentile(...)`, the same shape as a threshold block's
/// `threshold_call`). `action_kind` selects the output-gate behaviour;
/// `action_release_ms` is the hangover (null → 200 ms default).
#[derive(Debug, Deserialize)]
pub struct Inhibit {
    /// Canonical prefixed name (e.g. `inhibit/emg`) — used as the tap key.
    pub canonical_name: String,
    pub metric: Expr,
    pub threshold: Expr,
    pub action_kind: String,
    #[serde(default)]
    pub action_release_ms: Option<f64>,
}

/// `reward { continuous?, event?, combine?, components? }`. For a v0.1
/// single-reward protocol `combine` is absent (defaults to "all") and
/// `components` is empty — byte-identical runtime to before. A v0.2
/// weighted composite carries one `RewardComponent` per named reward/suppress
/// block and `combine == "weighted"`.
#[derive(Debug, Deserialize)]
pub struct Reward {
    #[serde(default)]
    pub continuous: Option<Expr>,
    #[serde(default)]
    pub event: Option<Expr>,
    #[serde(default = "default_combine")]
    pub combine: String,
    #[serde(default)]
    pub components: Vec<RewardComponent>,
}

fn default_combine() -> String {
    "all".to_string()
}

/// A named reward/suppress component of a v0.2 weighted composite
/// (`_emit_reward`'s `components[]` entries). `role` is "reward" (contributes
/// `signal`) or "suppress" (contributes `1 - signal`). `weight` is a
/// `control_ref` or `number` Expr; `None` ⇒ implicit weight 1.0. `canonical_name`
/// is in the wire JSON but the runtime keys taps/streams on `name`, so serde
/// ignores it (additionalProperties).
#[derive(Debug, Deserialize)]
pub struct RewardComponent {
    pub name: String,
    pub role: String,
    pub signal: Expr,
    #[serde(default)]
    pub weight: Option<Expr>,
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
    #[serde(default)]
    pub lag_samples: Option<usize>,
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
    ControlRef {
        /// Canonical control target (e.g. `control/smr_target_pct`). Preserves
        /// the binding so `set_control` can route a live change to the impl
        /// this ref feeds.
        target: String,
        /// Resolved default value (the value the runtime uses until a
        /// `set_control` arrives). Equals what the emitter would previously
        /// have baked as a literal `number` node, so default behaviour is
        /// unchanged.
        default: f64,
    },
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
