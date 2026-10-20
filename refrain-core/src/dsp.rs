//! Streaming DSP stages and stateful trackers.
//!
//! Coefficients are baked by the Python front-end (`refrain.ir_json`), so
//! these stages only *run* the deterministic recurrences/convolutions — no
//! filter design here. Each recurrence is written to match the exact state
//! convention of the SciPy routine the Python evaluator uses, so outputs
//! agree to floating-point tolerance.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use crate::coherence::WelchMsc;

/// A live-tunable scalar parameter shared between a stage and the evaluator's
/// `set_control` registry. `Arc<Mutex<_>>` (rather than `Rc<RefCell<_>>`) keeps
/// the evaluator `Send + Sync`, which the uniffi `Arc<Mutex<Evaluator>>` wrapper
/// requires. The cell is read once per `step`/`process` call (per chunk), so the
/// lock is uncontended and never on a per-sample hot path. Writes happen only
/// from `set_control` (under the evaluator's `&mut self`).
pub type ControlCell = Arc<Mutex<f64>>;

/// Make a fresh control cell holding `initial`.
pub fn control_cell(initial: f64) -> ControlCell {
    Arc::new(Mutex::new(initial))
}

/// A chunk of stream values: real-valued, or complex (analytic signal) held
/// as parallel real/imag vectors.
pub enum Signal {
    Real(Vec<f64>),
    Complex(Vec<f64>, Vec<f64>),
}

impl Signal {
    pub fn into_real(self) -> Vec<f64> {
        match self {
            Signal::Real(x) => x,
            Signal::Complex(..) => panic!("expected a real signal, got complex"),
        }
    }
}

pub trait Stage: Send + Sync {
    fn process(&mut self, input: Signal) -> Signal;
    /// Stateful trackers (`auto_range`) expose a compact cross-session summary
    /// (Ask 2). Default: not a tracker.
    fn tracker_export(&self) -> Option<TrackerState> {
        None
    }
    /// Re-prime the tracker from a prior session's summary (Ask 2). No-op for
    /// non-trackers.
    fn tracker_seed(&mut self, _state: &TrackerState) {}
}

/// Compact, rate-independent adaptive-tracker state for cross-session
/// persistence (Ask 2). Mirrors the Python `export_state()` dicts;
/// `Evaluator::export_state` keys these by `"<entity>.<callee>"`.
#[derive(Clone, Debug)]
pub enum TrackerState {
    AutoRange { low: f64, high: f64, n_eff: u64 },
    Percentile { value: f64, target_pct: f64, n_eff: u64 },
}

/// `scipy.signal.sosfilt_zi(sos)` — the `sosfilt` state that holds a
/// unit-step input at its steady-state response. Multiply by a sample's level
/// to warm-start the cascade at that level.
///
/// Mirrors `refrain.primitive_impls._sos_unit_zi`, which is itself SciPy's
/// routine written out: per second-order section `lfilter_zi` reduces to the
/// 2x2 solve `[[1 + a1, -1], [a2, 1]] @ zi = [b1 - b0*a1, b2 - b0*a2]`, and
/// the sections compose by carrying each one's DC gain `H(1)` forward as a
/// scale. The two engines must agree here or the drift gate fails.
fn sos_unit_zi(sos: &[[f64; 6]]) -> Vec<(f64, f64)> {
    let mut out = Vec::with_capacity(sos.len());
    let mut scale = 1.0_f64;
    for sec in sos {
        let a0 = sec[3];
        let (b0, b1, b2) = (sec[0] / a0, sec[1] / a0, sec[2] / a0);
        let (a1, a2) = (sec[4] / a0, sec[5] / a0);
        let rhs0 = b1 - b0 * a1;
        let rhs1 = b2 - b0 * a2;
        let det = 1.0 + a1 + a2;
        out.push((
            scale * (rhs0 + rhs1) / det,
            scale * ((1.0 + a1) * rhs1 - a2 * rhs0) / det,
        ));
        scale *= (b0 + b1 + b2) / det;
    }
    out
}

