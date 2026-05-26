//! The Rust core consumes the IR-JSON wire format emitted by the Python
//! front-end. This test pins that it deserializes a real emitted fixture.

use refrain_core::ir::Protocol;

fn load(stem: &str) -> Protocol {
    let path = format!("tests/fixtures/{stem}.ir.json");
    let s = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {path}: {e}"));
    serde_json::from_str(&s).unwrap_or_else(|e| panic!("parse {path}: {e}"))
}

#[test]
fn deserializes_micro_03_ir_json() {
    let p = load("micro_03_envelope");
    assert_eq!(p.sample_rate_hz, 256.0);
    // `channels` is the protocol's *required* channels (a constraint), not
    // the host's runtime acquisition layout (which carries reference
    // electrodes). The runtime layout is supplied separately, like the rate.
    assert_eq!(p.channels, vec!["Cz"]);
    assert!(p.derives.contains_key("smr_envelope"));
    assert!(p.inputs.contains_key("raw"));
}

#[test]
fn deserializes_v02_reward_with_components() {
    // Minimal v0.2 reward block (the shape `_emit_reward` produces): a
    // weighted combine + a reward component + a suppress component with a
    // control-ref weight. v0.1 docs (no combine/components) still deserialize
    // because both fields are `#[serde(default)]`.
    let json = r#"{
        "refrain_ir_version": "0.2",
        "sample_rate_hz": 256.0,
        "channels": ["Cz"],
        "inputs": {},
        "derives": {},
        "output": {},
        "topological_order": [],
        "reward": {
            "continuous": {"node": "reward_field", "field_path": "composite", "stream_type": {}},
            "event": null,
            "combine": "weighted",
            "components": [
                {"name": "smr", "canonical_name": "reward/smr", "role": "reward",
                 "signal": {"node": "call", "callee": "sigmoid", "args": []},
                 "weight": {"node": "control_ref", "target": "control/w_smr", "default": 1.0}},
                {"name": "theta", "canonical_name": "reward/theta", "role": "suppress",
                 "signal": {"node": "call", "callee": "sigmoid", "args": []},
                 "weight": null}
            ]
        }
    }"#;
    let p: Protocol = serde_json::from_str(json).unwrap();
    let r = p.reward.expect("reward present");
    assert_eq!(r.combine, "weighted");
    assert_eq!(r.components.len(), 2);
    assert_eq!(r.components[0].name, "smr");
    assert_eq!(r.components[0].role, "reward");
    assert!(r.components[0].weight.is_some());
    assert_eq!(r.components[1].role, "suppress");
    assert!(r.components[1].weight.is_none());
}

#[test]
fn v01_reward_defaults_combine_all_no_components() {
    // A v0.1 reward (continuous/event only) must still deserialize: combine
    // defaults to "all", components to empty.
    let json = r#"{
        "sample_rate_hz": 256.0, "channels": ["Cz"], "inputs": {}, "derives": {},
        "output": {}, "topological_order": [],
        "reward": {"continuous": null, "event": null}
    }"#;
    let p: Protocol = serde_json::from_str(json).unwrap();
    let r = p.reward.unwrap();
    assert_eq!(r.combine, "all");
    assert!(r.components.is_empty());
}
