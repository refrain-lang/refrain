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
    /// Leading samples whose *values* neither engine can be held to, for the
    /// reason `equivalence.rs` already skips them: the filters are still
    /// establishing themselves, so a derive's value there is the filter's own
    /// settling rather than a measurement. Since the filters warm-start (#93)
    /// a bandpass fed a standing level reads its steady-state zero from
    /// sample one — correct, and numerically degenerate: the first samples of
    /// an envelope are rounding dust around 1e-23, and `micro_05_reward`
    /// divides one such dust value by another (`sigmoid("env" / "t")`). The
    /// ratio of two numbers that are both zero-to-rounding is whatever the
    /// platform's last bits say, so it differs between SciPy's `sosfilt` and
    /// this crate's recurrence, and between macOS and Linux. Event identity
    /// — count, channel, kind, timestamp — is still pinned everywhere.
    ///
    /// Absent from the authored-pulse fixtures (`output_dwell_*`), which run
    /// no filter at all, so zero is the right default for them.
    #[serde(default)]
    warmup_samples: usize,
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

    let warmup_s = io.warmup_samples as f64 / io.sample_rate_hz;
    let mut max_ts = 0.0_f64;
    let mut max_val = 0.0_f64;
    let mut skipped = 0_usize;
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
            // Inside the warmup window a value is the filters' own settling,
            // not a measurement — see `Io::warmup_samples`. Identity above is
            // still asserted; only the number is let go.
            (Some(_), Some(_)) if g.timestamp_s < warmup_s => skipped += 1,
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
    assert!(
        skipped < got.len(),
        "{stem}: every event fell inside the {warmup_s} s warmup window, so no \
         value was actually compared"
    );

    // Report the per-channel/kind breakdown so the harness output is legible.
    let mut breakdown: BTreeMap<(String, String), usize> = BTreeMap::new();
    for g in &got {
        *breakdown.entry((g.channel.clone(), g.kind.clone())).or_default() += 1;
    }
    eprintln!(
        "  {stem} :: {} events matched; breakdown = {breakdown:?}; \
         max|ts diff| = {max_ts:e}; max|value diff| = {max_val:e} \
         ({skipped} value(s) inside the {warmup_s} s warmup window not compared)",
        got.len()
    );
}

#[test]
fn micro_05_reward_events_equivalent() {
    run_events("micro_05_reward");
}

#[test]
fn direct_output_dwell_events_equivalent() {
    // Authored pulse stream: one cue per hold, then another after a break.
    run_events("output_dwell_event");
}

#[test]
fn rearmed_output_dwell_events_equivalent() {
    // A four-sample break cannot rearm a five-sample requirement.
    run_events("output_dwell_rearm");
}

#[test]
fn rearmed_output_dwell_resets_after_gap() {
    let protocol = load_ir("output_dwell_rearm");
    let channels = vec!["Cz".to_string(), "linked_ears".to_string()];
    let mut evaluator = Evaluator::new(&protocol, 100.0, &channels);
    evaluator.start(true);
    assert!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 2]).is_empty());
    evaluator.reset_held_outputs();
    assert!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 2]).is_empty());
    assert_eq!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]]).len(), 1);
}

#[test]
fn rearmed_output_dwell_duration_changes_live() {
    let protocol = load_ir("output_dwell_rearm_tunable");
    let channels = vec!["Cz".to_string(), "linked_ears".to_string()];
    let mut evaluator = Evaluator::new(&protocol, 100.0, &channels);
    evaluator.start(true);
    assert_eq!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 3]).len(), 1);
    evaluator.set_control("cue_dwell", 50.0).unwrap();
    assert!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 4]).is_empty());
    assert_eq!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]]).len(), 1);
}

#[test]
fn direct_output_dwell_resets_after_gap() {
    let protocol = load_ir("output_dwell_event");
    let channels = vec!["Cz".to_string(), "linked_ears".to_string()];
    let mut evaluator = Evaluator::new(&protocol, 100.0, &channels);
    evaluator.start(true);
    assert!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 2]).is_empty());
    evaluator.reset_held_outputs();
    assert!(evaluator.step_chunk_events(&vec![vec![2.0, 0.0]; 2]).is_empty());
    let events = evaluator.step_chunk_events(&vec![vec![2.0, 0.0]]);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].channel, "cue");
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