/// IIR bandpass/bandpower as a cascade of second-order sections, in
/// Direct-Form-II-Transposed — matching `scipy.signal.sosfilt`'s per-section
/// state recurrence and its `(n_sections, 2)` state layout.
///
/// Warm-started on the first sample, matching `BandpassImpl` /
/// `BandpowerImpl`: state is seeded to the steady state that sample's level
/// would hold, so a signal arriving at a standing offset (skin temperature,
/// skin conductance, a respiration belt) reads its steady-state response
/// immediately instead of settling through the whole standing level first.
pub struct Biquad {
    sos: Vec<[f64; 6]>, // [b0, b1, b2, a0, a1, a2] per section (a0 == 1)
    z1: Vec<f64>,
    z2: Vec<f64>,
    /// `sosfilt_zi(sos)`, scaled by the first sample when it arrives.
    unit_zi: Vec<(f64, f64)>,
    primed: bool,
}

impl Biquad {
    pub fn new(sos: &[Vec<f64>]) -> Self {
        let sections: Vec<[f64; 6]> = sos
            .iter()
            .map(|s| [s[0], s[1], s[2], s[3], s[4], s[5]])
            .collect();
        let n = sections.len();
        let unit_zi = sos_unit_zi(&sections);
        Biquad { sos: sections, z1: vec![0.0; n], z2: vec![0.0; n], unit_zi, primed: false }
    }

    fn run(&mut self, x: &[f64]) -> Vec<f64> {
        if !self.primed {
            if let Some(&first) = x.first() {
                for (s, &(a, b)) in self.unit_zi.iter().enumerate() {
                    self.z1[s] = a * first;
                    self.z2[s] = b * first;
                }
                self.primed = true;
            }
        }
        let mut out = Vec::with_capacity(x.len());
        for &xin in x {
            let mut w = xin;
            for (s, sec) in self.sos.iter().enumerate() {
                let (b0, b1, b2, a1, a2) = (sec[0], sec[1], sec[2], sec[4], sec[5]);
                let y = b0 * w + self.z1[s];
                self.z1[s] = b1 * w - a1 * y + self.z2[s];
                self.z2[s] = b2 * w - a2 * y;
                w = y;
            }
            out.push(w);
        }
        out
    }
}

impl Stage for Biquad {
    fn process(&mut self, input: Signal) -> Signal {
        Signal::Real(self.run(&input.into_real()))
    }
}

/// FIR Hilbert transformer producing the analytic signal, matching
/// `HilbertFirImpl`: the imaginary branch is a streaming FIR convolution
/// (numpy `convolve(..., "valid")` semantics) and the real branch is a pure
/// delay by the FIR group delay so the parts align in time.
pub struct HilbertFir {
    h: Vec<f64>,
    group_delay: usize,
    imag_state: Vec<f64>, // trailing h.len()-1 samples
    real_state: Vec<f64>, // trailing group_delay samples
}

impl HilbertFir {
    pub fn new(taps: &[f64], group_delay: usize) -> Self {
        let keep = taps.len().saturating_sub(1);
        HilbertFir {
            h: taps.to_vec(),
            group_delay,
            imag_state: vec![0.0; keep],
            real_state: vec![0.0; group_delay],
        }
    }
}

impl Stage for HilbertFir {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let n = x.len();
        let nh = self.h.len();

        // Imag branch: padded = state ++ x; y[i] = sum_k h[k] * padded[i + nh-1 - k].
        let mut padded = Vec::with_capacity(self.imag_state.len() + n);
        padded.extend_from_slice(&self.imag_state);
        padded.extend_from_slice(&x);
        let mut imag = vec![0.0; n];
        for (i, slot) in imag.iter_mut().enumerate() {
            let mut acc = 0.0;
            for k in 0..nh {
                acc += self.h[k] * padded[i + (nh - 1) - k];
            }
            *slot = acc;
        }
        let keep = nh.saturating_sub(1);
        self.imag_state = padded[padded.len() - keep..].to_vec();

        // Real branch: delay by group_delay samples.
        let gd = self.group_delay;
        let mut combined = Vec::with_capacity(gd + n);
        combined.extend_from_slice(&self.real_state);
        combined.extend_from_slice(&x);
        let real = combined[..n].to_vec();
        self.real_state = combined[combined.len() - gd..].to_vec();

        Signal::Complex(real, imag)
    }
}

