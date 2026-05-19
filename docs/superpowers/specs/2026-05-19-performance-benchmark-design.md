# Refrain Performance Benchmark Suite — Design

**Date:** 2026-05-19
**Status:** Design, pre-implementation
**Scope:** Full tiered benchmark suite for the Refrain reference evaluator

## 1. Problem

Refrain is a high-level description language for clinical neurofeedback protocols. A reasonable objection from prospective hosts and reviewers is:

> "A high-level DSL must be slow. Why not write the pipeline directly in numpy?"

Today the project has no published answer to this. The pitch — reproducibility, auditability, executable specs — is undermined if the runtime cost is unknown or unfavorable. We need a defensible, reproducible, public answer in the form of measurements anyone can re-run.

This document specifies a benchmark suite that produces that answer.

## 2. Goals and non-goals

**Goals**

- Quantify the runtime overhead Refrain adds over a hand-written numpy/scipy equivalent ("DSL tax"), as a function of chunk size, on a synthetic constrained-CPU floor and at least one realistic deployment-class machine.
- Quantify real-time headroom (CPU time per chunk ÷ chunk wall-clock duration) across realistic protocols.
- Quantify per-chunk latency distribution (P50/P95/P99/P99.9) for closed-loop suitability.
- Define operationally when a protocol becomes "too complex" for a given hardware tier.
- Publish the data and the harness in the repo so any third party can reproduce.
- Gate CI against regressions on a fast micro-subset.

**Non-goals**

- Optimizing the evaluator. This suite measures; optimization is a separate effort informed by the measurements.
- GPU or vectorization work. The evaluator is numpy-on-CPU; benchmarks measure that surface only.
- Comparison against other neurofeedback platforms (BioEra, BCI2000, etc.). Apples-to-apples is impractical and politically fraught; we compare Refrain against hand-written numpy doing the same work.
- Clinical validation. Performance benchmarks say nothing about clinical efficacy.

## 3. Metrics

Four metrics, each addressing one strand of the performance objection.

| Metric | Definition | Threshold of concern |
|---|---|---|
| **DSL tax** | `t_refrain / t_baseline` per protocol, as a curve over chunk size | Tax > 2× at chunk ≥ 64 samples → investigate |
| **Real-time factor (RTF)** | `cpu_time_per_chunk / chunk_wall_duration`, P99 | RTF P99 ≥ 0.5 → near budget; ≥ 1.0 → cannot keep up |
| **Per-chunk latency** | Wall-clock time inside `step_chunk()`, P50/P95/P99/P99.9 | P99.9 > 2× P50 → jitter problem |
| **Complexity ceiling** | Maximum protocol complexity score (§7) at which RTF P99 < 0.5 on tier T | Reported per tier, not gated |

DSL tax is the headline number for §1's objection. The other three are operational.

## 4. Hardware tiers

| Tier | Hardware | Role |
|---|---|---|
| **A (Synthetic floor)** | Tier-B hardware pinned to one core at 1.5 GHz via `taskset -c 0` + `cpupower frequency-set -u 1500MHz` (Linux) or equivalent (macOS: cpulimit + qos) | Reproducible constrained-budget floor; runnable on any contributor's laptop and on free GitHub-hosted CI runners |
| **B (Clinical)** | x86-64 laptop, 4-core/8-thread, no GPU acceleration, Ubuntu LTS, governor=performance | Realistic clinical deployment |
| **C (Workstation)** | Developer machine (specifics captured per run) | Upper bound; sanity check |
| **D (Real ARM)** *optional, deferred* | Raspberry Pi 5, 8 GB, active cooling, Raspberry Pi OS Lite 64-bit | One real ARM datapoint for architectural diversity; not the floor |

**Rationale for synthetic Tier A over a physical SBC:**

- **Reproducibility matches the project's posture.** Refrain's pitch is "anyone with the file can re-run the protocol." Benchmark reproducibility should follow the same rule: a reader runs a script, not a procurement order.
- **No actual clinical deployment of Refrain targets a Pi.** The real surfaces are Windows clinical PCs (= Tier B), tablets paired over BLE, and commercial NF embedded SoCs (RK3588, NXP, Qualcomm — none Pi-like). A Pi 4 floor would be arbitrarily low rather than meaningfully conservative.
- **CI gates can run on free GitHub-hosted runners** by applying the same throttle config, removing the operational debt of self-hosted ARM runners.
- **No thermal-throttle confound.** Pinning frequency by governor sidesteps the largest reproducibility risk of an SBC under sustained DSP load.

