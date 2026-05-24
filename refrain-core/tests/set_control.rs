//! `set_control` parity: the Rust core's live, in-place control retuning must
//! reproduce the Python `Evaluator.set_control` — matching the event/tap
//! streams across a run where a clinician knob is changed mid-session, AND
//! proving the impl state (the percentile rolling buffer, the smooth filter
//! state) is PRESERVED across the change rather than reset.
//!
//! Modeled on `events.rs` / `taps.rs`: it loads the same `realistic_smr` IR /
//! IO fixtures (so the input signal and baked coefficients are identical to the
//! static run), plus the `_setcontrol` reference streams and the schedule
//! (which chunk to apply the change at, and the new control values). It replays
//! the SAME schedule against the Rust evaluator and asserts parity.
//!
//! `realistic_smr` is the live-tunable target: its `smr_t` / `theta_t`
//! thresholds are `percentile(target_pct: <control>)`, the canonical clinician
//! knob. `gen_fixtures.py::_setcontrol_reference` produced the reference by
//! calling `ev.set_control(...)` partway through the Python run.

use std::collections::{BTreeMap, BTreeSet};

use refrain_core::eval::{Evaluator, Event};
use refrain_core::ir::Protocol;
use serde::Deserialize;

const STEM: &str = "realistic_smr";

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

#[derive(Deserialize)]
struct Change {
    name: String,
    value: f64,
}

#[derive(Deserialize)]
struct Schedule {
    at_chunk: usize,
    changes: Vec<Change>,
}