/// `band_envelope(band:, latency:)`: a band-limited analytic envelope in one
/// stage. The taps are complex and the input is real, so one convolution
/// produces the analytic signal directly and `|y|` is the envelope — no
/// separate Hilbert branch and no pure-delay branch to align.
///
/// Filter *design* stays in Python (`BandEnvelopeImpl`): the closed-form
/// inverse transform, the Hanning taper and the analytic-amplitude
/// calibration all happen there, and only the finished taps cross the wire.
/// This mirrors every other stage here — the core runs recurrences, it does
/// not reimplement SciPy.
///
/// The delay line is real, matching `BandEnvelopeImpl._buf`: the coefficients
/// carry the complex part, so there is nothing complex to carry between
/// chunks.
pub struct BandEnvelope {
    h_re: Vec<f64>,
    h_im: Vec<f64>,
    state: Vec<f64>, // trailing h.len()-1 input samples
}

impl BandEnvelope {
    pub fn new(h_re: &[f64], h_im: &[f64]) -> Self {
        assert_eq!(
            h_re.len(),
            h_im.len(),
            "band_envelope: fir_taps_re/fir_taps_im length mismatch ({} vs {})",
            h_re.len(),
            h_im.len()
        );
        assert!(!h_re.is_empty(), "band_envelope: empty tap vector");
        let keep = h_re.len().saturating_sub(1);
        BandEnvelope { h_re: h_re.to_vec(), h_im: h_im.to_vec(), state: vec![0.0; keep] }
    }
}

impl Stage for BandEnvelope {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let n = x.len();
        let nh = self.h_re.len();

        // Same `convolve(..., "valid")` indexing HilbertFir uses, so the two
        // agree sample-for-sample with numpy: padded = state ++ x, then
        // y[i] = sum_k h[k] * padded[i + nh-1 - k].
        let mut padded = Vec::with_capacity(self.state.len() + n);
        padded.extend_from_slice(&self.state);
        padded.extend_from_slice(&x);

        let mut out = vec![0.0; n];
        for (i, slot) in out.iter_mut().enumerate() {
            let mut acc_re = 0.0;
            let mut acc_im = 0.0;
            for k in 0..nh {
                let v = padded[i + (nh - 1) - k];
                acc_re += self.h_re[k] * v;
                acc_im += self.h_im[k] * v;
            }
            // The envelope is |analytic|; the complex intermediate never
            // leaves this stage, so the output stream is real.
            *slot = (acc_re * acc_re + acc_im * acc_im).sqrt();
        }

        let keep = nh.saturating_sub(1);
        self.state = padded[padded.len() - keep..].to_vec();

        Signal::Real(out)
    }
}

/// `magnitude()` — |z| for a complex stream (and |x| for a real one).
pub struct Magnitude;

impl Stage for Magnitude {
    fn process(&mut self, input: Signal) -> Signal {
        match input {
            Signal::Complex(re, im) => Signal::Real(
                re.iter().zip(&im).map(|(r, i)| (r * r + i * i).sqrt()).collect(),
            ),
            Signal::Real(x) => Signal::Real(x.iter().map(|v| v.abs()).collect()),
        }
    }
}

/// `smooth(tau)` — one-pole IIR low-pass `y[n] = a*x[n] + (1-a)*y[n-1]`,
/// with the baked coefficient `a`, matching `SmoothImpl` (lfilter with
/// `zi = state*(1-a)`).
///
/// Warm-started: `prev` is seeded to the first sample, which is the steady
/// state of a one-pole low-pass for a constant input at that level, so a
/// tonic/phasic pair (`x - smooth(x)`) reads ~0 phasic from sample one rather
/// than reporting the standing level as a response. `SmoothImpl` does the
/// same; they must, or the drift gate fails.
pub struct Smooth {
    /// Smoothing coefficient, shared with the `set_control` registry so a live
    /// `tau` retune (which recomputes `alpha`) takes effect without resetting
    /// `prev`. Read once per `process` call (per chunk).
    alpha: ControlCell,
    prev: f64,
    primed: bool,
}

impl Smooth {
    pub fn new(alpha: f64) -> Self {
        Smooth { alpha: control_cell(alpha), prev: 0.0, primed: false }
    }

    /// Construct over an existing shared `alpha` cell so the evaluator can keep
    /// a clone in its control registry.
    pub fn from_cell(alpha: ControlCell) -> Self {
        Smooth { alpha, prev: 0.0, primed: false }
    }
}

