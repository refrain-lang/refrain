//! Tap-equivalence: the Rust core's `last_taps()` must reproduce the Python
//! evaluator's `Evaluator.last_taps()` / `_capture_taps` clinician-observation
//! snapshot per chunk, for every tap key, to machine precision.
//!
//! `equivalence.rs` pins the per-chunk *streams* and `events.rs` the feedback
//! events; this pins the host-facing tap snapshot — the canonical-prefixed
//! last-sample (or `any`) values a clinician observation window reads after
//! each chunk. The KEY SET must match exactly (a missing or extra tap key is a
//! failure) and every float value within 1e-6. All load the same IR / IO
//! fixtures emitted by `tools/gen_fixtures.py`.

use std::collections::{BTreeMap, BTreeSet};

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;
use serde::Deserialize;

#[derive(Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    input: Vec<Vec<f64>>,
}

fn load_ir(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_io(stem: &str) -> Io {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.io.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

/// Per-chunk reference taps: a list of `{key: float}` (bool already → 0.0/1.0).
fn load_taps(stem: &str) -> Vec<BTreeMap<String, f64>> {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.taps.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

/// Run the Rust core over the fixture signal (matching gen_fixtures.py's
/// `start(skip_warmup=True)` + per-chunk loop), calling `last_taps()` after
/// each chunk, then assert every key/value matches the Python reference.
fn run_taps(stem: &str) {
    let p = load_ir(stem);
    let io = load_io(stem);
    let want = load_taps(stem);

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    ev.start(true); // skip_warmup=True, same as gen_fixtures.py

    // Empty before the first chunk (mirrors Python's empty `_last_taps`).
    assert!(ev.last_taps().is_empty(), "{stem}: last_taps must be empty before first chunk");

    let chunks: Vec<&[Vec<f64>]> = io.input.chunks(io.chunk_size).collect();
    assert_eq!(
        chunks.len(),
        want.len(),
        "{stem}: chunk count mismatch (got {}, want {})",
        chunks.len(),
        want.len()
    );

    let mut max_diff = 0.0_f64;
    for (ci, (chunk, w)) in chunks.iter().zip(&want).enumerate() {
        ev.step_chunk_events(chunk);
        let got = ev.last_taps();

        // Key sets must match exactly — a missing or extra tap is a failure.
        let got_keys: BTreeSet<&String> = got.keys().collect();
        let want_keys: BTreeSet<&String> = w.keys().collect();
        assert_eq!(
            got_keys, want_keys,
            "{stem}: chunk {ci} tap key-set mismatch\n  missing (in want, not got): {:?}\n  extra   (in got, not want): {:?}",
            want_keys.difference(&got_keys).collect::<Vec<_>>(),
            got_keys.difference(&want_keys).collect::<Vec<_>>()
        );

        for (k, &wv) in w {
            let gv = got[k];
            let d = (gv - wv).abs();
            assert!(
                d <= 1e-6,
                "{stem}: chunk {ci} tap {k:?} diff {d:e} (got {gv}, want {wv})"
            );
            max_diff = max_diff.max(d);
        }
    }

    let keyset: Vec<&String> = want.last().map(|m| m.keys().collect()).unwrap_or_default();
    eprintln!(
        "  {stem} :: {} chunks; {} tap keys matched exactly; max|value diff| = {max_diff:e}\n    keys = {keyset:?}",
        want.len(),
        keyset.len(),
    );
}

#[test]
fn realistic_smr_taps_equivalent() {
    // input/derive/threshold/reward.continuous/reward.event/reward.event.holds/
    // reward.condition[i]/output taps (no inhibits → `muted` always false).
    run_taps("realistic_smr");
}

#[test]
fn micro_09_inhibit_taps_equivalent() {
    // Adds inhibit/<name> and the combined `muted` gate to the input/derive/
    // reward.continuous/output taps.
    run_taps("micro_09_inhibit");
}
