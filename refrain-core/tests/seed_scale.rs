//! A seed rule's `scale` multiplier, against the committed golden vector.
//!
//! `seed_scale_emg` is the worked per-person muscle limit: a muscle envelope
//! feeding nothing but an artifact gate, a control seeded from that envelope
//! at three times its warm-up median, and the gate's limit read off that
//! control. The multiplier is one number, and the only way it can be wrong
//! without anything else looking wrong is for one engine to apply it and the
//! other not to — so this compares every stream the Python reference recorded,
//! not just the output.
//!
//! Driven with `step_chunk_events` rather than the streams-only `step_chunk`
//! for the same reason `seed_parity.rs` is: the streams-only path neither
//! auto-starts nor advances the phase cursor, so the warm-up->run transition
//! that fires the seed never happens through it.

use std::collections::BTreeMap;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

const STEM: &str = "seed_scale_emg";

#[derive(serde::Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

fn load_ir() -> Protocol {
    serde_json::from_str(
        &std::fs::read_to_string(format!("tests/fixtures/{STEM}.ir.json")).unwrap(),
    )
    .unwrap()
}

fn load_io() -> Io {
    serde_json::from_str(
        &std::fs::read_to_string(format!("tests/fixtures/{STEM}.io.json")).unwrap(),
    )
    .unwrap()
}

#[test]
fn the_wire_carries_the_scale_and_the_version_that_protects_it() {
    let p = load_ir();
    assert_eq!(p.refrain_ir_version.as_deref(), Some("0.6"));
    let seed = p.controls["emg_limit_uv"].seed.as_ref().expect("seeded control");
    assert_eq!(seed.scale, 3.0);
    assert_eq!(seed.from, "derive/emg_envelope");
    // The gate reads its limit off the seeded control, which is what makes the
    // multiplier reach the session rather than only the report.
    assert_eq!(p.inhibits["emg"].kind.as_deref(), Some("artifact"));
}

#[test]
fn an_absent_scale_deserializes_as_one_not_as_zero() {
    // serde's own default for f64 is 0.0, which would seed every control in
    // every pre-0.6 document at zero — silently, since the key is absent by
    // design. `seed_smr_baseline` is one such document.
    let p: Protocol = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/seed_smr_baseline.ir.json").unwrap(),
    )
    .unwrap();
    assert_eq!(p.controls["thr_uv"].seed.as_ref().unwrap().scale, 1.0);
}

#[test]
fn seeded_and_scaled_streams_are_bit_exact_across_backends() {
    let p = load_ir();
    let io = load_io();
    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    let mut got: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for chunk in io.input.chunks(io.chunk_size) {
        ev.step_chunk_events(chunk);
        for (key, values) in ev.last_streams() {
            got.entry(key.clone()).or_default().extend(values.iter().copied());
        }
    }
    // The seeded value is checked BEFORE the per-stream comparison below, and
    // the order is load-bearing. The seeded limit feeds the artifact gate, so
    // any change to it moves `output/fb`; if the stream loop ran first it
    // would panic on a sample index and these assertions would never execute,
    // leaving the claim they make untested while the test still went red for
    // a different reason.
    // The seeded limit must be the measured median times three, and it must be
    // three times a median *amplitude*. Those are two different claims and
    // each needs its own assertion, because the first one alone cannot see the
    // bug this fixture exists to prevent: `value == 3 * median` is invariant
    // under squaring the whole signal, so a protocol that lost its `sqrt()`
    // stage would satisfy it exactly while gating at 1.73 times the median
    // amplitude rather than three times it. The golden number below is what
    // pins the domain.
    let report = ev.seed_report();
    let entry = &report["emg_limit_uv"];
    assert_eq!(entry.status, "seeded");
    assert_eq!(entry.scale, 3.0);
    let seeded = entry.value.expect("a seeded control has a value");

    // Claim 1: three times the median, recomputed independently.
    //
    // The seed's buffer is a 2 s trailing window, so at the warm-up->run edge
    // it holds the last 512 of the 768 warm-up samples. The median is taken
    // from the PYTHON REFERENCE stream rather than from the engine's own
    // percentile buffer, so this is an independent number and not the engine
    // agreeing with itself. For an even-length sample numpy's 50th percentile
    // interpolates between the two middle order statistics, which is their
    // mean; on this fixture the two spellings agree bit for bit.
    let env = io.streams.get("emg_envelope").expect("reference emg_envelope");
    let warmup = 3 * io.sample_rate_hz as usize; // the 3 s warm-up phase
    let window = 2 * io.sample_rate_hz as usize; // the seed's 2 s window
    let mut buffered: Vec<f64> = env[warmup - window..warmup].to_vec();
    assert!(buffered.iter().all(|v| v.is_finite()), "ingest skips non-finite");
    buffered.sort_by(|a, b| a.partial_cmp(b).expect("finite"));
    let median = (buffered[window / 2 - 1] + buffered[window / 2]) / 2.0;
    assert!(
        (seeded - 3.0 * median).abs() < 1e-9,
        "seeded {seeded} is not three times the warm-up median {median}"
    );

    // Claim 2: and that median is an amplitude in uV, not a band power in uV2.
    //
    // 18.7496 is three times the 6.2499 uV median of this fixture's warm-up.
    // Drop the `sqrt()` from the protocol and the same arithmetic lands on
    // 117.2 — three times the median POWER, which is the same gate as 1.73
    // times the median amplitude. A reader who writes `scale = 3.0` after
    // reading SPEC §4.9.4 is promised the former.
    assert!(
        (seeded - 18.749_592_701_504_177).abs() < 1e-9,
        "seeded {seeded} is not the golden uV limit — has the units domain moved?"
    );

    for (key, want) in &io.streams {
        let mine = got.get(key).unwrap_or_else(|| panic!("missing stream {key}"));
        assert_eq!(mine.len(), want.len(), "stream {key}: length mismatch");
        for (i, (g, w)) in mine.iter().zip(want).enumerate() {
            assert!(
                (g - w).abs() < 1e-9,
                "{key} @ sample {i}: rust {g} vs python {w}"
            );
        }
    }

    let muted: usize = io.streams["output/fb"]
        .iter()
        .skip(3 * io.sample_rate_hz as usize) // the warm-up is muted by its phase
        .filter(|&&v| v == 0.0)
        .count();
    assert!(muted > 0, "the artifact gate never tripped");
    assert!(
        muted < io.streams["output/fb"].len() - 3 * io.sample_rate_hz as usize,
        "the artifact gate never cleared"
    );
}