impl Stage for Smooth {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let alpha = *self.alpha.lock().unwrap();
        if !self.primed {
            if let Some(&first) = x.first() {
                self.prev = first;
                self.primed = true;
            }
        }
        let mut out = vec![0.0; x.len()];
        for (slot, &v) in out.iter_mut().zip(&x) {
            let y = alpha * v + (1.0 - alpha) * self.prev;
            self.prev = y;
            *slot = y;
        }
        Signal::Real(out)
    }
}

/// Recompute a `smooth` alpha from a new tau (in ms) at the given sample rate,
/// mirroring `SmoothImpl.update_control` / `_set_tau`:
/// `alpha = 1 - exp(-1 / ((tau_ms/1000) * fs))`. The `prev` state is untouched
/// (warm-restart), preserving the running output across the change.
pub fn smooth_alpha_from_tau_ms(tau_ms: f64, sample_rate_hz: f64) -> f64 {
    let tau_s = tau_ms / 1000.0;
    1.0 - (-1.0 / (tau_s * sample_rate_hz)).exp()
}

/// `percentile(target_pct, window)` — rolling-window percentile tracker
/// (mirrors `PercentileImpl`): a bounded buffer of the last `window` samples;
/// per sample append, then NumPy-`linear` percentile over the current buffer.
pub struct Percentile {
    /// Target percentile, shared with the `set_control` registry so a live
    /// retune takes effect WITHOUT resetting `buf` (the rolling window keeps
    /// filling across the change). Read once per `step` call (per chunk).
    target_pct: ControlCell,
    cap: usize,
    buf: VecDeque<f64>,
    /// When false, `step` still EMITS the current percentile over the existing
    /// buffer but does NOT ingest incoming samples — the evaluator freezes the
    /// window during mid-session muted rests (R6), mirroring
    /// `PercentileImpl.set_ingesting`.
    ingesting: bool,
    /// Samples ingested; reported (capped at `cap`) as `n_eff` in export.
    seen: u64,
}

impl Percentile {
    pub fn new(target_pct: f64, window_samples: usize) -> Self {
        Self::from_cell(control_cell(target_pct), window_samples)
    }

    /// Construct over an existing shared `target_pct` cell so the evaluator can
    /// keep a clone in its control registry.
    pub fn from_cell(target_pct: ControlCell, window_samples: usize) -> Self {
        let cap = window_samples.max(1);
        Percentile { target_pct, cap, buf: VecDeque::with_capacity(cap), ingesting: true, seen: 0 }
    }

    /// Compact cross-session summary (Ask 2): the current threshold value at
    /// `target_pct` plus the effective sample count. Mirrors
    /// `PercentileImpl.export_state`.
    pub fn export_state(&self) -> TrackerState {
        let value = if self.buf.is_empty() {
            0.0
        } else {
            percentile_linear(&self.buf, *self.target_pct.lock().unwrap())
        };
        TrackerState::Percentile {
            value,
            target_pct: *self.target_pct.lock().unwrap(),
            n_eff: (self.seen).min(self.cap as u64),
        }
    }

    /// Fill the window with `value` repeated so percentile(target_pct)==value
    /// exactly at session start (mirrors `PercentileImpl.seed`; identical math
    /// keeps parity structural).
    pub fn seed(&mut self, value: f64, n_eff: u64) {
        let n = (n_eff.min(self.cap as u64)).max(1) as usize;
        self.buf.clear();
        for _ in 0..n {
            self.buf.push_back(value);
        }
        self.seen = n as u64;
    }

    /// Append finite samples to the window WITHOUT computing a percentile
    /// (seed-latch warmup ingest). Non-finite samples are skipped — never
    /// pushed, never counted — so Rust matches Python's NaN-skip instead of
    /// panicking in `percentile_linear`.
    pub fn ingest(&mut self, x: &[f64]) {
        if !self.ingesting {
            return;
        }
        for &v in x {
            if v.is_finite() {
                if self.buf.len() == self.cap {
                    self.buf.pop_front();
                }
                self.buf.push_back(v);
                self.seen += 1;
            }
        }
    }

    /// Percentile of the current window at an explicit target (seed fire path,
    /// where the target tracks a live control read at the warmup->run edge).
    pub fn value_at(&self, pct: f64) -> f64 {
        if self.buf.is_empty() { 0.0 } else { percentile_linear(&self.buf, pct) }
    }

