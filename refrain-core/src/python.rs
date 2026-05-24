//! PyO3 binding: exposes the Rust evaluator to Python so the existing bench
//! harness (`ChunkedRunner` + `assert_equivalent`) can drive and validate it
//! in-process, and so latency can be measured against the Python evaluator.

use numpy::{IntoPyArray, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::eval::Evaluator;
use crate::ir::Protocol;

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
}

#[pymodule]
fn refrain_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustEvaluator>()?;
    Ok(())
}
