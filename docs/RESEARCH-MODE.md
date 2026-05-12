# Refrain Research Mode

**Audience:** researchers planning CRED-nf-grade studies; implementers
of Refrain-compatible runtimes; reviewers auditing a study's allocation
concealment.

**Companion docs:** [`SPEC.md`](./SPEC.md) §4.1, §7.9 for the
language-level contract. [`EMBEDDING.md`](./EMBEDDING.md) for host-side
API usage.

This document is the deep dive on Refrain's research-mode operation —
the cryptographic protocol, the threat model, per-sham-type constant-
time guarantees, the sealed-token format spec, and test fixtures
implementations can use to claim conformance.

---

## 1. Why this exists

The 2020 CRED-nf consensus checklist (Ros et al., *Brain* 143(6))
identified the items NF studies historically fail to report. The
biggest of these — the one most NF software stacks have *no* built-in
support for — is allocation concealment for sham conditions.

In a properly blinded NF trial:
- The participant doesn't know whether they're in the real or sham arm.
- The clinician running the session doesn't know either (single
  blinding is insufficient — clinician expectations measurably affect
  outcomes).
- The data analyst doesn't know until the analysis plan is locked.
- Only the independent statistician holding the unblinding key knows,
  and only post-hoc.

Refrain's architecture — separating *compute signal* from *render
feedback* — makes this mechanically tractable in a way it isn't with
most clinical NF stacks. Refrain owns the signal transformation
between raw EEG and the patient's experience; it can substitute a
sham signal at that boundary without the rest of the host application
needing to know which arm the session is in.

This document specifies how.

---

## 2. The host-runtime contract

Research mode is implemented as a sealed-allocation protocol between
the host application (recorder) and the Refrain runtime (evaluator):

1. The host application owns acquisition, the patient renderer, the
   clinician UI, session storage, and study orchestration.
2. The Refrain runtime owns the signal pipeline and — in research mode
   — the randomization, the sham substitution, and the cryptographic
   sealing of the allocation decision.
3. An independent third party owns the X25519 keypair used to seal and
   unseal allocations. The public key is published with the study
   protocol; the private key is held in an unblinding vault.

The boundary is the `ChunkTransformer` abstraction (SPEC §7.9.1). When
research mode is active, every EEG chunk passes through a transformer
chosen at session-start by Refrain's randomization. The host hands
Refrain the raw chunk; Refrain's evaluator processes the transformed
chunk; output events and tap values reflect the transformed signal.
The clinician's observation window plots what the patient is
experiencing, not the raw EEG.

---

## 3. Sham types — what each preserves and destroys

The reference implementation ships three sham transformers. Each has
a specific methodological niche.

### 3.1 `TimeShiftedSelf(delay_s)`

**What it does.** Buffers the most recent `delay_s` seconds of input.
On each `step()` call, returns the chunk from `delay_s` ago instead
of the current chunk. The patient experiences feedback derived from
their own EEG, but offset in time.

**What it preserves.**
- Spectral content (identical PSD)
- Artifact morphology (eyeblinks, EMG, etc. — they show up, just
  at the wrong time)
- Channel-to-channel relationships
- Amplitude distribution and dynamic range

**What it destroys.**
- Temporal correlation with the patient's current state (the whole
  point — feedback no longer responds to what they're doing *now*)

**Constant-time profile.** Pure indexed buffer read. Per-chunk cost
is `O(chunk_size)` regardless of input content. No detectable timing
side channel.

**Memory footprint.** `delay_s × sample_rate × n_channels × 8 bytes`.
For 30 s at 250 Hz × 4 channels: ~240 kB. Trivial.

**Methodological notes.**
- Standard sham in operant NF research; popular because it preserves
  the "feels real" properties of the signal (artifacts in particular).
- The `delay_s` must exceed any feedback-loop time constant in the
  protocol. For SMR Cz with 250 ms dwell, 30 s is safely far past.