    /// Effective sample count (capped at the window), for the full-window check.
    pub fn n_eff(&self) -> u64 {
        self.seen.min(self.cap as u64)
    }

    /// Freeze (false) or resume (true) buffer ingestion. A frozen window keeps
    /// emitting over its existing buffer (R6 mid-session-rest handling).
    pub fn set_ingesting(&mut self, ingesting: bool) {
        self.ingesting = ingesting;
    }

    pub fn step(&mut self, x: &[f64]) -> Vec<f64> {
        let target_pct = *self.target_pct.lock().unwrap();
        let mut out = Vec::with_capacity(x.len());
        for &v in x {
            if self.ingesting {
                if self.buf.len() == self.cap {
                    self.buf.pop_front();
                }
                self.buf.push_back(v);
                self.seen += 1;
            }
            // A frozen empty buffer (e.g. window never populated) emits 0.0,
            // matching the Python `np.zeros(1)` fallback.
            if self.buf.is_empty() {
                out.push(0.0);
            } else {
                out.push(percentile_linear(&self.buf, target_pct));
            }
        }
        out
    }
}

/// `auto_range(window, percentile)` — rolling normalisation to [0, 1]
/// (mirrors `AutoRangeImpl`): a bounded buffer of the last `window` samples;
/// per sample append, compute the `low_pct`/`high_pct` quantiles over the
/// current buffer, then `clip((x - low) / max(high - low, 1e-9), 0, 1)`.
/// REUSES `percentile_linear` (called once per quantile, matching numpy's
/// two-quantile `np.percentile(arr, [low, high])`).
pub struct AutoRange {
    low_pct: f64,
    high_pct: f64,
    cap: usize,
    buf: VecDeque<f64>,
    /// Samples ingested; reported (capped at `cap`) as `n_eff` in export.
    seen: u64,
}

impl AutoRange {
    pub fn new(window_samples: usize, low_pct: f64, high_pct: f64) -> Self {
        let cap = window_samples.max(1);
        AutoRange { low_pct, high_pct, cap, buf: VecDeque::with_capacity(cap), seen: 0 }
    }

    /// Compact cross-session summary (Ask 2): current low/high anchors + the
    /// effective sample count. Mirrors `AutoRangeImpl.export_state`.
    pub fn export_state(&self) -> TrackerState {
        let (low, high) = if self.buf.is_empty() {
            (0.0, 0.0)
        } else {
            (
                percentile_linear(&self.buf, self.low_pct),
                percentile_linear(&self.buf, self.high_pct),
            )
        };
        TrackerState::AutoRange { low, high, n_eff: (self.seen).min(self.cap as u64) }
    }

    /// Pre-fill the window with a deterministic uniform ramp whose
    /// `low_pct`/`high_pct` percentiles reproduce the seeded anchors — the
    /// IDENTICAL math as `AutoRangeImpl.seed` (np.linspace(a, a+b_minus_a, n)),
    /// so seed/export round-trips match Python to 1e-6.
    pub fn seed(&mut self, low: f64, high: f64, n_eff: u64) {
        let n = (n_eff.min(self.cap as u64)).max(1) as usize;
        let span_pct = (self.high_pct - self.low_pct).max(1e-9);
        let b_minus_a = (high - low) * 100.0 / span_pct;
        let a = low - (self.low_pct / 100.0) * b_minus_a;
        self.buf.clear();
        for i in 0..n {
            let t = if n == 1 { 0.0 } else { i as f64 / (n - 1) as f64 };
            self.buf.push_back(a + t * b_minus_a);
        }
        self.seen = n as u64;
    }
}

impl Stage for AutoRange {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let mut out = Vec::with_capacity(x.len());
        for &v in &x {
            if self.buf.len() == self.cap {
                self.buf.pop_front();
            }
            self.buf.push_back(v);
            self.seen += 1;
            let low = percentile_linear(&self.buf, self.low_pct);
            let high = percentile_linear(&self.buf, self.high_pct);
            let span = (high - low).max(1e-9);
            out.push(((v - low) / span).clamp(0.0, 1.0));
        }
        Signal::Real(out)
    }

    fn tracker_export(&self) -> Option<TrackerState> {
        Some(self.export_state())
    }

    fn tracker_seed(&mut self, state: &TrackerState) {
        if let TrackerState::AutoRange { low, high, n_eff } = state {
            self.seed(*low, *high, *n_eff);
        }
    }
}

