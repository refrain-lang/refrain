//! Staged-protocol runtime: the N-phase cursor, mid-session output muting,
//! reward-bundle selection, and the host transport surface (advance_phase /
//! hold / set_clock_frozen / current_phase). Uses a minimal protocol that
//! needs no baked DSP coeffs (referential montage + absolute threshold +
//! sigmoid), so the IR-JSON can be hand-written. The Python<->Rust numeric
//! parity on a full staged protocol is covered by the Python parity suite.

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

/// Two-block staged protocol: warm (muted) -> b1 (beta_up, timed_with_floor)
/// -> rest (open, muted). `audio = reward.continuous`, driven by bundle `br`
/// = sigmoid(raw - t) with t = 0, so a constant raw=1.0 gives audio ~0.73.
const STAGED: &str = r#"{
  "refrain_ir_version": "0.2",
  "sample_rate_hz": 256.0,
  "channels": ["Cz"],
  "inputs": { "raw": {
    "canonical_name": "input/raw",
    "montage": { "node": "call", "callee": "referential", "args": [
      { "name": "active", "value": { "node": "string", "value": "Cz" } },
      { "name": "reference", "value": { "node": "string", "value": "device" } }
    ] }
  }},
  "derives": {},
  "thresholds": { "t": {
    "canonical_name": "threshold/t",
    "signal": "input/raw",
    "threshold_call": { "node": "call", "callee": "absolute",
      "args": [ { "name": "value", "value": { "node": "number", "value": 0.0 } } ] }
  }},
  "inhibits": {},
  "reward": null,
  "output": { "audio": { "node": "reward_field", "field_path": "continuous" } },
  "controls": {},
  "blocks": { "beta_up": {
    "name": "beta_up", "thresholds": ["t"], "reward": "br",
    "output": ["audio"], "inhibits": []
  }},
  "reward_bundles": { "br": {
    "continuous": { "node": "call", "callee": "sigmoid", "args": [
      { "value": { "node": "binop", "op": "-",
        "left": { "node": "stream_ref", "target": "input/raw" },
        "right": { "node": "threshold_ref", "target": "threshold/t" } } },
      { "name": "midpoint", "value": { "node": "number", "value": 0.0 } },
      { "name": "steepness", "value": { "node": "number", "value": 1.0 } }
    ] },
    "event": null, "combine": "all", "components": []
  }},
  "session": { "phases": [
    { "name": "warm", "duration_ms": 1000.0, "output_muted": true },
    { "name": "b1", "duration_ms": 2000.0, "output_muted": false,
      "mode": "timed_with_floor", "block": "beta_up" },
    { "name": "rest", "duration_ms": 0.0, "output_muted": true, "mode": "open" }
  ]},
  "topological_order": ["input/raw", "threshold/t"]
}"#;

const SR: f64 = 256.0;

fn ev() -> Evaluator {
    let p: Protocol = serde_json::from_str(STAGED).expect("parse staged IR");
    let mut e = Evaluator::new(&p, SR, &["Cz".to_string()]);
    e.start(false);
    e
}

fn feed(e: &mut Evaluator, seconds: f64) {
    let total = (seconds * SR) as usize;
    let mut pushed = 0;
    while pushed < total {
        let n = (total - pushed).min(64);
        let chunk: Vec<Vec<f64>> = vec![vec![1.0]; n];
        e.step_chunk_events(&chunk);
        pushed += n;
    }
}

#[test]
fn cursor_sequences_and_mutes_midsession() {
    use refrain_core::eval::State;
    let mut e = ev();
    assert_eq!(e.current_phase().name.as_deref(), Some("warm"));
    assert_eq!(e.state(), State::Warmup);

    feed(&mut e, 1.0); // warm completes -> b1
    assert_eq!(e.state(), State::Run);

    feed(&mut e, 0.5); // processing b1 (beta_up active)
    assert_eq!(e.current_phase().name.as_deref(), Some("b1"));
    assert!(e.last_taps()["output/audio"] > 0.0); // active block emits

    feed(&mut e, 2.0); // b1's 2 s floor elapses -> rest (open, muted)
    feed(&mut e, 0.25); // process a rest chunk
    assert_eq!(e.current_phase().name.as_deref(), Some("rest"));
    assert_eq!(e.last_taps()["output/audio"], 0.0); // mid-session rest mutes
}

#[test]
fn open_phase_needs_advance_then_stops() {
    use refrain_core::eval::State;
    let mut e = ev();
    feed(&mut e, 1.0); // -> b1
    feed(&mut e, 2.0); // -> rest (open)
    feed(&mut e, 5.0); // open never auto-advances
    assert_eq!(e.current_phase().name.as_deref(), Some("rest"));
    assert!(e.advance_phase()); // rest is last -> stopped
    assert_eq!(e.state(), State::Stopped);
    assert!(!e.advance_phase()); // no-op past the end
}

#[test]
fn clock_freeze_pauses_countdown() {
    let mut e = ev();
    feed(&mut e, 1.0); // -> b1
    feed(&mut e, 1.0); // 1 s into b1 (of 2 s)
    e.set_clock_frozen(true);
    feed(&mut e, 5.0); // frozen: must NOT advance
    assert_eq!(e.current_phase().name.as_deref(), Some("b1"));
    assert!(e.advance_phase()); // Next works while frozen
    assert_eq!(e.current_phase().name.as_deref(), Some("rest"));
}

#[test]
fn hold_extends_timed_with_floor() {
    let mut e = ev();
    feed(&mut e, 1.0); // -> b1
    assert!(e.hold(true)); // b1 is timed_with_floor
    feed(&mut e, 5.0); // would auto-advance at 2 s, but held
    assert_eq!(e.current_phase().name.as_deref(), Some("b1"));
    assert!(e.hold(false)); // re-arm
    // Past the floor, the next chunk advances out of b1; current_phase() is
    // aligned with the chunk just run, so feed enough to actually process a
    // rest chunk before asserting.
    feed(&mut e, 0.5);
    assert_eq!(e.current_phase().name.as_deref(), Some("rest"));
}

#[test]
fn phase_index_tap_present() {
    let mut e = ev();
    feed(&mut e, 0.5); // in warm (index 0)
    let taps = e.last_taps();
    assert_eq!(taps["phase/index"], 0.0);
    assert_eq!(taps["phase/output_muted"], 1.0);
}
