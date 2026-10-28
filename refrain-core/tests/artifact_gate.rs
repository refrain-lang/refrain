//! Golden vector for an inhibit that says it is an artifact gate (IR-JSON v0.6).
//!
//! `kind = "artifact"` makes a muted sample unobservable to a hold rather than
//! a failing one, and that rule is per-sample carried state written twice —
//! `DwellImpl.step` plus `_compute_artifact_muted` in `src/refrain/eval_.py`,
//! and `dsp::Dwell` plus the inhibit loop in `src/eval.rs` here. The unit
//! tests in `dsp.rs` pin the frozen branch on hand-built streams; this pins
//! the whole path on a real protocol, against the streams, events and taps the
//! Python evaluator produced for the same input (`tools/gen_fixtures.py`).
//!
//! The fixture covers BOTH hold paths. `output/video_clarity` is the
//! `output`-bound `hold_for`; `reward.event` is the reward dwell, which 44 of
//! the 45 live protocols drive feedback from and which the mask reached one
//! release later than the output path did. The reward half of the vector is
//! what `reward_dwell_never_fires_into_a_blind_window` below is about: before
//! the mask reached this path, three of micro_15's fourteen reward edges fired
//! inside a mute and were thrown away by the output gate, and `dwell` does not
//! re-arm, so each of the three was a reward the trainee could never collect.
//! With the mask, the clock does not reach those edges at all and every edge
//! the dwell produces is one the patient hears.
//!
//! Streams are compared to 1e-9 rather than `equivalence.rs`'s 1e-6/1e-4, for
//! the same reason `tolerance.rs` does: `output/video_clarity` is a boolean a
//! held clock switches, so a one-sample disagreement about whether a mute was
//! observable is a whole-value difference and nothing here needs the looser
//! band. Events are compared exactly in count, channel, kind and order.

use std::collections::BTreeMap;

use refrain_core::eval::{Evaluator, Event};
use refrain_core::ir::Protocol;
use serde::Deserialize;

const STEM: &str = "micro_15_artifact_gate";

/// Absolute tolerance for a stream sample. See the module comment.
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

/// One pass over the fixture signal, capturing the per-chunk streams, the
/// events, and the `muted` tap after each chunk — the same single-pass shape
/// `gen_fixtures.py::_reference` used, so no DSP state is stepped twice.
fn run(p: &Protocol, io: &Io) -> (BTreeMap<String, Vec<f64>>, Vec<Event>, Vec<bool>) {
    let mut ev = Evaluator::new(p, io.sample_rate_hz, &io.channels);
    ev.start(true); // skip_warmup=True, same as gen_fixtures.py

    let mut streams: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut events: Vec<Event> = Vec::new();
    let mut muted: Vec<bool> = Vec::new();
    for chunk in io.input.chunks(io.chunk_size) {
        events.extend(ev.step_chunk_events(chunk));
        for (k, v) in ev.last_streams() {
            streams.entry(k).or_default().extend(v);
        }
        muted.push(ev.last_taps()["muted"] != 0.0);
    }
    (streams, events, muted)
}

#[test]
fn micro_15_artifact_gate_is_tagged_v06_and_labels_its_inhibit() {
    // The fixture's whole purpose is to be the `kind` vector. Without these
    // two assertions every comparison below would still pass while the label
    // was being dropped on the floor at either end of the wire.
    let p: Protocol = load("ir");
    assert_eq!(p.refrain_ir_version.as_deref(), Some("0.6"));
    refrain_core::ir::check_ir_version(&p).unwrap();
    let ih = p.inhibits.get("emg").expect("micro_15 declares inhibit \"emg\"");
    assert_eq!(ih.kind.as_deref(), Some("artifact"));
    assert_eq!(ih.action_kind, "mute");
}