The throttle config (core mask, frequency cap, governor) is part of the run record and version-controlled in `bench/harness/tier_a_throttle.sh`. Changes to that script are treated as breaking and bumped explicitly in `bench/results/_ci_baseline.json`.

**Tier D rationale:** included as a deferred optional tier so the suite can answer "does anything weird happen on ARM in stock numpy/OpenBLAS-NEON" if a host application requests it. Not required for v1 deliverables.

**Skipped tiers and why:** Jetson (GPU irrelevant for small-chunk EEG workload); bespoke commercial SBCs (procurement and reproducibility); phones and tablets (cannot pin governors or kill background load cleanly under stock OS).

**Mandatory per-run environment capture:** CPU model, governor setting, frequency cap, core count and affinity mask, RAM, OS version, Python version, numpy version, scipy version, BLAS implementation, temperature samples during the run, git SHA of Refrain, throttle script SHA. Runs where the governor or frequency drifted from the configured value are flagged and excluded from headline numbers.

## 5. Sweep dimensions and default operating point

| Dimension | Values |
|---|---|
| Sample rate | 256, 512, 1024, 2048 Hz |
| Channel count | 1, 2, 4, 8, 19, 32, 64 |
| Chunk size | 8, 16, 32, 64, 128, 256 samples |
| Protocol | Microbench ladder + realistic + stress (see §6) |
| Tier | A (synthetic floor), B, C; optionally D |

Cross-product is too large. Default operating point: **256 Hz, 1 channel, 32-sample chunks, `realistic_smr` protocol**. Sweeps vary one axis at a time from this point.

Why this default: matches the existing `examples/smr_cz.refrain`, lets future measurements compare against the project's canonical workload, keeps the matrix tractable.

## 6. Protocol corpus

```
bench/protocols/
  micro_01_passthrough.refrain        # input only, no DSP
  micro_02_bandpass.refrain           # bandpass only
  micro_03_envelope.refrain           # bandpass → hilbert → magnitude → smooth
  micro_04_threshold.refrain          # envelope + percentile threshold
  micro_05_reward.refrain             # envelope + threshold + sigmoid reward
  micro_06_coherence_1pair.refrain    # single coherence pair (FFT path)
  realistic_smr.refrain               # copy of examples/smr_cz.refrain
  realistic_othmer.refrain            # copy of examples/othmer_ilf_cz_pz.refrain
  realistic_alpha_theta.refrain       # copy of examples/alpha_theta.refrain
  realistic_coherence_4pair.refrain   # multi-pair coherence
  stress_N_derives.refrain.j2         # jinja template, parameterized by N
```

The micro ladder isolates one feature per step so DSL tax can be attributed. The realistic set reflects clinical workloads. The stress template drives the complexity ceiling sweep (§7).

## 7. Complexity score and ceiling

"Too complex" is defined operationally, not by feel. Score formula:

```
score = 1·|inputs|
      + 2·|derives|
      + 3·|coherence_pairs|       # FFT-heavy
      + 1·|thresholds|
      + 1·|inhibits|
      + 0.5·|percentile_windows_normalized|
```

`percentile_windows_normalized = sum(window_sec × sample_rate) / 60_000`, so a single 2-min @ 256 Hz window contributes ~0.5.

Weights are **calibrated from microbench timing**, not theoretical. Calibration procedure: run each micro_NN at the default operating point on Tier-B, regress per-chunk time against feature counts, adjust weights so each unit of score contributes roughly equal CPU. Document the calibration run in `bench/results/`.

**Complexity ceiling output:** per (tier, sample_rate, chunk_size) tuple, the max complexity score at which RTF P99 < 0.5. Generated by sweeping `stress_N_derives.refrain` from N=1 upward until RTF crosses the threshold. Published as a table in `docs/PERFORMANCE.md`.

## 8. Baselines (DSL tax denominator)

Three baseline kinds. Build (a) and (c) for v1; defer (b).

| Label | Source | Purpose |
|---|---|---|
| **(a) Idiomatic** | Hand-written `bench/baselines/<protocol>_idiomatic.py` using `scipy.signal.butter`/`sosfilt`, streaming FIR Hilbert, deque-based percentile, manual EWMA | Public-facing DSL tax: "what you get if you skip Refrain and write it yourself" |
| **(c) IR-transpiled** | Auto-generated by walking the IR and emitting flat numpy that calls the same primitive code without `Evaluator` dispatch | Engineering-direction number: isolates dispatch + tap + event-machinery overhead |
| **(b) Hand-tuned** | Future work | Ceiling for hostile reviewers |

