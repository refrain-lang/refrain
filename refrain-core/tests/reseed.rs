//! Block-boundary re-seeding against the committed golden vector.
//!
//! `reseed_staged` is a staged session whose second block re-measures the
//! threshold the first block trained against: 4 s of 2 uV warm-up (the
//! warm-up seed writes 2.0), 6 s of 5 uV under block one (the trailing 2 s
//! window at the boundary is all 5.0, so the re-seed writes 5.0), then 8 uV
//! under block two. Every percentile in the vector is therefore exact by
//! construction.
//!
//! Three separate things can be wrong here, and each one gets its own
//! assertion, because none of them implies the others:
//!
//!   - the **value** the re-seed writes (the seed report, and `threshold/thr`
//!     stepping at 10 s),
//!   - **when** it fires (one record per entry, at the boundary chunk),
//!   - the **hold reset** that comes with it: both bundles' dwells have been
//!     holding since 4.25 s, so without it the threshold would move and the
//!     trainee would hear nothing to mark it. The second `audio_chime` at
//!     10.25 s is that reset, and it is in the committed event list.
//!
//! Driven with `step_chunk_events` rather than the streams-only `step_chunk`
//! for the same reason `seed_parity.rs` and `seed_scale.rs` are: the
//! streams-only path neither auto-starts nor advances the phase cursor, so
//! neither the warm-up->run transition nor the block boundary would ever
//! happen through it.

use std::collections::{BTreeMap, BTreeSet};

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

const STEM: &str = "reseed_staged";

#[derive(serde::Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

#[derive(serde::Deserialize)]
struct RefEvent {
    timestamp_s: f64,
    channel: String,
    kind: String,
    value: Option<f64>,
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

fn load_events() -> Vec<RefEvent> {
    serde_json::from_str(
        &std::fs::read_to_string(format!("tests/fixtures/{STEM}.events.json")).unwrap(),
    )
    .unwrap()
}

fn load_taps() -> Vec<BTreeMap<String, f64>> {
    serde_json::from_str(
        &std::fs::read_to_string(format!("tests/fixtures/{STEM}.taps.json")).unwrap(),
    )
    .unwrap()
}

#[test]
fn the_wire_carries_the_reseed_keys_and_the_version_that_protects_them() {
    let p = load_ir();
    assert_eq!(p.refrain_ir_version.as_deref(), Some("0.6"));
    let two = &p.blocks["two"];
    assert_eq!(two.reseed, vec!["smr_threshold_uv".to_string()]);
    // The hold set is the resolver's, consumed as given. It is scoped to the
    // control and not to the block, so block one's bundle is in it too: a
    // streak measured against a threshold that just moved is stale wherever
    // it sits, and every bundle is stepped on every chunk.
    assert_eq!(
        two.reseed_holds,
        vec![
            "reward_bundle/block_one_reward".to_string(),
            "reward_bundle/block_two_reward".to_string(),
        ]
    );
    // The block that re-seeds nothing says so with empty vectors, not with
    // absent fields that deserialize as something else.
    assert!(p.blocks["one"].reseed.is_empty());
    assert!(p.blocks["one"].reseed_holds.is_empty());
}

#[test]
fn an_absent_reseed_list_deserializes_as_empty_not_as_a_missing_field() {
    // `seed_smr_baseline` predates the keys entirely and has no blocks; the
    // staged autopilot bench has blocks written before the keys existed.
    // Either would fail to deserialize at all if `#[serde(default)]` were
    // dropped, which is the backward-compatibility property itself.
    let p: Protocol = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/autopilot_staged.ir.json").unwrap(),
    )
    .unwrap();
    for (name, block) in p.blocks.iter() {
        assert!(block.reseed.is_empty(), "block {name} re-seeds nothing");
        assert!(block.reseed_holds.is_empty(), "block {name} resets nothing");
    }
}

