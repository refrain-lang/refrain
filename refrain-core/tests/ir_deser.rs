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

#[test]
fn deserializes_phase_mode_block_and_staging() {
    // Staged-protocol IR-JSON: phases carry mode/block, plus top-level
    // `blocks` and `reward_bundles`. Older fixtures omit all of these and
    // still deserialize because every new field is `#[serde(default)]`.
    let json = r#"{
        "refrain_ir_version": "0.2",
        "sample_rate_hz": 256.0,
        "channels": ["Cz"],
        "inputs": {},
        "derives": {},
        "thresholds": {},
        "inhibits": {},
        "output": {},
        "controls": {},
        "topological_order": [],
        "session": { "phases": [
            { "name": "warm", "duration_ms": 1000.0, "output_muted": true },
            { "name": "b1", "duration_ms": 2000.0, "output_muted": false,
              "mode": "timed_with_floor", "block": "beta_up" },
            { "name": "rest", "duration_ms": 0.0, "output_muted": true, "mode": "open" }
        ]},
        "blocks": { "beta_up": {
            "name": "beta_up", "thresholds": ["bt"], "reward": "br",
            "output": ["audio"], "inhibits": []
        }},
        "reward_bundles": { "br": {
            "continuous": {"node": "number", "value": 0.5},
            "event": null, "combine": "all", "components": []
        }}
    }"#;
    let p: Protocol = serde_json::from_str(json).expect("parse staged IR");
    let s = p.session.as_ref().unwrap();
    assert_eq!(s.phases[0].mode, "timed"); // serde default
    assert_eq!(s.phases[0].block, None);
    assert_eq!(s.phases[1].mode, "timed_with_floor");
    assert_eq!(s.phases[1].block.as_deref(), Some("beta_up"));
    assert_eq!(s.phases[2].mode, "open");
    let b = p.blocks.get("beta_up").expect("beta_up block");
    assert_eq!(b.thresholds, vec!["bt"]);
    assert_eq!(b.reward.as_deref(), Some("br"));
    assert_eq!(b.output, vec!["audio"]);
    assert!(p.reward_bundles.contains_key("br"));
    assert!(p.reward_bundles["br"].continuous.is_some());
}

/// SPEC 9.3: a protocol whose schema is newer than this runtime supports must
/// be refused at load with a clear diagnostic — never silently ignored. An
/// unknown field is invisible to serde, so without this gate a newer protocol
/// runs with its new semantics dropped and nobody is told.
#[test]
fn refuses_ir_version_newer_than_supported() {
    let mut v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/micro_03_envelope.ir.json").unwrap(),
    )
    .unwrap();
    v["refrain_ir_version"] = serde_json::Value::String("99.0".into());

    let p: Protocol = serde_json::from_str(&v.to_string()).expect("still deserializes");
    let err = refrain_core::ir::check_ir_version(&p)
        .expect_err("must refuse IR newer than supported");
    assert!(
        err.contains("99.0") && err.contains("refrain_ir_version"),
        "diagnostic must name the offending version and the field, got: {err}"
    );
}

/// The versions this core claims to support must pass the gate. Guards against
/// it being over-eager.
#[test]
fn accepts_supported_ir_versions() {
    for stem in ["micro_03_envelope", "realistic_smr"] {
        let p = load(stem);
        let tag = p.refrain_ir_version.clone().unwrap_or_else(|| "0.1".into());
        assert!(
            refrain_core::ir::SUPPORTED_IR_VERSIONS.contains(&tag.as_str()),
            "{stem} is tagged {tag}, which this core does not claim to support"
        );
        refrain_core::ir::check_ir_version(&p)
            .unwrap_or_else(|e| panic!("{stem} ({tag}) must pass the gate: {e}"));
    }
}

/// A document with no version tag predates versioning; treat it as 0.1 and
/// accept it.
#[test]
fn accepts_ir_with_no_version_tag() {
    let mut v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/micro_03_envelope.ir.json").unwrap(),
    )
    .unwrap();
    v.as_object_mut().unwrap().remove("refrain_ir_version");

    let p: Protocol = serde_json::from_str(&v.to_string()).expect("untagged must deserialize");
    assert!(p.refrain_ir_version.is_none());
    refrain_core::ir::check_ir_version(&p).expect("untagged must pass the gate");
}