/// `differentiate()` — centered finite differences (mirrors
/// `DifferentiateImpl`): per chunk, prepend the two carried-over samples
/// `[prev2, prev]`, then `y[i] = (ext[i+2] - ext[i]) / (2*dt)`. `dt` is baked
/// (`1 / sample_rate`) so the runtime never threads the sample rate.
pub struct Differentiate {
    dt: f64,
    prev: f64,
    prev2: f64,
}

impl Differentiate {
    pub fn new(dt: f64) -> Self {
        Differentiate { dt, prev: 0.0, prev2: 0.0 }
    }
}

impl Stage for Differentiate {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let n = x.len();
        // ext = [prev2, prev, x[0], x[1], ...]; y[i] = (ext[i+2]-ext[i])/(2*dt).
        let mut out = Vec::with_capacity(n);
        let denom = 2.0 * self.dt;
        for i in 0..n {
            let lag = match i {
                0 => self.prev2,
                1 => self.prev,
                _ => x[i - 2],
            };
            out.push((x[i] - lag) / denom);
        }
        if n >= 2 {
            self.prev2 = x[n - 2];
            self.prev = x[n - 1];
        } else if n == 1 {
            self.prev2 = self.prev;
            self.prev = x[0];
        }
        Signal::Real(out)
    }
}

/// `bandpower(input, band, window)` — sliding-window mean of squared,
/// band-limited samples (mirrors `BandpowerImpl`): a Butterworth bandpass
/// (REUSES `Biquad` with the baked `sos`), then square, then a rolling mean
/// over the last `window_samples` squared values. The mean is computed over
/// the current (possibly short) buffer to match numpy's `np.mean(deque)`.
pub struct Bandpower {
    filter: Biquad,
    cap: usize,
    buf: VecDeque<f64>,
}

impl Bandpower {
    pub fn new(sos: &[Vec<f64>], window_samples: usize) -> Self {
        let cap = window_samples.max(1);
        Bandpower { filter: Biquad::new(sos), cap, buf: VecDeque::with_capacity(cap) }
    }
}

impl Stage for Bandpower {
    fn process(&mut self, input: Signal) -> Signal {
        let filtered = self.filter.run(&input.into_real());
        let mut out = Vec::with_capacity(filtered.len());
        for &v in &filtered {
            let sq = v * v;
            if self.buf.len() == self.cap {
                self.buf.pop_front();
            }
            self.buf.push_back(sq);
            let mean = self.buf.iter().sum::<f64>() / self.buf.len() as f64;
            out.push(mean);
        }
        Signal::Real(out)
    }
}

/// `autocorr(lag, window)` — rolling lag-`k` Pearson autocorrelation over a
/// sliding window of `window_samples`. Emits one value per input sample; 0.0
/// until `lag + 2` samples accumulate (warm-up) and 0.0 for a constant window
/// (zero variance, avoiding NaN). Output in [-1, 1]. Mirrors `AutocorrImpl`
/// byte-for-byte (same buffer eviction, mean-centering, and summation order)
/// — the critical-slowing-down early-warning indicator.
pub struct Autocorr {
    cap: usize,
    lag: usize,
    buf: VecDeque<f64>,
}

impl Autocorr {
    pub fn new(window_samples: usize, lag_samples: usize) -> Self {
        let cap = window_samples.max(1);
        Autocorr { cap, lag: lag_samples.max(1), buf: VecDeque::with_capacity(cap) }
    }

    fn lag_autocorr(&self) -> f64 {
        let n = self.buf.len();
        if n < self.lag + 2 {
            return 0.0;
        }
        let mean = self.buf.iter().sum::<f64>() / n as f64;
        let d: Vec<f64> = self.buf.iter().map(|&v| v - mean).collect();
        let den: f64 = d.iter().map(|&v| v * v).sum();
        if den == 0.0 {
            return 0.0;
        }
        let mut num = 0.0;
        for i in self.lag..n {
            num += d[i] * d[i - self.lag];
        }
        (num / den).clamp(-1.0, 1.0)
    }
}

