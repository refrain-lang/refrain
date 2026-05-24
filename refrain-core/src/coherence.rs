//! Streaming Welch magnitude-squared coherence (MSC).
//!
//! Matches `scipy.signal.coherence(a, b, fs, nperseg, noverlap,
//! window="hann")` as used by `CoherenceImpl`: the **periodic** Hann window
//! (`0.5 - 0.5*cos(2*pi*n/nperseg)`, n=0..nperseg-1), per-segment constant
//! detrend (subtract the segment mean), segments stepped by
//! `nperseg - noverlap`, one-sided density-scaled periodograms, and
//! `MSC = |mean(Pxy)|^2 / (mean(Pxx) * mean(Pyy))` averaged over a frequency
//! band. The density scaling `1/(fs * sum(win^2))` cancels in the ratio but is
//! kept for fidelity. The real FFT comes from the `realfft` crate (no
//! hand-rolled transform).

use std::sync::Arc;

use realfft::{RealFftPlanner, RealToComplex};

/// Band-averaged Welch MSC over two equal-length buffers.
///
/// Holds the FFT plan + scratch so per-call evaluation does no extra
/// allocation of the plan. Stateless across calls otherwise; the rolling
/// buffers live in the `Coherence` tracker (`dsp.rs`).
pub struct WelchMsc {
    fs: f64,
    nperseg: usize,
    noverlap: usize,
    window: Vec<f64>,
    win_sumsq: f64,
    /// Inclusive frequency-bin indices `[lo, hi]` covering the band.
    band_lo_idx: usize,
    band_hi_idx: usize,
    band_empty: bool,
    fft: Arc<dyn RealToComplex<f64>>,
}

impl WelchMsc {
    pub fn new(fs: f64, nperseg: usize, noverlap: usize, band: (f64, f64)) -> Self {
        // Periodic Hann, matching scipy.signal.get_window("hann", nperseg).
        let window: Vec<f64> = (0..nperseg)
            .map(|n| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * n as f64 / nperseg as f64).cos())
            .collect();
        let win_sumsq: f64 = window.iter().map(|w| w * w).sum();

        // One-sided frequency bins: f[k] = k * fs / nperseg, k = 0..nperseg/2.
        let nfreq = nperseg / 2 + 1;
        let (low, high) = band;
        let mut band_lo_idx = nfreq;
        let mut band_hi_idx = 0usize;
        let mut band_empty = true;
        for k in 0..nfreq {
            let f = k as f64 * fs / nperseg as f64;
            if f >= low && f <= high {
                if band_empty {
                    band_lo_idx = k;
                    band_empty = false;
                }
                band_hi_idx = k;
            }
        }

        let mut planner = RealFftPlanner::<f64>::new();
        let fft = planner.plan_fft_forward(nperseg);

        WelchMsc {
            fs,
            nperseg,
            noverlap,
            window,
            win_sumsq,
            band_lo_idx,
            band_hi_idx,
            band_empty,
            fft,
        }
    }

    /// Compute the band-averaged MSC for the two buffers. Returns a scalar in
    /// `[0, 1]`; NaN/inf bins (constant or zero-power segments) are coerced to
    /// 0, matching `np.nan_to_num`, and the band mean is clipped to `[0, 1]`.
    pub fn band_msc(&self, a: &[f64], b: &[f64]) -> f64 {
        if self.band_empty {
            return 0.0;
        }
        let nperseg = self.nperseg;
        let step = nperseg - self.noverlap;
        let nfreq = nperseg / 2 + 1;

        let mut pxx = vec![0.0f64; nfreq];
        let mut pyy = vec![0.0f64; nfreq];
        let mut pxy_re = vec![0.0f64; nfreq];
        let mut pxy_im = vec![0.0f64; nfreq];

        // Density scaling: 1 / (fs * sum(win^2)).
        let scale = 1.0 / (self.fs * self.win_sumsq);

        let mut seg_a = vec![0.0f64; nperseg];
        let mut seg_b = vec![0.0f64; nperseg];
        let mut spec_a = self.fft.make_output_vec();
        let mut spec_b = self.fft.make_output_vec();
        let mut scratch = self.fft.make_scratch_vec();

        let mut nseg = 0usize;
        let mut start = 0usize;
        let n = a.len();
        while start + nperseg <= n {
            // Detrend "constant": subtract per-segment mean, then window.
            let mean_a: f64 = a[start..start + nperseg].iter().sum::<f64>() / nperseg as f64;
            let mean_b: f64 = b[start..start + nperseg].iter().sum::<f64>() / nperseg as f64;
            for i in 0..nperseg {
                seg_a[i] = (a[start + i] - mean_a) * self.window[i];
                seg_b[i] = (b[start + i] - mean_b) * self.window[i];
            }

            self.fft
                .process_with_scratch(&mut seg_a, &mut spec_a, &mut scratch)
                .expect("rfft a");
            self.fft
                .process_with_scratch(&mut seg_b, &mut spec_b, &mut scratch)
                .expect("rfft b");

            for k in 0..nfreq {
                let (ar, ai) = (spec_a[k].re, spec_a[k].im);
                let (br, bi) = (spec_b[k].re, spec_b[k].im);
                // One-sided density: 2x for non-edge bins (DC + Nyquist single).
                let two = if k == 0 || k == nfreq - 1 { 1.0 } else { 2.0 };
                let s = scale * two;
                pxx[k] += (ar * ar + ai * ai) * s;
                pyy[k] += (br * br + bi * bi) * s;
                // Pxy = conj(X) * Y, matching scipy's csd(x, y).
                pxy_re[k] += (ar * br + ai * bi) * s;
                pxy_im[k] += (ar * bi - ai * br) * s;
            }
            nseg += 1;
            start += step;
        }

        if nseg == 0 {
            return 0.0;
        }

        // Band-average the per-bin MSC = |mean(Pxy)|^2 / (mean(Pxx)*mean(Pyy)).
        // Dividing the accumulated sums by nseg cancels in the ratio, so the
        // raw sums suffice for the per-bin coherence.
        let mut acc = 0.0f64;
        let mut count = 0usize;
        for k in self.band_lo_idx..=self.band_hi_idx {
            let num = pxy_re[k] * pxy_re[k] + pxy_im[k] * pxy_im[k];
            let den = pxx[k] * pyy[k];
            let c = num / den;
            // np.nan_to_num: NaN/inf -> 0.
            acc += if c.is_finite() { c } else { 0.0 };
            count += 1;
        }
        let msc = acc / count as f64;
        msc.clamp(0.0, 1.0)
    }
}