**Why (c) is cheap to build:** the IR already exists; a ~300-line transpiler emits an equivalent `.py`. The `(refrain_time − transpiled_time)` delta is exactly the runtime framing cost — directly answers "would AOT-compiling Refrain help?"

**Why (b) is deferred:** it's a tar pit (sosfilt vs. lfilter, batched FFTs, numba, …), reviewers rarely demand it once (a) is in hand, and the (a) curve is what readers actually care about.

### Numerical equivalence is mandatory

A tax number is only credible if `refrain_output ≡ baseline_output` on identical input. Without this, any reviewer can claim the baseline is computing something simpler.

For each (protocol, baseline) pair:

1. Run both on deterministic synthetic input (seeded pink noise + simulated SMR bursts, see §9).
2. Discard the first `max(warmup_samples, 2·max_filter_order)` samples — filter transients differ in early samples.
3. Assert `np.allclose(refrain_out, baseline_out, atol=1e-9, rtol=1e-6)` on the steady-state tail.
4. **CI gates this assertion before any timing is reported.** A baseline that is faster but computes something different is worse than no baseline at all.

The transpiled (c) baseline is equivalent by construction (same primitive code paths). The idiomatic (a) baseline takes manual care: e.g., Refrain's `hilbert()` is streaming FIR, so the (a) baseline must also be streaming FIR — `scipy.signal.hilbert` (FFT, full-signal) is not equivalent. Equivalence mismatches are where benchmark fraud usually hides; making them explicit and gated is the whole point.

### DSL tax is a curve, not a scalar

The tax is approximately constant overhead per chunk per primitive. Therefore:

- At 256-sample chunks, the overhead amortizes over many samples → small percentage.
- At 8-sample chunks (low-latency mode), overhead dominates → large percentage.

Reporting a single number is misleading. Report as a curve over chunk size, one chart per realistic protocol per tier, with three lines: Refrain, IR-transpiled (c), idiomatic numpy (a). The curve is the answer; pick your operating point and read off the cost.

## 9. Harness

```
bench/harness/
  signal_gen.py        # deterministic pink noise + simulated SMR bursts, seeded
  runner.py            # warmup → steady-state window → per-chunk perf_counter_ns
  equivalence.py       # numerical equivalence checker, CI gate
  transpile.py         # IR → flat numpy .py emitter for baseline (c)
  report.py            # CSV/JSON → markdown tables + matplotlib charts
  env_capture.py       # CPU/governor/temp/lib versions snapshot
```

### Timing protocol

- `time.perf_counter_ns()` per chunk; store **every chunk's latency**, not just the mean. Do not use `pytest-benchmark`'s default aggregation — it discards the tail we care about.
- Warmup: discard the first `max(2 · longest_percentile_window_samples, 10 · sample_rate)` samples. Steady state must be reached before timing begins.
- Steady-state window: minimum 60 seconds of simulated signal, minimum 1000 chunks, whichever is greater.
- Repeat each (config, protocol) measurement 3 times across separate process invocations. Report median of the three P99 values; flag if max/min spread > 20 %.

### Signal generator

Deterministic. Pink noise (1/f) at configurable amplitude, plus optional synthetic SMR bursts at known times so reward-pipeline microbenches have something to trigger on. Seed is part of the run record; same seed produces identical input forever.

### Output format

```
bench/results/YYYY-MM-DD_<host>_<git-sha>/
  env.json             # captured environment
  raw/<config>.parquet # per-chunk latencies, one row per chunk
  summary.csv          # aggregated P50/P95/P99/P99.9/mean per config
  charts/*.png         # generated by report.py
  report.md            # human-readable summary
```

Parquet for raw per-chunk data because it compresses well and pandas reads it fast. CSV for the summary because it diff-reviews well.

## 10. CI integration

**Per-PR fast subset** (~30 s budget, runs on GitHub-hosted runner):

- 5 microbench protocols at default operating point.
- Equivalence assertions must pass; otherwise the PR fails before timing runs.
- Compare P99 per-chunk latency against the main-branch baseline stored in `bench/results/_ci_baseline.json`.
- **Gate:** > 10 % regression on any tracked config fails the PR. The threshold is intentionally loose to absorb runner variance; persistent regressions show up across multiple PRs.

**Nightly full sweep** on GitHub-hosted runners for Tier A (synthetic-throttled) and Tier B (untrottled host machine):