impl Stage for Autocorr {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let mut out = Vec::with_capacity(x.len());
        for &v in &x {
            if self.buf.len() == self.cap {
                self.buf.pop_front();
            }
            self.buf.push_back(v);
            out.push(self.lag_autocorr());
        }
        Signal::Real(out)
    }
}

/// `np.percentile(..., method="linear")` over the current buffer.
///
/// Uses selection (`select_nth_unstable`), not a full sort, to match NumPy's
/// introselect complexity — O(n) per call rather than O(n log n). With
/// `frac > 0` the upper order statistic is `lo+1`, i.e. the minimum of the
/// partition above `lo`, so no second selection is needed.
fn percentile_linear(buf: &VecDeque<f64>, pct: f64) -> f64 {
    let n = buf.len();
    if n == 1 {
        return *buf.front().unwrap();
    }
    let mut a: Vec<f64> = buf.iter().copied().collect();
    let cmp = |x: &f64, y: &f64| x.partial_cmp(y).expect("NaN in percentile buffer");
    let rank = pct / 100.0 * (n as f64 - 1.0);
    let lo = rank.floor() as usize;
    let frac = rank - lo as f64;
    a.select_nth_unstable_by(lo, cmp);
    let lo_v = a[lo];
    if frac == 0.0 {
        return lo_v;
    }
    let hi_v = a[lo + 1..].iter().copied().fold(f64::INFINITY, f64::min);
    lo_v + (hi_v - lo_v) * frac
}

/// `coherence(input_a, input_b, band, window)` — band-averaged
/// magnitude-squared coherence between two streams via streaming Welch's
/// method (mirrors `CoherenceImpl`). Two bounded buffers hold the trailing
/// `window_samples` of each input; per chunk both buffers are extended, and
/// once `>= nperseg + (nperseg - noverlap)` samples are present a single MSC
/// scalar is computed and broadcast across the chunk. During warm-up the
/// scalar is 0.0 (matching `CoherenceImpl`'s "return 0.0 until enough
/// samples" convention). The Welch math lives in `WelchMsc`.
pub struct Coherence {
    welch: WelchMsc,
    cap: usize,
    min_samples: usize,
    buf_a: VecDeque<f64>,
    buf_b: VecDeque<f64>,
}

impl Coherence {
    pub fn new(
        sample_rate_hz: f64,
        nperseg: usize,
        noverlap: usize,
        window_samples: usize,
        band: (f64, f64),
    ) -> Self {
        let cap = window_samples.max(1);
        Coherence {
            welch: WelchMsc::new(sample_rate_hz, nperseg, noverlap, band),
            cap,
            min_samples: nperseg + (nperseg - noverlap),
            buf_a: VecDeque::with_capacity(cap),
            buf_b: VecDeque::with_capacity(cap),
        }
    }

    /// Append both chunks, then emit the per-chunk-constant MSC across `n`
    /// samples (`n == a.len() == b.len()`).
    pub fn step(&mut self, a: &[f64], b: &[f64]) -> Vec<f64> {
        let n = a.len();
        for &v in a {
            if self.buf_a.len() == self.cap {
                self.buf_a.pop_front();
            }
            self.buf_a.push_back(v);
        }
        for &v in b {
            if self.buf_b.len() == self.cap {
                self.buf_b.pop_front();
            }
            self.buf_b.push_back(v);
        }
        if self.buf_a.len() < self.min_samples {
            return vec![0.0; n];
        }
        let va: Vec<f64> = self.buf_a.iter().copied().collect();
        let vb: Vec<f64> = self.buf_b.iter().copied().collect();
        let msc = self.welch.band_msc(&va, &vb);
        vec![msc; n]
    }
}

/// `dwell(condition, duration)` rising-edge / holds state machine (mirrors
/// `DwellImpl`): a streak that increments while the condition holds and
/// resets otherwise; `holds` once the streak reaches `dwell_samples`, with an
/// `event` on each rising edge of holding. State persists across chunks.
pub struct Dwell {
    dwell_samples: usize,
    streak: usize,
    was_holding: bool,
}

impl Dwell {
    pub fn new(dwell_samples: usize) -> Self {
        Dwell { dwell_samples: dwell_samples.max(1), streak: 0, was_holding: false }
    }

    pub fn set_duration_and_reset(&mut self, dwell_samples: usize) {
        self.dwell_samples = dwell_samples.max(1);
        self.reset();
    }