- Patients with very stable state may not notice the substitution;
  patients with rapidly varying state will perceive a mismatch.
  Choose `delay_s` such that the autocorrelation of the relevant
  feature has decayed.

### 3.2 `PhaseScrambled(window_s)`

**What it does.** Buffers `window_s` of input; applies a windowed FFT;
randomizes the phase of every frequency component while preserving
magnitudes; inverse-FFTs; overlap-adds the result. The output has the
same power spectrum as the input but is incoherent in time.

**What it preserves.**
- Power spectral density within each `window_s` window
- Total signal power per band

**What it destroys.**
- Phase coherence (the dominant cue for "this is real EEG")
- Transient morphology (eyeblinks, spindles, spikes — all smeared)
- Cross-channel phase relationships

**Constant-time profile.** FFT is `O(N log N)` where `N = window_s ×
sample_rate`. Per-chunk cost is constant with respect to input
content (FFT cost depends on size, not on the signal itself). The
randomization seed is fixed at session start; same seed always
produces the same scrambling given the same input.

**Memory footprint.** `2 × window_s × sample_rate × n_channels × 8
bytes` (input buffer + overlap-add output buffer). For 10 s at 250 Hz
× 4 channels: ~160 kB. Trivial.

**Methodological notes.**
- Appropriate for amplitude-based NF (SMR, theta-beta, Othmer ILF,
  alpha-theta). The protocol's bandpass + Hilbert + magnitude
  pipeline produces meaningful values from phase-scrambled signal,
  but without any temporal structure the patient cannot control.
- **Inappropriate** for phase-based NF (coherence training,
  phase-locked stimulus paradigms, ERP-NF). Phase-scrambled signal
  is exactly the signal these protocols try to modulate; the sham
  is then indistinguishable-by-construction. Protocol authors should
  exclude `phase_scrambled` from `meta.sham_strategies` for such
  protocols.
- Window size matters. Too short and the scrambling becomes
  perceptible to the patient as choppiness; too long and the
  windowing artifacts at boundaries dominate. 10 s is a reasonable
  default for SMR-class protocols.

### 3.3 `YokedReplay(candidates)`

**What it does.** Ignores the live EEG entirely. Plays back one of
several pre-recorded sessions, chunk by chunk. Refrain picks which
candidate to use at session start and never tells the host. The
candidate set is host-provided (typically anonymized recordings from
control participants or earlier sessions).

**What it preserves.**
- Full structural realism of EEG (it *is* a real EEG recording)
- All inter-channel and temporal relationships of the chosen recording

**What it destroys.**
- Any link to the current patient's state

**Constant-time profile.** Per-chunk cost is `O(chunk_size)` — a
straight read from a pre-loaded array. No FFT or filtering. No
timing variation between candidates if all have the same channel
count and sample rate (which Refrain enforces at construction).

**Memory footprint.** The full duration of every candidate is loaded
at construction (the reference runtime preloads via `mne` /  `pyxdf`).
For 30 min × 4 channels × 250 Hz × 8 bytes × 5 candidates: ~70 MB.
Manageable.

**Methodological notes.**
- Considered the strongest sham for many designs because it removes
  the participant's self-correlation entirely while preserving every
  other property of real EEG.
- The candidate pool must be carefully chosen: too small and a
  motivated unblinder could identify the recording; too large and
  the study protocol grows unwieldy.
- Recordings from the same recording session as the study (same
  amplifier, same room, same time of day) are methodologically
  stronger than recordings from elsewhere, because environmental
  context is matched.

### 3.4 Composition

Sham types do not compose. A session is in exactly one condition,
and the condition uses exactly one transformer. `TimeShiftedSelf` ⊕
`YokedReplay` is not a thing in Refrain. If a study needs the
characteristics of both, the answer is a study-design discussion, not
a tool feature.

---

## 4. Sealed allocation — the cryptographic protocol

### 4.1 Cryptographic primitive

