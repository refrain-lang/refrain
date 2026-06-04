# Response — HRV biofeedback feature request

**To:** Coherence Recorder
**From:** Refrain
**Re:** `recorder/docs/refrain-hrv-feature-request.md` (evaluated against v0.6.3)
**Lands in:** **v0.8.0** — move your pin from `@v0.6.3` to `@v0.8.0`.

Thanks for the unusually precise request — verifying against source before
filing made this fast. Everything below is **additive**: no existing protocol
changes behavior, the IR-JSON schema is unchanged, and the Rust core stays at
machine-precision parity with the Python evaluator. Bump your pin and opt in.

---

## Ask 2 — seed + export of adaptive state — **done** ✅ (the headline)

This was your "key longitudinal ask" and it's the cleanest win.

- **Export:** `Evaluator.export_state()` → a compact, rate-independent summary
  per stateful tracker, keyed `"<entity>.<callee>"`:
  ```jsonc
  {
    "rhythm_strength.auto_range": { "low": 0.0123, "high": 0.0481, "n_eff": 1200 },
    "rs_t.percentile":            { "value": 0.0402, "target_pct": 70, "n_eff": 1200 }
  }
  ```
- **Seed:** `Evaluator.live(ir, ..., seed_state=<prior export>)` re-primes the
  trackers at session start.
- **Persistence model:** this is *runtime* state, **not** IR — your protocol
  files and their IR-JSON are untouched. Persist the dict to the patient record;
  the ceiling you see in `auto_range.high` / `rs_t.percentile.value` is the
  per-user mastery signal that rises week to week.
- **Both backends, with parity.** `export_state()` and the `seed_state` ctor arg
  are exposed on the `refrain_core` wheel too; a cross-backend test pins
  Python↔Rust agreement to 1e-6. Seeding pre-fills the rolling window with a
  deterministic synthetic distribution that reproduces your anchors (it is the
  compact-summary contract you'd expect: continuity means *feedback is
  well-scaled from sample 1*, not bit-exact buffer restoration).
- Scope: `auto_range` and `percentile` (incl. percentile thresholds). `smooth`
  is fast-settling and out of scope.

One deferral to flag: the **Swift/Kotlin (uniffi) mobile** binding does **not**
expose seed/export yet — it's an additive follow-up. You consume the Python
`refrain_core` wheel, which has it, so this only matters if you later drive HRV
from the mobile core directly. Say the word if you need it sooner.

## Ask 4 — `passthrough()` montage — **done** ✅

First-class identity montage; drop the `referential(reference: "device")` hack:

```refrain
input "tachogram" {        // single non-EEG channel @ 4 Hz
  montage = passthrough()
}
```

Single-channel sources only (for multi-channel, name the channel with
`referential`/`bipolar`). Implemented in both backends with a parity fixture.

## Ask 1 — low-latency low-Fs envelope — **delivered as `rectify()+smooth()`**, and here's the honest story

Your request was *"any one of"* (1) implement `hilbert(kind="iir_allpass")`,
(2) low-`taps` guidance, or (3) confirm `rectify()+smooth()`. **We delivered
#3** — validated and documented as the sanctioned low-Fs envelope:

```refrain
derive "lf_env" {
  from = "tachogram"
  pipeline = [
    bandpass(band: (0.04 Hz, 0.15 Hz), order: 4),
    rectify(),
    smooth(tau: 4 s),   // low-latency LF envelope at 4 Hz
  ]
}
```

We chose this over your *preferred* #1 deliberately, and you should have the
reasoning:

- **`iir_allpass` won't help you, so we did not implement it** (the stub now
  raises a clear error pointing here). A low-latency two-all-pass IIR Hilbert is
  accurate only mid-band; near DC it collapses to ~0 dB image rejection. **Every
  real NF/HRV band sits near DC** at typical rates (your 0.04–0.15 Hz @4 Hz =
  2–7.5 % of Nyquist; EEG 4–20 Hz @256 Hz is similar), so it cannot produce a
  usable analytic envelope there. Raising the order doesn't fix near-DC accuracy
  and the group delay blows up. Full writeup: `docs/DESIGN-NOTES.md` §7a.
- **The `<1 s` latency target is physically unreachable for a 0.1 Hz rhythm**,
  by *any* causal method — its envelope only has meaning over ~half a cycle
  (~5 s). We measured it: `rectify+smooth(4s)` tracks the true envelope at
  corr ≈ 0.98; complex demodulation sits on the *same* latency/quality curve
  (marginally cleaner, never faster); the only 0-lag result is the **acausal**
  FFT-Hilbert, which can't run live. The real, achievable win is removing the
  *extra* 8 s of FIR group delay — and `rectify()` adds ~0 on top of the
  `smooth` your pipeline already budgets.

Net: your own interim path **is** the ceiling here. We've blessed it, documented
it (`docs/PRIMITIVES.md`, `hilbert` section), and validated it
(`tests/test_low_fs_envelope.py`). If you ever want marginally *cleaner*
(unbiased) envelopes — a quality, not latency, gain — a `demodulate(center,
bandwidth)` primitive is the natural future add; tell us if it's worth it.

## Ask 3 — multi-rate / multi-source inputs — **not in this release** (M2)

Out of scope by agreement: it's your M2 gate and the only ask touching a
breaking surface (`step_chunk` is single-rate; `fanout` caps at one input). It's
shared with the dual-device-sync project, so it gets its own spec and lands once
designed to keep the single-input path byte-identical. The `coherence` operator
it needs already exists.

---

## TL;DR for the recorder

| Ask | Status in v0.8.0 | Your action |
|---|---|---|
| 2 — seed/export | ✅ done (both backends, parity) | `export_state()` / `seed_state=`; persist the dict |
| 4 — passthrough | ✅ done | `montage = passthrough()` |
| 1 — low-Fs envelope | ✅ `rectify()+smooth()` (validated); `iir_allpass` won't ship (near-DC infeasible) | use `rectify→smooth`; drop the analytic signal |
| 3 — multi-rate inputs | ⏭ deferred to M2 (own spec) | n/a this cycle |

**HRV M1 ships "good" on v0.8.0.** Move the pin, and you're unblocked on the
longitudinal ceiling (Ask 2) with a clean tachogram input (Ask 4) and a
sanctioned envelope (Ask 1).