    pub fn reset(&mut self) {
        self.streak = 0;
        self.was_holding = false;
    }

    /// Returns `(events, holds)`.
    pub fn step(&mut self, cond: &[bool]) -> (Vec<bool>, Vec<bool>) {
        let n = cond.len();
        let mut events = vec![false; n];
        let mut holds = vec![false; n];
        for i in 0..n {
            if cond[i] {
                self.streak += 1;
            } else {
                self.streak = 0;
            }
            let holding = self.streak >= self.dwell_samples;
            holds[i] = holding;
            if holding && !self.was_holding {
                events[i] = true;
            }
            self.was_holding = holding;
        }
        (events, holds)
    }
}

/// Event latch that requires a continuous false interval before rearming.
pub struct DwellRearm {
    dwell_samples: usize,
    rearm_samples: usize,
    streak: usize,
    out_streak: usize,
    armed: bool,
}

impl DwellRearm {
    pub fn new(dwell_samples: usize, rearm_samples: usize) -> Self {
        Self {
            dwell_samples: dwell_samples.max(1),
            rearm_samples: rearm_samples.max(1),
            streak: 0,
            out_streak: 0,
            armed: true,
        }
    }

    pub fn reset(&mut self) {
        self.streak = 0;
        self.out_streak = 0;
        self.armed = true;
    }

    pub fn set_duration_and_reset(&mut self, dwell_samples: usize) {
        self.dwell_samples = dwell_samples.max(1);
        self.reset();
    }

    pub fn step(&mut self, cond: &[bool]) -> Vec<bool> {
        let mut events = vec![false; cond.len()];
        for (i, &on) in cond.iter().enumerate() {
            if on {
                self.streak += 1;
                self.out_streak = 0;
                if self.streak >= self.dwell_samples && self.armed {
                    events[i] = true;
                    self.armed = false;
                }
            } else {
                self.streak = 0;
                self.out_streak += 1;
                if self.out_streak >= self.rearm_samples {
                    self.armed = true;
                }
            }
        }
        events
    }
}

#[cfg(test)]
mod tests {
    use super::{control_cell, Biquad, Percentile, Signal, Smooth, Stage};

    #[test]
    fn ingest_skips_nonfinite_and_value_at_matches() {
        let mut p = Percentile::new(70.0, 10);
        p.ingest(&[1.0, 2.0, f64::NAN, 3.0, f64::INFINITY, 4.0]);
        assert_eq!(p.n_eff(), 4);
        // linear percentile of [1,2,3,4] at 70 == 3.1 (NumPy 'linear')
        assert!((p.value_at(70.0) - 3.1).abs() < 1e-9);
    }

    #[test]
    fn biquad_warm_starts_so_a_standing_offset_reads_zero() {
        // butter(4, [0.05/7.5, 5.0/7.5], btype="band", output="sos") at 15 Hz
        // — the 0.05 Hz high-pass whose cold-start ringing ran for 37 s on a
        // constant input, as measured for #93.
        #[rustfmt::skip]
        let sos = vec![
            vec![0.22715093943767742, 0.45430187887535484, 0.22715093943767742,
                 1.0, 0.5460107373802591, 0.1094037376799148],
            vec![1.0, 2.0, 1.0, 1.0, 0.7508908646387135, 0.5057096314747951],
            vec![1.0, -2.0, 1.0, 1.0, -1.9612785066793454, 0.9617177779874133],
            vec![1.0, -2.0, 1.0, 1.0, -1.9837978083289831, 0.9842344985346861],
        ];
        let mut f = Biquad::new(&sos);
        let y = f.process(Signal::Real(vec![33.0; 900])).into_real();
        let peak = y.iter().cloned().fold(0.0f64, |m, v| m.max(v.abs()));
        assert!(peak < 1e-9, "bandpass rang to {peak} on a 33.0 standing level");
    }

    #[test]
    fn smooth_warm_starts_to_the_first_sample() {
        let mut s = Smooth::from_cell(control_cell(0.001));
        let y = s.process(Signal::Real(vec![33.0; 128])).into_real();
        assert!(
            y.iter().all(|v| (v - 33.0).abs() < 1e-12),
            "smooth ramped from 0: y[0] = {}",
            y[0]
        );
    }
}
