//! Refrain portable core (PoC).
//!
//! Consumes the IR-JSON wire format emitted by the Python front-end
//! (`refrain.ir_json`) and evaluates it chunk-by-chunk, reproducing the
//! Python evaluator's signal-to-feedback transformation. Filter design stays
//! in Python; the coefficients are baked into the IR, so this core only
//! *runs* deterministic cascades/convolutions.

pub mod dsp;
pub mod eval;
pub mod ir;

#[cfg(feature = "python")]
mod python;