/// Drive the whole fixture once and assert the seed report, the events, the
/// taps and the streams all match the Python reference.
///
/// One test rather than four because one run produces all four observations
/// and splitting it would quadruple a 4096-sample replay for no extra
/// coverage. The seed report is asserted FIRST and the stream comparison
/// last, deliberately: the re-seeded value feeds `threshold/thr`, so any
/// change to it moves every stream below. With the stream loop first, a wrong
/// value would fail on a sample index and the assertions that say what went
/// wrong would never run.
#[test]
fn reseeded_streams_events_and_taps_are_bit_exact_across_backends() {
    let p = load_ir();
    let io = load_io();
    let want_events = load_events();
    let want_taps = load_taps();

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    let mut got_events = Vec::new();
    let mut got_streams: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut got_taps: Vec<BTreeMap<String, f64>> = Vec::new();
    for chunk in io.input.chunks(io.chunk_size) {
        // No `start()` call: `step_chunk_events` auto-starts with
        // skip_warmup=false, which is how the warm-up seed fires at all.
        got_events.extend(ev.step_chunk_events(chunk));
        for (key, values) in ev.last_streams() {
            got_streams.entry(key.clone()).or_default().extend(values.iter().copied());
        }
        got_taps.push(ev.last_taps());
    }

    // --- The value, and the trail that says where it came from -------------
    let report = ev.seed_report();
    let entry = &report["smr_threshold_uv"];
    assert_eq!(entry.status, "seeded");
    assert_eq!(entry.value, Some(5.0), "the re-seed must write block one's median");
    let trail: Vec<(&str, &str, Option<f64>, f64)> = entry
        .history
        .iter()
        .map(|h| (h.trigger.as_str(), h.status.as_str(), h.new_value, h.at_time_s))
        .collect();
    assert_eq!(
        trail,
        vec![
            ("warmup", "seeded", Some(2.0), 4.0),
            ("block:two", "seeded", Some(5.0), 10.0),
        ],
        "two seeding events: the warm-up at the run edge, the re-seed at the boundary"
    );
    // The window the re-seed measured is the signal since the warm-up seed —
    // 6 s of block one at 256 Hz — not the whole session.
    assert_eq!(entry.history[1].fresh_samples, 1536);
    assert_eq!(entry.history[1].old_value, Some(2.0));

    // --- When it fired, and the hold restart that came with it -------------
    let chimes: Vec<f64> = got_events
        .iter()
        .filter(|e| e.channel == "audio_chime")
        .map(|e| e.timestamp_s)
        .collect();
    assert_eq!(chimes.len(), 2, "one chime per earned streak, got {chimes:?}");
    // 250 ms after the run edge, then 250 ms after the boundary. The second
    // one exists only because the re-seed restarted the dwell: without the
    // reset both bundles would still be holding from the first streak and no
    // rising edge would fire.
    assert!((chimes[0] - (4.0 + 63.0 / io.sample_rate_hz)).abs() < 1e-9, "{chimes:?}");
    assert!((chimes[1] - (10.0 + 63.0 / io.sample_rate_hz)).abs() < 1e-9, "{chimes:?}");

    // --- Events, event for event, against the Python reference -------------
    assert_eq!(
        got_events.len(),
        want_events.len(),
        "event count mismatch (got {}, want {})",
        got_events.len(),
        want_events.len()
    );
    for (i, (g, w)) in got_events.iter().zip(&want_events).enumerate() {
        assert_eq!(g.channel, w.channel, "event {i} channel");
        assert_eq!(g.kind, w.kind, "event {i} kind");
        assert!((g.timestamp_s - w.timestamp_s).abs() < 1e-9, "event {i} timestamp");
        match (g.value, w.value) {
            (Some(gv), Some(wv)) => assert!(
                (gv - wv).abs() < 1e-9,
                "event {i} value: rust {gv} vs python {wv}"
            ),
            (None, None) => {}
            (gv, wv) => panic!("event {i} value None-ness: {gv:?} vs {wv:?}"),
        }
    }

    // --- Taps, chunk for chunk ---------------------------------------------
    // Every chunk's values are compared, warm-up included — unlike the other
    // tap vectors, which skip the warm-up window because a filter is still
    // settling there. This protocol runs no filter at all (`magnitude()` off a
    // `passthrough()` input, then a sigmoid), so every tap is exact from the
    // first sample and there is nothing to let go of.
    assert_eq!(got_taps.len(), want_taps.len(), "chunk count mismatch");
    for (ci, (got, want)) in got_taps.iter().zip(&want_taps).enumerate() {
        let got_keys: BTreeSet<&String> = got.keys().collect();
        let want_keys: BTreeSet<&String> = want.keys().collect();
        assert_eq!(got_keys, want_keys, "chunk {ci} tap key-set mismatch");
        for (k, &wv) in want {
            let gv = got[k];
            assert!((gv - wv).abs() < 1e-9, "chunk {ci} tap {k:?}: rust {gv} vs python {wv}");
        }
    }
    // The tap a dropped re-seed moves on its own, read at the chunk either
    // side of the 10 s boundary (8 chunks of 32 samples per second).
    assert_eq!(got_taps[79]["threshold/thr"], 2.0);
    assert_eq!(got_taps[80]["threshold/thr"], 5.0);

    // --- Streams, sample for sample ----------------------------------------
    for (key, want) in &io.streams {
        let mine = got_streams.get(key).unwrap_or_else(|| panic!("missing stream {key}"));
        assert_eq!(mine.len(), want.len(), "stream {key}: length mismatch");
        for (i, (g, w)) in mine.iter().zip(want).enumerate() {
            assert!((g - w).abs() < 1e-9, "{key} @ sample {i}: rust {g} vs python {w}");
        }
    }
}

/// A host that pauses the transport at the start of a re-seeding block must
/// not re-fire the re-seed on every chunk of the pause.
///
/// `phase_elapsed` is not a first-chunk marker: `advance` skips the elapsed
/// increment while the clock is frozen, so a marker built on `phase_elapsed
/// == 0` would fire the re-seed, and restart every downstream hold, on every
/// chunk until the practitioner resumed. Both engines would do it, so the
/// drift gate would stay silent — which is why this is pinned here as well as
/// in `tests/test_eval_reseed.py`.
#[test]
fn a_frozen_clock_at_the_boundary_fires_the_reseed_once() {
    let p = load_ir();
    let io = load_io();
    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);

    // Run the warm-up and block one (10 s), then pause and feed block two.
    let boundary = 10 * io.sample_rate_hz as usize;
    for chunk in io.input[..boundary].chunks(io.chunk_size) {
        ev.step_chunk_events(chunk);
    }
    ev.set_clock_frozen(true);
    let mut chimes = 0;
    for chunk in io.input[boundary..].chunks(io.chunk_size) {
        chimes += ev
            .step_chunk_events(chunk)
            .iter()
            .filter(|e| e.channel == "audio_chime")
            .count();
    }

    let report = ev.seed_report();
    let entry = &report["smr_threshold_uv"];
    let triggers: Vec<&str> = entry.history.iter().map(|h| h.trigger.as_str()).collect();
    assert_eq!(triggers, vec!["warmup", "block:two"], "one fire per entry, pause or no pause");
    // And one hold restart, so one re-earned chime, not one per frozen chunk.
    // `set_clock_frozen` resets held outputs itself, so the chime comes from
    // the dwell re-earning its streak either way; what this counts is that it
    // happens once.
    assert_eq!(chimes, 1, "the restarted streak earns one chime, not one per chunk");
}
