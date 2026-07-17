//! uniffi binding: exposes the Rust evaluator to Swift and Kotlin as a thin
//! mobile wrapper. This is deliberately a *thin* layer over `eval::Evaluator`
//! — it owns one evaluator behind a `Mutex` (uniffi objects are `Arc`-shared,
//! and the evaluator's chunk methods take `&mut self`) and forwards calls.
//! It does NOT reimplement any evaluation logic. It mirrors the shape of the
//! pyo3 binding in `python.rs`; the only FFI-specific work is reshaping the
//! flat row-major chunk into rows (uniffi has no 2-D type), which reuses the
//! shared `eval::rows_from_flat` helper rather than carrying its own copy.

use std::sync::Mutex;

use crate::eval::{rows_from_flat, Evaluator, Event as CoreEvent};
use crate::ir::Protocol;

// NB: `uniffi::setup_scaffolding!()` lives at the crate root (lib.rs); it
// generates the crate-level `UniFfiTag` the derive macros below resolve to.

/// Errors surfaced across the FFI boundary. Currently the only fallible step
/// is deserializing the IR-JSON wire format. `Display`/`Error` are implemented
/// by hand to avoid pulling in `thiserror` just for one variant.
#[derive(Debug, uniffi::Error)]
pub enum RefrainError {
    /// The IR-JSON string could not be deserialized into a `Protocol`.
    InvalidIr { message: String },
    /// `set_control` was called with a name that is not a declared control
    /// (mirrors the Python evaluator raising `KeyError`).
    UnknownControl { message: String },
}

impl std::fmt::Display for RefrainError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RefrainError::InvalidIr { message } => {
                write!(f, "IR-JSON deserialization failed: {message}")
            }
            RefrainError::UnknownControl { message } => {
                write!(f, "set_control: {message}")
            }
        }
    }
}

impl std::error::Error for RefrainError {}

/// Mirror of `eval::Event` / `eval_.Event`: one unit of evaluator output.
/// `value` is the per-chunk mean for value channels and `None` for discrete
/// events. Re-exposed as a uniffi record so Swift/Kotlin get a plain value
/// type; the conversion is a field-for-field move, no logic.
#[derive(Clone, Debug, uniffi::Record)]
pub struct Event {
    pub timestamp_s: f64,
    pub channel: String,
    pub kind: String,
    pub value: Option<f64>,
}

impl From<CoreEvent> for Event {
    fn from(e: CoreEvent) -> Self {
        Event { timestamp_s: e.timestamp_s, channel: e.channel, kind: e.kind, value: e.value }
    }
}

/// Mirror of `eval::PhaseSnapshot` / `eval_.Evaluator.current_phase`: the phase
/// the most recent chunk ran under (aligned with `last_taps`). `index == -1`
/// once terminal / before any chunk.
#[derive(Clone, Debug, uniffi::Record)]
pub struct PhaseInfo {
    pub index: i64,
    pub name: Option<String>,
    pub mode: Option<String>,
    pub output_muted: bool,
    pub block: Option<String>,
    pub remaining_s: Option<f64>,
    pub clock_frozen: bool,
    pub held: bool,
}

impl From<crate::eval::PhaseSnapshot> for PhaseInfo {
    fn from(s: crate::eval::PhaseSnapshot) -> Self {
        PhaseInfo {
            index: s.index,
            name: s.name,
            mode: s.mode,
            output_muted: s.output_muted,
            block: s.block,
            remaining_s: s.remaining_s,
            clock_frozen: s.clock_frozen,
            held: s.held,
        }
    }
}

/// Mobile-facing handle that owns one `eval::Evaluator`. Thin wrapper: every
/// method forwards to the evaluator. The `Mutex` provides the interior
/// mutability uniffi's `&self` methods need (the evaluator's chunk methods are
/// `&mut self`) and is safe because uniffi shares this object as an `Arc`.
#[derive(uniffi::Object)]
pub struct RefrainCore {
    inner: Mutex<Evaluator>,
}

#[uniffi::export]
impl RefrainCore {
    /// Build from the IR-JSON wire format plus the host's runtime sample rate
    /// and channel layout. Deserializes the IR via the existing serde
    /// `ir::Protocol`; deserialization errors map to `RefrainError::InvalidIr`.
    #[uniffi::constructor]
    pub fn new(
        ir_json: String,
        sample_rate_hz: f64,
        channel_names: Vec<String>,
    ) -> Result<std::sync::Arc<Self>, RefrainError> {
        let p: Protocol = serde_json::from_str(&ir_json)
            .map_err(|e| RefrainError::InvalidIr { message: e.to_string() })?;
        crate::eval::check_ir_version(&p)
            .map_err(|message| RefrainError::InvalidIr { message })?;
        Ok(std::sync::Arc::new(Self {
            inner: Mutex::new(Evaluator::new(&p, sample_rate_hz, &channel_names)),
        }))
    }

    /// `eval::Evaluator::start`: enter warmup (or run). Call before the first
    /// `step_chunk_events`. `skip_warmup` jumps straight to `run`.
    pub fn start(&self, skip_warmup: bool) {
        self.inner.lock().unwrap().start(skip_warmup);
    }