Refrain uses libsodium's `crypto_box_seal` (anonymous sender public-key
encryption over X25519 + XSalsa20-Poly1305). The Python reference
implementation depends on PyNaCl, which provides the bindings.

`crypto_box_seal` is the right primitive for this use case because:
- The sender (Refrain) has no long-term key to manage; each session
  uses an ephemeral keypair.
- The receiver (statistician) has a single long-term keypair.
- The ciphertext is integrity-protected (authenticated encryption).
- The ciphertext is anonymous: it does not reveal who sealed it.
- The ciphertext is small (~80 bytes per allocation, plus the
  plaintext length).

The host receives the sealed token as an opaque `bytes` value. To
decrypt, the holder of the matching private key calls
`crypto_box_seal_open`. There is no other way to recover the plaintext;
the seal cannot be opened by guessing keys, replaying tokens, or
inspecting the ciphertext.

### 4.2 Sealed-plaintext format

```json
{
  "version": 1,
  "condition": "real" | "sham",
  "sham_type": "time_shifted_self" | "phase_scrambled" | "yoked_replay" | null,
  "sham_params": { ... },
  "candidate_index": 0,
  "seed": "0x1f2e3d4c5b6a7980",
  "timestamp": "2026-05-12T14:23:11Z",
  "refrain_version": "0.0.5",
  "protocol_id": "smr_cz_brainbit_v1",
  "protocol_hash": "sha256:abc1234567..."
}
```

Field semantics:

| Field | Type | Required? | Meaning |
|---|---|---|---|
| `version` | int | always | Sealed-token format version. Currently `1`. |
| `condition` | string | always | `"real"` or `"sham"`. |
| `sham_type` | string \| null | always | One of the runtime's permitted sham type names; `null` when `condition` is `"real"`. |
| `sham_params` | object | always | Constructor arguments for the chosen sham, sufficient to reconstruct it. Empty `{}` when `condition` is `"real"`. |
| `candidate_index` | int | when sham | Index into the host's `candidates` list for the chosen sham. `-1` when `condition` is `"real"`. |
| `seed` | string | always | Hex-encoded 64-bit integer. The seed for any internal randomization (e.g. `PhaseScrambled`'s phase randomizer). Sufficient for deterministic re-run given the same input. |
| `timestamp` | string | always | ISO-8601 UTC timestamp of session start. |
| `refrain_version` | string | always | The runtime's version string (`refrain.__version__`). |
| `protocol_id` | string | always | The protocol's name (the `protocol "..."` declaration). |
| `protocol_hash` | string | always | `"sha256:" + hex(sha256(canonical_unparsed_resolved_ir))`. See §4.3. |

Implementations conforming to the v0.0.5 contract MUST emit exactly
this schema. Implementations MAY include additional fields under a
top-level `extensions` key without breaking conformance.

### 4.3 Protocol hash

The `protocol_hash` is computed as:

1. The runtime's IR-canonical-unparser emits the resolved IR (post-
   composition, post-resolution) as a deterministic string.
2. The string is UTF-8 encoded.
3. SHA-256 is applied.
4. The result is hex-encoded and prefixed with `"sha256:"`.

Hashing the *resolved* IR rather than the source file ensures:
- Composition (`extends`, `amend`, `remove`) is captured.
- Whitespace, comments, and source-file arrangement don't affect the
  hash.
- Two sessions with identical `protocol_hash` are mathematically
  guaranteed to have run the same computation.

The canonical-unparse format is specified by the reference
implementation in `src/refrain/unparser.py`. Cross-runtime
implementations claiming conformance MUST produce identical hashes
for identical resolved IRs; this is verified by test fixtures
distributed with the runtime (§7).

### 4.4 Randomization

The condition decision uses Python's `secrets` module (which delegates
to the operating system's cryptographic RNG):

```python
import secrets
session_seed = secrets.randbits(64)
rng = numpy.random.default_rng(session_seed)
is_sham = rng.random() < sham_probability
```

The `session_seed` is recorded in the sealed token. Reconstructing the
allocation from the sealed token is deterministic.

