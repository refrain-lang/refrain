//! v0.2 weighted-composite golden-vector parity: the Rust core, fed the v0.2
//! IR-JSON the Python emitter produces, must reproduce the Python evaluator's
//! `reward.composite` and gated output streams within the same tolerance the
//! equivalence harness uses (atol=1e-6, rtol=1e-4, after warmup). This is the
//! Stage-2 exit criterion: a weighted protocol runs identically on backend=rust.

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

fn check(name: &str, got: &[f64], want: &[f64], warmup: usize, atol: f64, rtol: f64) -> f64 {
    assert_eq!(got.len(), want.len(), "stream {name}: length mismatch");
    let mut max_abs = 0.0_f64;
    for i in warmup..got.len() {
        let d = (got[i] - want[i]).abs();
        assert!(
            d <= atol + rtol * want[i].abs(),
            "stream {name}: divergence at sample {i} (got {}, want {}); |diff|={d:e}",
            got[i],
            want[i]
        );
        max_abs = max_abs.max(d);
    }
    max_abs
}

#[test]
fn composite_smr_theta_equivalent() {
    let p = load_ir("composite_smr_theta");
    let io = load_io("composite_smr_theta");

    // The reward block must have deserialized as v0.2.
    {
        let r = p.reward.as_ref().expect("reward present");
        assert_eq!(r.combine, "weighted");
        assert_eq!(r.components.len(), 2);
    }

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    let mut out: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for chunk in io.input.chunks(io.chunk_size) {
        for (k, v) in ev.step_chunk(chunk) {
            out.entry(k).or_default().extend(v);
        }
    }

    // Must include the composite stream and the gated output channel.
    let mut checked = 0;
    let mut saw_composite = false;
    for (name, want) in &io.streams {
        if let Some(got) = out.get(name) {
            let max_abs = check(name, got, want, io.warmup_samples, 1e-6, 1e-4);
            eprintln!("  composite_smr_theta :: {name:<24} max|diff| = {max_abs:e}");
            if name == "reward.composite" {
                saw_composite = true;
            }
            checked += 1;
        }
    }
    assert!(saw_composite, "reward.composite stream not produced by the Rust core");
    assert!(checked >= 2, "expected to check multiple streams, got {checked}");
}
