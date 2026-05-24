//! PyO3 binding: exposes the Rust evaluator to Python so the existing bench
//! harness (`ChunkedRunner` + `assert_equivalent`) can drive and validate it
//! in-process, and so latency can be measured against the Python evaluator.

use numpy::{IntoPyArray, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::eval::{Evaluator, Event as CoreEvent};
use crate::ir::Protocol;

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
        Ok(Self {
            inner: Evaluator::new(&p, sample_rate_hz, &channel_names),
        })
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
        let streams = self.inner.step_chunk(&rows);
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
    fn step_chunk_events<'py>(
        &mut self,
        chunk: PyReadonlyArray2<'py, f64>,
    ) -> PyResult<Vec<Event>> {
        let arr = chunk.as_array();
        let rows: Vec<Vec<f64>> = arr.outer_iter().map(|r| r.to_vec()).collect();
        Ok(self.inner.step_chunk_events(&rows).into_iter().map(Event::from).collect())
    }
}

#[pymodule]
fn refrain_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustEvaluator>()?;
    m.add_class::<Event>()?;
    Ok(())
}
