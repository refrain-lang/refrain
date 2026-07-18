//! Baseline-seeding parity: run the `skip_warmup=False` seeding fixture on
//! the Rust core and compare its `output/fb` stream to the Python reference
//! bundle, bit-exact.
//!
//! Every other fixture in the corpus is generated with `skip_warmup=True`
//! (`docs/CONFORMANCE.md` §3) — the baseline-seeding feature (§2.6) fires
//! only during warmup, so it needs its own `skip_warmup=False` bundle.
//!
//! Driving requirement: the streams-only `Evaluator::step_chunk` (used by
//! `equivalence.rs`) does NOT advance the phase cursor or auto-start, so the
//! warmup->run transition that fires the seed never happens through it. This
//! test MUST drive with `step_chunk_events`, which auto-starts with
//! `skip_warmup=false` and advances the cursor, then read the cached streams
//! via `last_streams()`.

use std::collections::BTreeMap;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

#[derive(serde::Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

fn load_ir(stem: &str) -> Protocol {
    serde_json::from_str(&std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap()).unwrap()
}
fn load_io(stem: &str) -> Io {
    serde_json::from_str(&std::fs::read_to_string(format!("tests/fixtures/{stem}.io.json")).unwrap()).unwrap()
}

/// Drive `stem`'s fixture through the Rust core with `step_chunk_events`
/// (the lifecycle-aware path that fires the seed at the warmup->run edge)
/// and assert its `output/fb` stream matches the Python reference bit-exact
/// (1e-9), returning the max|diff| for the caller to report.
fn run_seed_parity(stem: &str) -> f64 {
    let p = load_ir(stem);
    let io = load_io(stem);
    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    // step_chunk_events auto-starts skip_warmup=false + advances, so the seed
    // fires at the warmup->run edge; last_streams() surfaces the cached streams.
    let mut got: Vec<f64> = Vec::new();
    for chunk in io.input.chunks(io.chunk_size) {
        ev.step_chunk_events(chunk);
        if let Some(v) = ev.last_streams().get("output/fb") {
            got.extend(v.iter().copied());
        }
    }
    let want = io.streams.get("output/fb").expect("reference output/fb");
    assert_eq!(got.len(), want.len(), "stream length mismatch");
    // Constant-fill percentile is exact across backends -> pin at 1e-9.
    let mut max_abs = 0.0_f64;
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        let d = (g - w).abs();
        assert!(d < 1e-9, "seed parity @ sample {i}: rust {g} vs python {w}");
        max_abs = max_abs.max(d);
    }
    eprintln!("{stem} :: output/fb max|diff| = {max_abs:e}");
    // Sanity: the run-phase output must actually be the seeded 0.5 (not the 9.9-default
    // sigmoid), proving the seed fired.
    assert!(got.iter().any(|&x| (x - 0.5).abs() < 1e-9), "expected seeded 0.5 output");
    max_abs
}

#[test]
fn seed_stream_is_bit_exact_across_backends() {
    run_seed_parity("seed_smr_baseline");
}

/// Same seeding conformance shape as `seed_smr_baseline`, but the seeded
/// control is consumed in EXPRESSION position (`"env" / thr_uv` as a bare
/// operand) rather than through an impl parameter slot. Locks the
/// fire-chunk freshness fix (Python must read the just-seeded value the
/// same chunk it fires, matching Rust's shared `ConstCell`) across both
/// backends.
#[test]
fn seed_exprpos_stream_is_bit_exact_across_backends() {
    run_seed_parity("seed_exprpos");
}