Implementations MAY accept a `random_seed` parameter for tests and
deterministic re-runs, but the default MUST be cryptographic
randomness from the OS.

---

## 5. Threat model

### 5.1 What the threat model defends against

| Threat | Defense |
|---|---|
| Clinician determines condition from event timing within a session | Within-session constant-time guarantee (mandatory, §6) |
| Clinician determines condition from log files or telemetry | Sealed token is opaque; output events identical between arms |
| Host application developer is bribed/coerced to reveal allocation | Host doesn't know the allocation; only the private-key holder can decrypt |
| Sealed token is tampered with post-hoc | `crypto_box_seal` is authenticated; tampering produces a decryption failure |
| Researcher accidentally peeks before unblinding | The token is encrypted; there is no "almost-decrypt" |
| Different sessions ran different protocols and this was hidden | `protocol_hash` is in every token; comparing hashes reveals divergence |

### 5.2 What the threat model does NOT defend against

| Non-threat | Why we don't defend |
|---|---|
| The participant determining condition through introspection | A study-design problem, not a tool problem. The shams are designed to be plausibly real; the rest is on the protocol writer. |
| Cross-session timing analysis aggregated over many runs | OPTIONAL. Defaults are within-session constant only. Strict mode (§6) addresses this. |
| The unblinding-vault key holder being compromised | This is the trust anchor of the system. If the private key is leaked, the study's blinding is broken — but no automated mechanism can prevent that. |
| Side channels external to Refrain (clinician shoulder-surfing the recorder UI, dotted-line conversations with the IRB, etc.) | Out of scope. Host's clinician-blinding UI mode is host responsibility. |

### 5.3 Within-session vs cross-session constant time

The default constant-time guarantee is *within-session*: during a
single session, the per-chunk processing time, memory footprint, and
event timing distribution MUST NOT vary with the chosen condition.
This protects against the clinician determining the arm by observing
the patient's session in real time.

The default is **not** *cross-session* constant: real-mode sessions
and `PhaseScrambled` sessions have different per-chunk processing
costs (real mode skips the FFT; sham mode pays it). A researcher
running 100 sessions and aggregating timing measurements could
potentially distinguish condition prevalence by mean session
wall-clock time.

For most NF studies this is not a credible threat — the timing
difference is small (~3× CPU on a modern machine = <1 ms of wall-
clock difference per chunk over a 30-minute session) and the
methodological assumption is that blinding occurs *during* sessions,
not after their completion.

For studies where cross-session timing analysis is a credible
adversary, opt into strict mode:

```python
ShamConfig(..., strict_constant_time=True)
```

In strict mode, the evaluator instantiates *every* candidate
transformer, runs *every* transformer on each chunk, and selects the
output via a constant-time mux based on the (still-sealed) condition.
This costs more CPU but produces a chunk-by-chunk constant-time
guarantee across all conditions.

---

## 6. Constant-time guarantees per sham type

### 6.1 Within-session (default; mandatory)

| Sham type | Within-session guarantee |
|---|---|
| `TimeShiftedSelf` | Identical per-chunk cost to identity. Indexed buffer read; no input-dependent branching. |
| `PhaseScrambled` | FFT cost is `O(N log N)` per window regardless of input content. Per-chunk cost is constant. |
| `YokedReplay` | Per-chunk cost is identical regardless of which candidate was chosen, provided all candidates have the same `sample_rate_hz` and `n_channels` (enforced at `ShamConfig` construction). |

### 6.2 Cross-session (opt-in via `strict_constant_time=True`)

With strict mode, all four conditions (real, `time_shifted_self`,
`phase_scrambled`, `yoked_replay`) take the same per-chunk wall-clock
time: max over all candidates' per-chunk costs.

The constant-time mux that selects the actual output from the four
parallel computations is implemented using `numpy` masked-array
operations rather than `if condition == "real":` branching. This
ensures no branch-predictor side channel either.

---

## 7. Conformance test fixtures

