//! Golden vector for a hold that forgives a short dip (IR-JSON v0.6).
//!
//! `tolerance` is per-sample carried state written twice — `DwellImpl.step` in
//! `src/refrain/primitive_impls.py` and `dsp::Dwell` here. The unit tests in
//! `dsp.rs` pin the rule on hand-built condition streams; this pins it on a
//! real protocol end to end, against the streams and events the Python
//! evaluator produced for the same input (`tools/gen_fixtures.py`).
//!
//! Streams are compared to 1e-9 rather than `equivalence.rs`'s 1e-6/1e-4: the
//! `holds`-gated `output/audio_gain` stream is a continuous value switched by
//! the dip counter, so a one-sample disagreement about forgiveness is a
//! whole-value difference and nothing here needs the looser band. Events are
//! compared exactly in count, channel, kind and order.

use std::collections::BTreeMap;

use refrain_core::eval::{Evaluator, Event};
use refrain_core::ir::Protocol;
use serde::Deserialize;

const STEM: &str = "micro_14_tolerance";

/// Absolute tolerance for a stream sample. Tight on purpose — see the module
/// comment. The FIR convolution behind `smr_envelope` agrees to roughly 1e-14
/// at these amplitudes, so this leaves three orders of magnitude of headroom
/// while still catching a single forgiven-or-not sample.
const ATOL: f64 = 1e-9;

#[derive(Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    warmup_samples: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

#[derive(Deserialize)]
struct RefEvent {
    timestamp_s: f64,
    channel: String,
    kind: String,
    value: Option<f64>,
}

fn load<T: serde::de::DeserializeOwned>(suffix: &str) -> T {
    let s = std::fs::read_to_string(format!("tests/fixtures/{STEM}.{suffix}.json"))
        .unwrap_or_else(|e| panic!("tests/fixtures/{STEM}.{suffix}.json: {e}"));
    serde_json::from_str(&s).unwrap()
}

/// One pass over the fixture signal, capturing both the per-chunk streams and
/// the events — the same single-pass shape `gen_fixtures.py::_reference` used
/// to produce the reference, so no DSP state is stepped twice.
fn run(p: &Protocol, io: &Io) -> (BTreeMap<String, Vec<f64>>, Vec<Event>) {
    let mut ev = Evaluator::new(p, io.sample_rate_hz, &io.channels);
    ev.start(true); // skip_warmup=True, same as gen_fixtures.py

    let mut streams: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut events: Vec<Event> = Vec::new();
    for chunk in io.input.chunks(io.chunk_size) {
        events.extend(ev.step_chunk_events(chunk));
        for (k, v) in ev.last_streams() {
            streams.entry(k).or_default().extend(v);
        }
    }
    (streams, events)
}

#[test]
fn micro_14_tolerance_is_tagged_v06_and_carries_a_baked_tolerance() {
    // The fixture's whole purpose is to be the v0.6 vector. If the emitter
    // ever stops tagging it, every assertion below would still pass while the
    // v0.6 schema and this crate's version gate went unexercised.
    let p: Protocol = load("ir");
    assert_eq!(p.refrain_ir_version.as_deref(), Some("0.6"));
    refrain_core::ir::check_ir_version(&p).unwrap();
}

#[test]
fn micro_14_tolerance_streams_equivalent() {
    let p: Protocol = load("ir");
    let io: Io = load("io");
    let (got, _) = run(&p, &io);

    let mut checked = 0;
    let mut worst = 0.0_f64;
    for (name, want) in &io.streams {
        let Some(got) = got.get(name) else { continue };
        assert_eq!(got.len(), want.len(), "stream {name}: length mismatch");
        for i in io.warmup_samples..got.len() {
            let d = (got[i] - want[i]).abs();
            assert!(
                d <= ATOL,
                "stream {name}: divergence at sample {i} (got {}, want {}); |diff|={d:e}",
                got[i], want[i]
            );
            worst = worst.max(d);
        }
        checked += 1;
    }
    // The gated continuous channel and the dwell's own streams are the ones
    // the dip counter moves; naming them keeps a renamed fixture channel from
    // quietly reducing this test to a pass over nothing.
    for required in ["smr_envelope", "reward.event", "reward.event.holds", "output/audio_gain"] {
        assert!(got.contains_key(required), "{STEM}: missing stream {required}");
        assert!(io.streams.contains_key(required), "{STEM}: no reference for {required}");
    }
    eprintln!("  {STEM} :: {checked} streams matched; max|diff| = {worst:e}");
}

#[test]
fn micro_14_tolerance_events_equivalent() {
    let p: Protocol = load("ir");
    let io: Io = load("io");
    let want: Vec<RefEvent> = load("events");
    let (_, got) = run(&p, &io);

    assert_eq!(
        got.len(), want.len(),
        "{STEM}: event count mismatch (got {}, want {})", got.len(), want.len()
    );
    assert!(!want.is_empty(), "{STEM}: reference event list is empty");
    for (i, (g, w)) in got.iter().zip(&want).enumerate() {
        assert_eq!(g.channel, w.channel, "{STEM}: event {i} channel mismatch");
        assert_eq!(g.kind, w.kind, "{STEM}: event {i} kind mismatch");
        let dts = (g.timestamp_s - w.timestamp_s).abs();
        assert!(dts <= 1e-9, "{STEM}: event {i} timestamp diff {dts:e} on {}", g.channel);
        match (g.value, w.value) {
            (Some(gv), Some(wv)) => {
                let dv = (gv - wv).abs();
                assert!(
                    dv <= ATOL,
                    "{STEM}: event {i} value diff {dv:e} on {} (got {gv}, want {wv})",
                    g.channel
                );
            }
            (None, None) => {}
            (gv, wv) => panic!("{STEM}: event {i} value None-ness mismatch ({gv:?} vs {wv:?})"),
        }
    }
    // A chime only fires when the dwell's rising edge does, so the fixture is
    // worthless as a tolerance vector if the forgiving hold never rose.
    let chimes = got.iter().filter(|e| e.channel == "audio_chime").count();
    assert!(chimes > 0, "{STEM}: no dwell events in the reference run");
    eprintln!("  {STEM} :: {} events matched ({chimes} chimes)", got.len());
}
