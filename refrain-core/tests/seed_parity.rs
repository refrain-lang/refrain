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

#[test]
fn seed_stream_is_bit_exact_across_backends() {
    let p = load_ir("seed_smr_baseline");
    let io = load_io("seed_smr_baseline");
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
    eprintln!("seed_smr_baseline :: output/fb max|diff| = {max_abs:e}");
    // Sanity: the run-phase output must actually be the seeded 0.5 (not the 9.9-default
    // sigmoid), proving the seed fired.
    assert!(got.iter().any(|&x| (x - 0.5).abs() < 1e-9), "expected seeded 0.5 output");
}