- Full protocol corpus × full sweep dimensions.
- Tier A runs apply the throttle script before timing; Tier B runs do not. Both run on the same GitHub-hosted runner SKU so the only methodological difference is the throttle.
- Results published to `bench/results/` and to a small static site (deferred to a follow-on; v1 ships the data only).
- Baseline file `_ci_baseline.json` is updated weekly by a scheduled job, not per-commit.

Tier C nightly runs are not automated — they require the maintainer's workstation and are captured ad hoc when a baseline refresh is taken. Tier D, when enabled, requires a self-hosted ARM runner; provisioning is explicitly out of scope for v1.

## 11. Deliverables

1. `bench/` directory with the structure above, all harness code, all protocol fixtures, all idiomatic baselines for the micro ladder and realistic protocols, and the IR transpiler.
2. CI workflow (`.github/workflows/bench.yml`) running the per-PR fast subset.
3. `docs/PERFORMANCE.md` containing:
   - Methodology summary (links to this design doc).
   - DSL-tax curves: one chart per realistic protocol per tier, three lines each.
   - RTF P99 table: rows = protocols, columns = (tier, sample_rate, chunk_size) operating points.
   - Per-chunk latency table: P50/P95/P99/P99.9 at default operating point per tier.
   - Complexity ceiling table: max score per (tier, sample_rate, chunk_size).
   - Equivalence audit pass/fail summary.
4. `README.md` performance section: one paragraph with the headline numbers and a link to `docs/PERFORMANCE.md`.
5. At least one full nightly run captured under `bench/results/` and committed.

## 12. Phases

| Phase | Scope | Done when |
|---|---|---|
| **P1 — Harness and equivalence** | `bench/harness/*`, signal generator, equivalence checker, IR transpiler. No tiers, no CI yet. Runs locally on Tier-C only. | Equivalence asserts pass for all micro + realistic protocols against (a) and (c) baselines. |
| **P2 — Local full matrix on Tier-C** | All metrics computed at default op point and one full sweep axis. `report.py` produces a markdown summary. | `docs/PERFORMANCE.md` exists with Tier-C numbers only. |
| **P3 — Tier-A (synthetic floor) and Tier-B measurements** | Implement the throttle script, run the full matrix on Tier A and Tier B, fold results into `docs/PERFORMANCE.md`. | Three tiers represented (A, B, C); throttle config version-controlled; governor/frequency logged per run. |
| **P4 — CI integration** | Per-PR fast subset wired into GitHub Actions; regression gate active. | A deliberate slowdown PR is rejected by the gate. |
| **P5 — Complexity ceiling sweep** | Calibrate the score weights; run the stress template; produce the ceiling table. | Ceiling table in `docs/PERFORMANCE.md`. |

Nightly self-hosted runners and the static-site report are explicitly deferred to a follow-on design.

## 13. Risks and open questions

- **GitHub-hosted runner variance.** Shared CI runners have noisy neighbors and can vary 10–30 % across runs. Mitigation: the per-PR gate threshold (10 %) is set deliberately loose; sustained regressions show up across multiple PRs. The nightly full sweep is the authoritative source, not per-PR runs.
- **Throttle config drift across OSes.** `cpupower` (Linux) and the macOS equivalents behave differently; some kernels silently ignore frequency caps if the requested value is below the firmware floor. Mitigation: `tier_a_throttle.sh` verifies the configured state after applying it (`cpupower frequency-info`) and aborts the run if the cap was not honored. The verification output is part of `env.json`.
- **OpenBLAS variance across distros.** Same numpy/scipy versions may use different BLAS builds with different SIMD paths, affecting Tier B in particular and Tier D (if enabled) acutely. Mitigation: `env.json` captures BLAS info; if variance exceeds what CPU class explains, pin a BLAS build per tier in the harness setup.
- **Idiomatic baseline drift.** Hand-written baselines can fall out of sync with primitive implementations as the evaluator evolves. Mitigation: equivalence assertions in CI catch drift immediately; baselines are reviewed alongside any primitive change.
- **Open: do we publish raw parquet to the repo, or just summaries?** Raw is ~10–100 MB per nightly run. Recommend: keep last 30 days of raw under `bench/results/`, archive older runs to a release artifact.
- **Open: who reviews the (a) baselines?** They are load-bearing — a bug in a baseline directly distorts the DSL tax number. Recommend: each baseline gets a second-pair-of-eyes review at PR time, with the equivalence test as the objective check.

## 14. What this design explicitly does not do

- Define hardware procurement budget or timeline.
- Specify the exact GitHub Actions YAML.
- Choose the static-site generator for nightly results.
- Optimize anything in the evaluator. The first commits land measurements only; any optimization work is a separate effort that this suite then validates.