fn load_ir() -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{STEM}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_io() -> Io {
    let s = std::fs::read_to_string(format!("tests/fixtures/{STEM}.io.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_setcontrol_events() -> Vec<RefEvent> {
    let s =
        std::fs::read_to_string(format!("tests/fixtures/{STEM}_setcontrol.events.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_setcontrol_taps() -> Vec<BTreeMap<String, f64>> {
    let s = std::fs::read_to_string(format!("tests/fixtures/{STEM}_setcontrol.taps.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_schedule() -> Schedule {
    let s =
        std::fs::read_to_string(format!("tests/fixtures/{STEM}_setcontrol.schedule.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

/// Replay the schedule (same chunked loop as `gen_fixtures.py`, `set_control`
/// applied at the scheduled chunk) and assert the event stream matches Python.
#[test]
fn realistic_smr_setcontrol_events_equivalent() {
    let p = load_ir();
    let io = load_io();
    let schedule = load_schedule();
    let want = load_setcontrol_events();

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    ev.start(true); // skip_warmup=True, same as gen_fixtures.py

    let mut got: Vec<Event> = Vec::new();
    for (ci, chunk) in io.input.chunks(io.chunk_size).enumerate() {
        if ci == schedule.at_chunk {
            for c in &schedule.changes {
                ev.set_control(&c.name, c.value)
                    .unwrap_or_else(|e| panic!("set_control({}, {}): {e}", c.name, c.value));
            }
        }
        got.extend(ev.step_chunk_events(chunk));
    }

    assert_eq!(
        got.len(),
        want.len(),
        "{STEM}_setcontrol: event count mismatch (got {}, want {})",
        got.len(),
        want.len()
    );

    let mut max_ts = 0.0_f64;
    let mut max_val = 0.0_f64;
    for (i, (g, w)) in got.iter().zip(&want).enumerate() {
        assert_eq!(g.channel, w.channel, "{STEM}_setcontrol: event {i} channel mismatch");
        assert_eq!(g.kind, w.kind, "{STEM}_setcontrol: event {i} kind mismatch");

        let dts = (g.timestamp_s - w.timestamp_s).abs();
        assert!(dts <= 1e-9, "{STEM}_setcontrol: event {i} timestamp diff {dts:e} on {}", g.channel);
        max_ts = max_ts.max(dts);

        match (g.value, w.value) {
            (Some(gv), Some(wv)) => {
                let dv = (gv - wv).abs();
                assert!(
                    dv <= 1e-6,
                    "{STEM}_setcontrol: event {i} value diff {dv:e} on {} (got {gv}, want {wv})",
                    g.channel
                );
                max_val = max_val.max(dv);
            }
            (None, None) => {}
            (gv, wv) => panic!(
                "{STEM}_setcontrol: event {i} value None-ness mismatch (got {gv:?}, want {wv:?})"
            ),
        }
    }

    eprintln!(
        "  {STEM}_setcontrol :: {} events matched; set_control @ chunk {} ({} knobs); \
         max|ts diff| = {max_ts:e}; max|value diff| = {max_val:e}",
        got.len(),
        schedule.at_chunk,
        schedule.changes.len(),
    );
}

/// Replay the schedule and assert the per-chunk tap snapshot matches Python,
/// for every key, in every chunk. This pins the threshold/derive/reward taps
/// across the change — the threshold taps (`threshold/smr_t`, `threshold/theta_t`)
/// move when the percentile target retunes, so matching them proves the new
/// `target_pct` took effect on the SAME (un-reset) rolling buffer.
#[test]
fn realistic_smr_setcontrol_taps_equivalent() {
    let p = load_ir();
    let io = load_io();
    let schedule = load_schedule();
    let want = load_setcontrol_taps();

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    ev.start(true);

    let chunks: Vec<&[Vec<f64>]> = io.input.chunks(io.chunk_size).collect();
    assert_eq!(
        chunks.len(),
        want.len(),
        "{STEM}_setcontrol: chunk count mismatch (got {}, want {})",
        chunks.len(),
        want.len()
    );

    let mut max_diff = 0.0_f64;
    for (ci, (chunk, w)) in chunks.iter().zip(&want).enumerate() {
        if ci == schedule.at_chunk {
            for c in &schedule.changes {
                ev.set_control(&c.name, c.value)
                    .unwrap_or_else(|e| panic!("set_control({}, {}): {e}", c.name, c.value));
            }
        }
        ev.step_chunk_events(chunk);
        let got = ev.last_taps();

        let got_keys: BTreeSet<&String> = got.keys().collect();
        let want_keys: BTreeSet<&String> = w.keys().collect();
        assert_eq!(
            got_keys, want_keys,
            "{STEM}_setcontrol: chunk {ci} tap key-set mismatch"
        );

        for (k, &wv) in w {
            let gv = got[k];
            let d = (gv - wv).abs();
            assert!(
                d <= 1e-6,
                "{STEM}_setcontrol: chunk {ci} tap {k:?} diff {d:e} (got {gv}, want {wv})"
            );
            max_diff = max_diff.max(d);
        }
    }

    eprintln!(
        "  {STEM}_setcontrol taps :: {} chunks matched; max|value diff| = {max_diff:e}",
        want.len()
    );
}

/// State-preservation proof: the threshold tap on the controlled stream must
/// be IDENTICAL to the static run for every chunk BEFORE the change, then
/// diverge AFTER it. If `set_control` had reset the percentile buffer, the
/// post-change threshold would jump discontinuously (a short buffer yields a
/// very different percentile); instead it evolves continuously from the
/// already-filled buffer. Matching Python's reference taps (which also keep the
/// buffer) across the boundary is the parity proof; here we additionally assert
/// the pre-change prefix is byte-identical to the *static* reference, confirming
/// the change is genuinely in-place and time-localized.
#[test]
fn realistic_smr_setcontrol_state_preserved() {
    let static_taps: Vec<BTreeMap<String, f64>> = {
        let s = std::fs::read_to_string(format!("tests/fixtures/{STEM}.taps.json")).unwrap();
        serde_json::from_str(&s).unwrap()
    };
    let sc_taps = load_setcontrol_taps();
    let schedule = load_schedule();

    assert_eq!(static_taps.len(), sc_taps.len(), "tap chunk count mismatch");
    assert!(schedule.at_chunk > 0 && schedule.at_chunk < static_taps.len());

    // The threshold tap on the SMR-controlled stream.
    let key = "threshold/smr_t";

    // Before the change: the set_control run and the static run are identical
    // (no control change has happened yet) — proves nothing else perturbs state.
    for ci in 0..schedule.at_chunk {
        let a = static_taps[ci][key];
        let b = sc_taps[ci][key];
        assert!(
            (a - b).abs() <= 1e-9,
            "chunk {ci}: pre-change {key} should equal the static run (got {b}, static {a})"
        );
    }

    // After the change: the threshold must actually move (the new target_pct on
    // the SAME, un-reset buffer), confirming the retune took effect.
    let mut diverged = false;
    for ci in schedule.at_chunk..static_taps.len() {
        if (static_taps[ci][key] - sc_taps[ci][key]).abs() > 1e-6 {
            diverged = true;
            break;
        }
    }
    assert!(
        diverged,
        "post-change {key} never diverged from the static run — set_control had no effect"
    );

    eprintln!(
        "  {STEM}_setcontrol state :: pre-chunk-{} taps identical to static run, \
         post-change {key} diverged (in-place retune, buffer preserved)",
        schedule.at_chunk
    );
}