    /// `eval::Evaluator::stop`: end the session.
    pub fn stop(&self) {
        self.inner.lock().unwrap().stop();
    }

    /// `eval::Evaluator::set_control`: live-retune a clinician control in place,
    /// preserving streaming state. An unknown name yields
    /// `RefrainError::UnknownControl` (the FFI analogue of Python's `KeyError`).
    pub fn set_control(&self, name: String, value: f64) -> Result<(), RefrainError> {
        self.inner
            .lock()
            .unwrap()
            .set_control(&name, value)
            .map_err(|message| RefrainError::UnknownControl { message })
    }

    /// Process one chunk and emit feedback `Event`s. uniffi has no 2-D type, so
    /// the chunk arrives as a flat, row-major `(n_samples * n_channels)` buffer
    /// plus `n_channels`; it is reshaped into rows via the shared
    /// `eval::rows_from_flat` helper (same reshape contract as the pyo3 path)
    /// and forwarded to the unchanged `step_chunk_events`.
    pub fn step_chunk_events(&self, chunk: Vec<f64>, n_channels: u32) -> Vec<Event> {
        let rows = rows_from_flat(&chunk, n_channels as usize);
        self.inner
            .lock()
            .unwrap()
            .step_chunk_events(&rows)
            .into_iter()
            .map(Event::from)
            .collect()
    }

    /// `eval::Evaluator::advance_phase`: end the current phase now, enter the
    /// next; past the last phase transitions to `stopped`. Returns false if
    /// already stopped. (Clinician "Next →".)
    pub fn advance_phase(&self) -> bool {
        self.inner.lock().unwrap().advance_phase()
    }

    /// `eval::Evaluator::hold`: extend a `timed_with_floor` phase past its floor
    /// (`hold(false)` re-arms). Returns true if it took effect.
    pub fn hold(&self, held: bool) -> bool {
        self.inner.lock().unwrap().hold(held)
    }

    /// `eval::Evaluator::set_clock_frozen`: freeze/resume the phase clock on any
    /// phase type (transport pause); orthogonal to output muting.
    pub fn set_clock_frozen(&self, frozen: bool) {
        self.inner.lock().unwrap().set_clock_frozen(frozen);
    }

    /// `eval::Evaluator::current_phase`: snapshot of the phase the most recent
    /// chunk ran under (aligned with the taps).
    pub fn current_phase(&self) -> PhaseInfo {
        self.inner.lock().unwrap().current_phase().into()
    }

    /// `eval::Evaluator::last_taps`: the most recent chunk's internal computed values —
    /// keys like `input/raw`, `derive/<name>`, `threshold/<name>`, `reward/continuous`,
    /// `output/<channel>` (booleans encoded as 0.0/1.0). For clinician observation /
    /// live tuning. Empty before the first chunk. (BTreeMap → HashMap so uniffi maps it
    /// to a Swift `[String: Double]` / Kotlin `Map`.)
    pub fn last_taps(&self) -> std::collections::HashMap<String, f64> {
        self.inner.lock().unwrap().last_taps().into_iter().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The mobile wrapper (flat-buffer in, `Event`s out) must produce exactly
    /// what the direct `Evaluator` path produces — proving it is a thin pass-
    /// through and that `rows_from_flat` reshapes correctly. Runs only under
    /// `--features uniffi`; the default `cargo test` never sees it.
    #[test]
    fn wrapper_matches_direct_evaluator() {
        let ir = std::fs::read_to_string("tests/fixtures/micro_05_reward.ir.json").unwrap();
        let io: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string("tests/fixtures/micro_05_reward.io.json").unwrap(),
        )
        .unwrap();

        let channels: Vec<String> = io["channels"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_str().unwrap().to_string())
            .collect();
        let sample_rate_hz = io["sample_rate_hz"].as_f64().unwrap();
        let chunk_size = io["chunk_size"].as_u64().unwrap() as usize;
        let rows: Vec<Vec<f64>> = io["input"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect())
            .collect();
        let n_channels = channels.len() as u32;

        // Direct evaluator path (the reference).
        let p: Protocol = serde_json::from_str(&ir).unwrap();
        let mut direct = Evaluator::new(&p, sample_rate_hz, &channels);
        direct.start(true);
        let mut want: Vec<CoreEvent> = Vec::new();
        for chunk in rows.chunks(chunk_size) {
            want.extend(direct.step_chunk_events(chunk));
        }

        // Mobile wrapper path: same chunks, but flattened row-major.
        let core = RefrainCore::new(ir, sample_rate_hz, channels).unwrap();
        core.start(true);
        let mut got: Vec<Event> = Vec::new();
        for chunk in rows.chunks(chunk_size) {
            let flat: Vec<f64> = chunk.iter().flatten().copied().collect();
            got.extend(core.step_chunk_events(flat, n_channels));
        }

        assert_eq!(got.len(), want.len(), "event count mismatch");
        assert!(!got.is_empty(), "fixture should produce events");
        for (g, w) in got.iter().zip(&want) {
            assert_eq!(g.channel, w.channel);
            assert_eq!(g.kind, w.kind);
            assert!((g.timestamp_s - w.timestamp_s).abs() <= 1e-12);
            assert_eq!(g.value, w.value);
        }
    }
}
