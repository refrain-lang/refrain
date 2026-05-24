//! Golden-vector equivalence: the Rust core, running coefficients baked by
//! the Python front-end, must reproduce the Python evaluator's output
//! streams within the same tolerance the bench harness uses
//! (atol=1e-6, rtol=1e-4, after a warmup window).

use std::collections::BTreeMap;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;
use serde::Deserialize;

#[derive(Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    warmup_samples: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

fn load_ir(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_io(stem: &str) -> Io {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.io.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

/// Mirrors `bench.harness.equivalence.assert_equivalent`: compare the
/// steady-state tail (after `warmup`) within tolerance.
fn check(name: &str, got: &[f64], want: &[f64], warmup: usize, atol: f64, rtol: f64) -> f64 {
    assert_eq!(got.len(), want.len(), "stream {name}: length mismatch");
    let mut max_abs = 0.0_f64;
    for i in warmup..got.len() {
        let d = (got[i] - want[i]).abs();
        assert!(
            d <= atol + rtol * want[i].abs(),
            "stream {name}: divergence at sample {i} (got {}, want {}); |diff|={d:e}",
            got[i], want[i]
        );
        max_abs = max_abs.max(d);
    }
    max_abs
}

fn run_protocol(stem: &str) {
    let p = load_ir(stem);
    let io = load_io(stem);

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    let mut out: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for chunk in io.input.chunks(io.chunk_size) {
        for (k, v) in ev.step_chunk(chunk) {
            out.entry(k).or_default().extend(v);
        }
    }

    // Compare every reference stream the Rust core also produces.
    let mut checked = 0;
    for (name, want) in &io.streams {
        if let Some(got) = out.get(name) {
            let max_abs = check(name, got, want, io.warmup_samples, 1e-6, 1e-4);
            eprintln!("  {stem} :: {name:<20} max|diff| = {max_abs:e}");
            checked += 1;
        }
    }
    assert!(checked >= 2, "{stem}: expected to check multiple streams, got {checked}");
}

#[test]
fn micro_01_passthrough_equivalent() {
    run_protocol("micro_01_passthrough");
}

#[test]
fn micro_02_bandpass_equivalent() {
    run_protocol("micro_02_bandpass");
}

#[test]
fn micro_03_envelope_equivalent() {
    run_protocol("micro_03_envelope");
}

#[test]
fn micro_04_threshold_equivalent() {
    run_protocol("micro_04_threshold");
}

#[test]
fn micro_05_reward_equivalent() {
    run_protocol("micro_05_reward");
}

#[test]
fn coherence_pair_equivalent() {
    run_protocol("micro_06_coherence");
}

#[test]
fn micro_07_ilf_equivalent() {
    // bipolar montage + differentiate + auto_range + inside reward sub-condition.
    run_protocol("micro_07_ilf");
}

#[test]
fn micro_08_bandpower_equivalent() {
    // bandpower derive (Biquad -> square -> rolling-mean) + auto_range + inside.
    run_protocol("micro_08_bandpower");
}

#[test]
fn realistic_smr_equivalent() {
    run_protocol("realistic_smr");
}
