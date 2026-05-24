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
