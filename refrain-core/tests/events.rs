//! Event-equivalence: the Rust core's `step_chunk_events` must reproduce the
//! Python evaluator's `list[Event]` feedback output to machine precision.
//!
//! The streams equivalence (`equivalence.rs`) pins the per-chunk *streams*;
//! this pins the streams → events emission stage and the warmup lifecycle.
//! Both load the same IR / IO fixtures emitted by `tools/gen_fixtures.py`.

use std::collections::BTreeMap;

use refrain_core::eval::{Evaluator, Event};
use refrain_core::ir::Protocol;
use serde::Deserialize;

#[derive(Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    input: Vec<Vec<f64>>,
}

#[derive(Deserialize)]
struct RefEvent {
    timestamp_s: f64,
    channel: String,
    kind: String,
    value: Option<f64>,
}

fn load_ir(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_io(stem: &str) -> Io {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.io.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_events(stem: &str) -> Vec<RefEvent> {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.events.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

/// Run the Rust core over the fixture signal (matching gen_fixtures.py's
/// `start(skip_warmup=True)` + per-chunk loop), concatenating every chunk's
/// events, then assert it matches the Python reference event list.
fn run_events(stem: &str) {
    let p = load_ir(stem);
    let io = load_io(stem);
    let want = load_events(stem);

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    ev.start(true); // skip_warmup=True, same as gen_fixtures.py

    let mut got: Vec<Event> = Vec::new();
    for chunk in io.input.chunks(io.chunk_size) {
        got.extend(ev.step_chunk_events(chunk));
    }

    assert_eq!(
        got.len(),
        want.len(),
        "{stem}: event count mismatch (got {}, want {})",
        got.len(),
        want.len()
    );

    let mut max_ts = 0.0_f64;
    let mut max_val = 0.0_f64;
    for (i, (g, w)) in got.iter().zip(&want).enumerate() {
        assert_eq!(g.channel, w.channel, "{stem}: event {i} channel mismatch");
        assert_eq!(g.kind, w.kind, "{stem}: event {i} kind mismatch");

        let dts = (g.timestamp_s - w.timestamp_s).abs();
        assert!(
            dts <= 1e-9,
            "{stem}: event {i} timestamp diff {dts:e} on {}",
            g.channel
        );
        max_ts = max_ts.max(dts);

        match (g.value, w.value) {
            (Some(gv), Some(wv)) => {
                let dv = (gv - wv).abs();
                assert!(
                    dv <= 1e-6,
                    "{stem}: event {i} value diff {dv:e} on {} (got {gv}, want {wv})",
                    g.channel
                );
                max_val = max_val.max(dv);
            }
            (None, None) => {}
            (gv, wv) => panic!("{stem}: event {i} value None-ness mismatch (got {gv:?}, want {wv:?})"),
        }
    }

    // Report the per-channel/kind breakdown so the harness output is legible.
    let mut breakdown: BTreeMap<(String, String), usize> = BTreeMap::new();
    for g in &got {
        *breakdown.entry((g.channel.clone(), g.kind.clone())).or_default() += 1;
    }
    eprintln!(
        "  {stem} :: {} events matched; breakdown = {breakdown:?}; \
         max|ts diff| = {max_ts:e}; max|value diff| = {max_val:e}",
        got.len()
    );
}

#[test]
fn micro_05_reward_events_equivalent() {
    run_events("micro_05_reward");
}

#[test]
fn micro_09_inhibit_events_equivalent() {
    // The `audio_gain` value events carry the per-chunk mean of the muted/
    // clamped continuous output; matching them pins the inhibit gate's effect
    // on the value-channel emission path.
    run_events("micro_09_inhibit");
}

#[test]
fn realistic_smr_events_equivalent() {
    run_events("realistic_smr");
}