#[test]
fn micro_15_artifact_gate_streams_equivalent() {
    let p: Protocol = load("ir");
    let io: Io = load("io");
    let (got, _, muted) = run(&p, &io);

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
    // The `output`-bound hold is the stream the artifact mask moves; the two
    // EMG derives are what the gate reads. Naming them keeps a renamed
    // fixture channel from quietly reducing this test to a pass over nothing.
    for required in [
        "smr_envelope", "emg_power", "emg_envelope",
        "reward.event", "reward.event.holds",
        "output/video_clarity", "output/audio_chime", "output/audio_gain",
    ] {
        assert!(got.contains_key(required), "{STEM}: missing stream {required}");
        assert!(io.streams.contains_key(required), "{STEM}: no reference for {required}");
    }
    // A fixture whose gate never trips, or never clears, is not a vector for
    // `kind` at all: the held clock would never be exercised either way.
    assert!(muted.iter().any(|&m| m), "{STEM}: the EMG gate never tripped");
    assert!(muted.iter().any(|&m| !m), "{STEM}: the EMG gate never cleared");
    // Likewise the hold has to both rise and fall somewhere in the run.
    let clarity = &got["output/video_clarity"];
    assert!(clarity.iter().any(|&v| v > 0.0), "{STEM}: the output-bound hold never held");
    assert!(clarity.iter().any(|&v| v == 0.0), "{STEM}: the output-bound hold never dropped");
    eprintln!(
        "  {STEM} :: {checked} streams matched; max|diff| = {worst:e}; \
         muted on {}/{} chunks",
        muted.iter().filter(|&&m| m).count(), muted.len()
    );
}

#[test]
fn reward_dwell_never_fires_into_a_blind_window() {
    // The reward half of the vector, stated as the property rather than as a
    // sample index. `audio_chime = reward.event` and the output stage ANDs
    // `!muted` into every event channel, so an edge the gate swallowed is one
    // that is set in `reward.event` and clear in `output/audio_chime`. Under
    // the pause rule the clock cannot reach a rising edge inside a mute, so
    // the two streams have to agree sample for sample. Three of the fourteen
    // edges in the pre-mask reference failed exactly this.
    let p: Protocol = load("ir");
    let io: Io = load("io");
    let (got, events, _) = run(&p, &io);

    let raw = &got["reward.event"];
    let heard = &got["output/audio_chime"];
    let edges: Vec<usize> = (0..raw.len()).filter(|&i| raw[i] != 0.0).collect();
    // A run with no reward edges would pass the comparison below vacuously.
    assert!(
        edges.len() > 5,
        "{STEM}: only {} reward edges — not a vector for the reward path",
        edges.len()
    );
    let swallowed: Vec<usize> =
        edges.iter().copied().filter(|&i| heard[i] == 0.0).collect();
    assert!(
        swallowed.is_empty(),
        "{STEM}: reward edges fired inside a blind window and were discarded \
         at samples {swallowed:?} — the reward clock ran through a mute"
    );
    // And the edges really are the chimes the host receives, so the property
    // above is about delivered feedback and not an internal stream alone.
    let chimes = events.iter().filter(|e| e.channel == "audio_chime").count();
    assert_eq!(
        chimes, edges.len(),
        "{STEM}: {} reward edges but {chimes} audio_chime events", edges.len()
    );
    // The gate still has to be shutting over part of the run, or there was no
    // blind window for an edge to fall into in the first place.
    let clarity = &got["output/video_clarity"];
    assert!(
        clarity.iter().any(|&v| v == 0.0) && clarity.iter().any(|&v| v > 0.0),
        "{STEM}: the output-bound hold never changed state"
    );
    eprintln!("  {STEM} :: {} reward edges, none swallowed", edges.len());
}

#[test]
fn micro_15_artifact_gate_events_equivalent() {
    let p: Protocol = load("ir");
    let io: Io = load("io");
    let want: Vec<RefEvent> = load("events");
    let (_, got, _) = run(&p, &io);

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
    // The chime fires on the reward dwell's rising edge, and `video_clarity`
    // emits the `output`-bound hold's value: an empty run of either would
    // leave one of the two hold paths unexercised.
    let chimes = got.iter().filter(|e| e.channel == "audio_chime").count();
    assert!(chimes > 0, "{STEM}: no reward dwell events in the reference run");
    let clarity = got.iter().filter(|e| e.channel == "video_clarity").count();
    assert!(clarity > 0, "{STEM}: no output-bound hold events in the reference run");
    eprintln!(
        "  {STEM} :: {} events matched ({chimes} chimes, {clarity} clarity)", got.len()
    );
}
