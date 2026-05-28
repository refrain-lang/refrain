//! Regression: `absolute(value: <control_ref>)` must be accepted by the Rust
//! core (parity with the Python evaluator), and `set_control` must retune the
//! constant threshold value live.
//!
//! Before the fix, `absolute_value()` only matched `Expr::Number` for the
//! `value:` arg and fell through to `positional(args, 0)`, which panicked at
//! `eval.rs:1153` because the only arg in the call was named. After the fix,
//! a control-ref for `value:` registers a binding so `set_control` retargets
//! the threshold's constant cell in place.
//!
//! Threshold blocks AND inhibits parse the same `absolute(...)` constructor
//! via `build_threshold_call` / `build_inhibit_threshold`; both shapes are
//! covered.

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

/// A minimal protocol with a single `absolute(value: control/threshold_uv)`
/// threshold over a device-referential input. The threshold stream's
/// per-sample value is the constant the test asserts against.
const PROTOCOL_JSON: &str = r#"{
    "sample_rate_hz": 256.0,
    "channels": ["Cz"],
    "inputs": {
        "raw": {
            "canonical_name": "input/raw",
            "montage": {
                "node": "call",
                "callee": "referential",
                "args": [
                    {"name": "active",    "value": {"node": "string", "value": "Cz"}},
                    {"name": "reference", "value": {"node": "string", "value": "device"}}
                ]
            }
        }
    },
    "derives": {},
    "thresholds": {
        "smr_t": {
            "canonical_name": "threshold/smr_t",
            "signal": "raw",
            "threshold_call": {
                "node": "call",
                "callee": "absolute",
                "args": [
                    {"name": "value", "value": {
                        "node": "control_ref",
                        "target": "control/threshold_uv",
                        "default": 4.0
                    }}
                ]
            }
        }
    },
    "controls": {
        "threshold_uv": {"canonical_name": "control/threshold_uv"}
    },
    "output": {},
    "topological_order": []
}"#;

fn build() -> (Evaluator, Vec<Vec<f64>>) {
    let p: Protocol = serde_json::from_str(PROTOCOL_JSON).expect("parse PROTOCOL_JSON");
    let channels = vec!["Cz".to_string()];
    let mut ev = Evaluator::new(&p, p.sample_rate_hz, &channels);
    ev.start(true); // skip_warmup
    // Two sample chunk of zeros; the threshold stream is constant regardless.
    let chunk: Vec<Vec<f64>> = vec![vec![0.0]; 2];
    (ev, chunk)
}

/// Bug 1: Rust core must accept `absolute(value: <control_ref>)` without
/// panicking at construction.
#[test]
fn absolute_value_control_ref_construction_does_not_panic() {
    let (_ev, _chunk) = build();
}

/// Default control value (4.0) is observed on the threshold stream before any
/// `set_control` call — proves the control_ref's baked default is read.
#[test]
fn absolute_value_control_ref_default_observed() {
    let (mut ev, chunk) = build();
    let streams = ev.step_chunk(&chunk);
    let smr_t = streams.get("smr_t").expect("threshold stream `smr_t` present");
    assert!(smr_t.iter().all(|&v| (v - 4.0).abs() < 1e-12),
        "threshold default not 4.0: {smr_t:?}");
}

/// After `set_control`, the next chunk's threshold stream reflects the new
/// constant — the fix wires the ControlCell through.
#[test]
fn absolute_value_set_control_retunes_threshold() {
    let (mut ev, chunk) = build();
    let _ = ev.step_chunk(&chunk);
    ev.set_control("threshold_uv", 5.5)
        .expect("set_control should succeed for declared control");
    let streams = ev.step_chunk(&chunk);
    let smr_t = streams.get("smr_t").expect("threshold stream `smr_t` present");
    assert!(smr_t.iter().all(|&v| (v - 5.5).abs() < 1e-12),
        "threshold did not retune to 5.5: {smr_t:?}");
}
