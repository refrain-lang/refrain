//! Streaming DSP stages and stateful trackers.
//!
//! Coefficients are baked by the Python front-end (`refrain.ir_json`), so
//! these stages only *run* the deterministic recurrences/convolutions — no
//! filter design here. Each recurrence is written to match the exact state
//! convention of the SciPy routine the Python evaluator uses, so outputs
//! agree to floating-point tolerance.

use std::collections::VecDeque;

use crate::coherence::WelchMsc;

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
}

/// IIR bandpass/bandpower as a cascade of second-order sections, in
/// Direct-Form-II-Transposed — matching `scipy.signal.sosfilt`'s per-section
/// state recurrence and its `(n_sections, 2)` state layout.
pub struct Biquad {
    sos: Vec<[f64; 6]>, // [b0, b1, b2, a0, a1, a2] per section (a0 == 1)
    z1: Vec<f64>,
    z2: Vec<f64>,
}

impl Biquad {
    pub fn new(sos: &[Vec<f64>]) -> Self {
        let sections: Vec<[f64; 6]> = sos
            .iter()
            .map(|s| [s[0], s[1], s[2], s[3], s[4], s[5]])
            .collect();
        let n = sections.len();
        Biquad { sos: sections, z1: vec![0.0; n], z2: vec![0.0; n] }
    }

    fn run(&mut self, x: &[f64]) -> Vec<f64> {
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
/// with the baked coefficient `a` and zero initial state, matching
/// `SmoothImpl` (lfilter with `zi = state*(1-a)`, state starting at 0).
pub struct Smooth {
    alpha: f64,
    prev: f64,
}

impl Smooth {
    pub fn new(alpha: f64) -> Self {
        Smooth { alpha, prev: 0.0 }
    }
}

impl Stage for Smooth {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let mut out = vec![0.0; x.len()];
        for (slot, &v) in out.iter_mut().zip(&x) {
            let y = self.alpha * v + (1.0 - self.alpha) * self.prev;
            self.prev = y;
            *slot = y;
        }
        Signal::Real(out)
    }
}

/// `percentile(target_pct, window)` — rolling-window percentile tracker
/// (mirrors `PercentileImpl`): a bounded buffer of the last `window` samples;
/// per sample append, then NumPy-`linear` percentile over the current buffer.
pub struct Percentile {
    target_pct: f64,
    cap: usize,
    buf: VecDeque<f64>,
}

impl Percentile {
    pub fn new(target_pct: f64, window_samples: usize) -> Self {
        let cap = window_samples.max(1);
        Percentile { target_pct, cap, buf: VecDeque::with_capacity(cap) }
    }

    pub fn step(&mut self, x: &[f64]) -> Vec<f64> {
        let mut out = Vec::with_capacity(x.len());
        for &v in x {
            if self.buf.len() == self.cap {
                self.buf.pop_front();
            }
            self.buf.push_back(v);
            out.push(percentile_linear(&self.buf, self.target_pct));
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
}

impl AutoRange {
    pub fn new(window_samples: usize, low_pct: f64, high_pct: f64) -> Self {
        let cap = window_samples.max(1);
        AutoRange { low_pct, high_pct, cap, buf: VecDeque::with_capacity(cap) }
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
            let low = percentile_linear(&self.buf, self.low_pct);
            let high = percentile_linear(&self.buf, self.high_pct);
            let span = (high - low).max(1e-9);
            out.push(((v - low) / span).clamp(0.0, 1.0));
        }
        Signal::Real(out)
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