Implementations claiming research-mode conformance MUST produce
identical sealed tokens (modulo `timestamp` and `seed`, which are
nondeterministic by design) when given identical inputs.

The reference implementation ships test fixtures at
`tests/fixtures/research_mode/`:

- `keypair.json` — a known X25519 keypair (NOT a real study key —
  test-only).
- `protocol.refrain` — a known small protocol.
- `expected_protocol_hash.txt` — the expected SHA-256 of the resolved
  IR.
- `sealed_token_*.bin` — a set of sealed tokens generated from
  known seeds.
- `expected_plaintext_*.json` — the expected decrypted plaintext for
  each token (with seed/timestamp redacted to known values).

To claim conformance, an implementation MUST:
1. Hash the test protocol to produce the same `protocol_hash`.
2. Decrypt the supplied sealed tokens with the supplied private key
   and produce the expected plaintext.
3. With the seed forced to the known value, regenerate the sealed
   token and produce a token that decrypts to the same plaintext.

---

## 8. Operational notes for researchers

### 8.1 Generating a study keypair

The unblinding vault should hold a keypair generated for this study
(do not reuse a keypair across studies):

```python
from nacl.public import PrivateKey
sk = PrivateKey.generate()
public_bytes = bytes(sk.public_key)  # publish in study protocol
private_bytes = bytes(sk)            # vault
```

### 8.2 Unblinding the study

When the analysis plan is locked and unblinding is approved:

```python
from refrain.research import open_sealed_token

for session_id, sealed_token in session_records:
    plaintext = open_sealed_token(sealed_token, private_bytes)
    print(session_id, plaintext["condition"], plaintext["sham_type"])
```

The resulting allocation matrix is then merged with the recorded EEG
data and outcome measures for analysis.

### 8.3 Verifying protocol integrity across sessions

Two sessions are guaranteed to have run the same computation iff
their sealed-token `protocol_hash` fields match. The study should
verify this for every session before analysis:

```python
hashes = {plaintext["protocol_hash"] for plaintext in all_plaintexts}
assert len(hashes) == 1, f"protocol drift detected: {hashes}"
```

### 8.4 Reproducibility from the sealed token

Given a session's sealed token and the corresponding raw recording
(or yoked-replay candidate), the entire session can be deterministically
replayed:

```python
plaintext = open_sealed_token(sealed_token, private_key)
sham_type = plaintext["sham_type"]
sham_params = plaintext["sham_params"]
seed = int(plaintext["seed"], 16)

# Reconstruct the transformer with the recorded seed
if sham_type == "phase_scrambled":
    transformer = PhaseScrambled(seed=seed, **sham_params)
elif sham_type == "time_shifted_self":
    transformer = TimeShiftedSelf(**sham_params)
# …

# Re-run the session
evaluator = Evaluator.live(ir, ..., chunk_transformer=transformer)
# Feed the raw recording, observe identical event stream
```

This is the strongest evidence base for re-analysis. Differences
between original-session events and replayed events indicate either
non-determinism in the evaluator (a bug) or non-determinism in the
host's recording loop (out of scope).

---

## 9. Limitations and open questions

- **Bandpass coefficient retuning under `set_control` is not yet
  reproducible** in v0.0.5. If a session's clinician tunes a
  filter-coefficient-affecting control mid-session, the warm-restart
  state cannot currently be reconstructed from the sealed token
  alone. Phase 0e-c addresses this.
- **No support for switching condition mid-session.** Each session is
  one condition. Crossover designs require multiple sessions with
  independent sealed tokens.
- **No multi-arm whitelist semantics beyond type-level.** The protocol
  whitelists *types* (`"phase_scrambled"`), not specific parameter
  values. A protocol cannot say "`phase_scrambled` with `window_s ≥ 10`
  only"; that's host-level study design.
- **Real-arm probability is per-session-config**, not per-protocol.
  A future spec extension may add a `meta.sham_probability` recommend-
  ation that the host honours by default.
