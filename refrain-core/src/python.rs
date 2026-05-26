//! PyO3 binding: exposes the Rust evaluator to Python so the existing bench
//! harness (`ChunkedRunner` + `assert_equivalent`) can drive and validate it
//! in-process, and so latency can be measured against the Python evaluator.

use numpy::{IntoPyArray, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use std::panic::{catch_unwind, AssertUnwindSafe};

use crate::eval::{Evaluator, Event as CoreEvent, State};
use crate::ir::Protocol;

/// Run a Rust-core call that may panic, converting any panic into a catchable
/// Python `RuntimeError` rather than letting it propagate as an (uncatchable)
/// `pyo3_runtime.PanicException`. This lets an embedding host fall back to the
/// pure-Python backend on an unsupported-in-Rust construct instead of having a
/// panic take down the process. `what` names the operation in the message.
fn guard<R>(what: &str, f: impl FnOnce() -> R) -> PyResult<R> {
    catch_unwind(AssertUnwindSafe(f)).map_err(|e| {
        let detail = e
            .downcast_ref::<&str>()
            .map(|s| (*s).to_string())
            .or_else(|| e.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "unknown panic".to_string());
        pyo3::exceptions::PyRuntimeError::new_err(format!(
            "refrain_core: {what} failed in the Rust core ({detail}). This protocol \
             or construct may be unsupported by the Rust backend; retry with \
             backend=\"python\"."
        ))
    })
}

/// Map the Rust `State` enum to the string values Python expects, matching
/// `eval_.Evaluator.state` (`"ready"` | `"warmup"` | `"run"` | `"stopped"`).
fn state_str(s: State) -> &'static str {
    match s {
        State::Ready => "ready",
        State::Warmup => "warmup",
        State::Run => "run",
        State::Stopped => "stopped",
    }
}

/// Mirror of `eval_.Event`: one unit of evaluator output.
#[pyclass]
#[derive(Clone)]
struct Event {
    #[pyo3(get)]
    timestamp_s: f64,
    #[pyo3(get)]
    channel: String,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    value: Option<f64>,
}

impl From<CoreEvent> for Event {
    fn from(e: CoreEvent) -> Self {
        Event { timestamp_s: e.timestamp_s, channel: e.channel, kind: e.kind, value: e.value }
    }
}

#[pyclass]
struct RustEvaluator {
    inner: Evaluator,
}

#[pymethods]
impl RustEvaluator {
    /// Build from the IR-JSON wire format plus the host's runtime sample
    /// rate and channel layout.
    #[new]
    fn new(ir_json: &str, sample_rate_hz: f64, channel_names: Vec<String>) -> PyResult<Self> {
        let p: Protocol = serde_json::from_str(ir_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("IR-JSON: {e}")))?;
        let inner = guard("evaluator construction", || {
            Evaluator::new(&p, sample_rate_hz, &channel_names)
        })?;
        Ok(Self { inner })
    }

    /// Process one `(n_samples, n_channels)` chunk; return `{stream: ndarray}`
    /// matching the Python evaluator's `last_streams()` contract.
    fn step_chunk<'py>(
        &mut self,
        py: Python<'py>,
        chunk: PyReadonlyArray2<'py, f64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let arr = chunk.as_array();
        let rows: Vec<Vec<f64>> = arr.outer_iter().map(|r| r.to_vec()).collect();
        let streams = guard("step_chunk", || self.inner.step_chunk(&rows))?;
        let out = PyDict::new(py);
        for (k, v) in streams {
            out.set_item(k, v.into_pyarray(py))?;
        }
        Ok(out)
    }

    /// `eval_.Evaluator.start`: enter warmup (or run). Call before the first
    /// `step_chunk_events`.
    #[pyo3(signature = (skip_warmup = false))]
    fn start(&mut self, skip_warmup: bool) {
        self.inner.start(skip_warmup);
    }

    /// `eval_.Evaluator.stop`: end the session.
    fn stop(&mut self) {
        self.inner.stop();
    }

    /// `eval_.Evaluator.set_control`: live-retune a clinician control in place,
    /// preserving streaming state. An unknown name raises `KeyError`, matching
    /// the Python evaluator.
    fn set_control(&mut self, name: &str, value: f64) -> PyResult<()> {
        self.inner
            .set_control(name, value)
            .map_err(pyo3::exceptions::PyKeyError::new_err)
    }

    /// Process one `(n_samples, n_channels)` chunk and return the feedback
    /// `Event`s, matching the Python evaluator's `step_chunk` return value.
    /// Also caches the per-chunk streams map into `self.inner.last_streams`
    /// so `last_streams()` can surface it without a second `eval_chunk` call.
    /// Raises `RuntimeError` if called after `stop()`, matching the Python
    /// evaluator's behaviour (rather than panicking).
    fn step_chunk_events<'py>(
        &mut self,
        chunk: PyReadonlyArray2<'py, f64>,
    ) -> PyResult<Vec<Event>> {
        if self.inner.state() == State::Stopped {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "Evaluator.step_chunk() called after stop()",
            ));
        }
        let arr = chunk.as_array();
        let rows: Vec<Vec<f64>> = arr.outer_iter().map(|r| r.to_vec()).collect();
        let events = guard("step_chunk", || self.inner.step_chunk_events(&rows))?;
        Ok(events.into_iter().map(Event::from).collect())
    }

    /// Current lifecycle state, mirroring `eval_.Evaluator.state`.
    /// Returns `"ready"` | `"warmup"` | `"run"` | `"stopped"`.
    fn state(&self) -> &'static str {
        state_str(self.inner.state())
    }

    /// Seconds of warmup remaining, mirroring `eval_.Evaluator.warmup_remaining_s`.
    /// Returns 0.0 unless the evaluator is in `warmup` state.
    fn warmup_remaining_s(&self) -> f64 {
        self.inner.warmup_remaining_s()
    }

    /// Clinician-observation snapshot from the most recent `step_chunk_events`
    /// call. Returns `{canonical_name: f64}` — booleans stored as 0.0/1.0.
    /// The Python wrapper coerces known-boolean keys back to `bool`.
    /// Empty before the first `step_chunk_events` call. Returns a copy.
    fn last_taps<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        for (k, v) in self.inner.last_taps() {
            out.set_item(k, v)?;
        }
        Ok(out)
    }

    /// Per-chunk stream snapshot cached by the most recent `step_chunk_events`
    /// call. Returns `{stream_name: ndarray}` matching `last_streams()` in
    /// the Python evaluator. Empty before the first call.
    fn last_streams<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        for (k, v) in self.inner.last_streams() {
            out.set_item(k, v.into_pyarray(py))?;
        }
        Ok(out)
    }
}

#[pymodule]
fn refrain_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustEvaluator>()?;
    m.add_class::<Event>()?;
    Ok(())
}
