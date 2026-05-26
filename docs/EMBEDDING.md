# Embedding Refrain in a host application

This guide is for someone wiring Refrain into an EEG recorder, an LSL
relay, or any other host that already has its own data acquisition and
its own patient-facing renderer. Refrain provides the protocol parser,
typed IR, and the streaming evaluator that turns chunks of EEG into
reward events; the host owns everything else.

The intended division of labour:

```
┌────────────────────────────────────────────────────────────────────┐
│  Host application                                                  │
│                                                                    │
│  ┌───────────┐    ┌──────────────────────────────────┐             │
│  │ Amp / SDK │ ── │       refrain.eval_.Evaluator     │             │
│  └───────────┘    │                                  │             │
│                   │  load + resolve protocol         │             │
│                   │  step_chunk(samples) → events    │             │
│                   │  set_control(name, value)        │             │
│                   └──────────────────────────────────┘             │
│                                  ↓                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Patient renderer: audio player, video modulator, ambient    │  │
│  │  effects. Reads events; produces sensory feedback.           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

Refrain does not open audio devices, video surfaces, or amp connections.

---

## Installation

```bash
pip install refrain[eval]
```

The `[eval]` extra pulls in `mne` and `pyxdf` for the file-based source
adapters (FIF/EDF/XDF). Embedded hosts that feed Refrain via
`step_chunk` don't strictly need either, but most do already for
offline replay during development. The parser/resolver/IR work without
the extra.

---

## Minimum integration loop

```python
import refrain
from refrain.amp_profile import load_amp_profile
from refrain.resolver import resolve
from refrain.eval_ import Evaluator

# === Once at session start ==========================================

protocol_ast = refrain.parse_file("smr_cz_brainbit.refrain")
amp = load_amp_profile("brainbit_flex.json")
ir = resolve(protocol_ast, amp)

evaluator = Evaluator.live(
    ir,
    sample_rate_hz=250,
    channel_names=("Cz", "F3", "F4", "Pz"),  # whatever your placement is
)
evaluator.start()  # enters warmup automatically if the protocol declares one

# === Per chunk from your amp callback ===============================

def on_brainbit_chunk(chunk):
    """chunk: numpy.ndarray of shape (n_samples, n_channels), float64.

    Channel column order MUST match the channel_names passed at
    Evaluator.live() construction. Sample count per chunk can vary;
    Refrain handles arbitrary chunk sizes.
    """
    for event in evaluator.step_chunk(chunk):
        if event.channel == "audio_chime" and event.kind == "event":
            patient.audio.play_chime()
        elif event.channel == "audio_gain" and event.kind == "value":
            patient.audio.set_gain(event.value)          # already in [0, 1]
        elif event.channel == "video_clarity":
            patient.video.set_clarity(event.value)
        elif event.channel == "ambient_density":
            patient.ambient.set_density(event.value)

# === Clinician tunes a control mid-session ==========================

evaluator.set_control("smr_target_pct", 65)   # was 70 by default

# === Session end =====================================================

evaluator.stop()
```

That's the whole surface. Five `Evaluator` methods: `live`, `start`,
`step_chunk`, `set_control`, `stop`.

---

## Deploy-time: binding a parameterized protocol

A protocol can declare `placement` controls so a *single* `.refrain` artifact
deploys at clinician-chosen sites without re-authoring (SPEC §4.9). Binding
happens once, at **resolve time** — off the realtime path and independent of
`backend=`. The resolved IR is identical in shape to a hand-written fixed-site
protocol, so the wire format (`IR_JSON_VERSION` stays `0.1`) and the Rust core
never see "placement" at all. `backend="rust"` and parameterized placement are
orthogonal and compose freely.

**1. Discover what a protocol exposes.** Resolve once (defaults bound) and read
the placement controls straight off the in-memory IR — they're retained on
`ir.controls` even though the IR-JSON emitter omits them:

```python
ir = resolve(protocol_ast, amp)                 # defaults bound; no overrides yet

placements = {n: c for n, c in ir.controls.items() if c.type_kind == "placement"}
for name, c in placements.items():
    print(name, c.kind, repr(c.label),
          "allowed:", c.allowed or "any",        # () means "any"
          "default:", c.default_placement,
          "locked" if c.final else "")
```

Each placement control carries `.kind` (`"active" | "bipolar" | "pair" | "set"`),
`.allowed` (a tuple of channel names — or of 2-tuples for `bipolar`/`pair`; `()`
means "any device channel"), `.default_placement`, `.label`, `.final`, and for
`set` the size bounds `.set_min` / `.set_max`. Build the clinician's site-picker
UI from exactly these fields.

**2. Bind the clinician's choices and re-resolve.** The value shape matches the
control's `kind`:

```python
ir = resolve(protocol_ast, amp, bindings={
    "site":  "C4",                  # active   → a channel string
    "motor": ("C3", "C4"),          # bipolar  → 2-tuple (active, reference)
    "coh":   ("F3", "F4"),          # pair     → 2-tuple (coherence legs .a/.b)
    "sites": ["C3", "Cz", "C4"],    # set      → list of channels
})
evaluator = Evaluator.live(ir, sample_rate_hz=250, channel_names=layout)
```

- **active** — the bound channel substitutes into the montage and `requires.channels`.
- **bipolar** — `(active, reference)`; **pair** — the two coherence legs referenced as `coh.a` / `coh.b`.
- **set** — each bound site replicates the input's dependent pipeline (derives,
  thresholds, reward *condition*), and the reward combines the per-site conditions
  with `all`/`any` per the protocol's `reward.combine` (Mode 2a). The resolved IR
  is a flat N-site graph the core runs unchanged.

**3. Validation is fail-fast, at deploy — never mid-session.** Every bound site
is checked against the control's `allowed` set intersected with the amp's actual
channels (and, for `set`, against `min`/`max`). A site the device can't provide,
a value outside `allowed`, or any override of a `final` (locked) control raises
`ResolveError` from `resolve(...)` — before `Evaluator.live(...)`, so a bad
placement can't reach a running session:

```python
from refrain.resolver import ResolveError
try:
    ir = resolve(protocol_ast, amp, bindings={"site": "Fz"})
except ResolveError as e:
    show_clinician_error(str(e))   # e.g. "site 'Fz' not in allowed {...}" / not on this amp
```

A `set` placement also gates `reward.continuous`: a continuous reward over a
replicated set raises `ResolveError` (it needs aggregation — Mode 2b). See SPEC
§4.9 for the language-side declaration syntax.

---

## The lifecycle

Refrain's evaluator transitions through these states (SPEC §7.1):

| state | what it means |
|---|---|
| `ready` | constructed but not yet running. `start()` advances. |
| `warmup` | running, but output events suppressed. Filter state is settling and percentile windows are populating. Duration = the protocol's `session.phases[0].duration` if that phase has `output_muted = true`. |
| `run` | running, full output. Reward chimes fire, gain values flow. |
| `stopped` | session ended. Further `step_chunk` calls raise. |

`evaluator.state` exposes the current state at any time;
`evaluator.warmup_remaining_s` tells you how long until the warmup
window ends so your UI can show "warming up: 47 s left."

Skipping warmup is supported but should only be used in offline
analysis: `evaluator.start(skip_warmup=True)`. In a live clinical
session, the warmup is what prevents the patient from hearing filter
settling artifacts in the first 90 seconds.

---

## Events you'll receive

`step_chunk` returns a list of `Event` records:

```python
@dataclass(frozen=True)
class Event:
    timestamp_s: float    # seconds since the first chunk was pushed
    channel: str          # output-binding name (audio_gain, audio_chime, …)
    kind: str             # "value" (analog) or "event" (discrete)
    value: float | None   # in [0, 1] for analog; None for events
```

The protocol's `output { … }` block declares which channels exist:

```refrain
output {
  audio_chime = reward.event                       // → kind="event"
  audio_gain  = reward.event.holds ? reward.continuous : 0   // → kind="value"
}
```

Analog channels are clamped to `[0, 1]` and emitted as one Event per
chunk (carrying the chunk's mean). Event channels emit one Event per
sample where the rising edge fires. Custom output channel names are
permitted; your renderer dispatches on whatever names the protocol
declares.

---

## Live control tuning

Any `controls.<name>` declared with `live_tunable = true` can be
adjusted mid-session via `evaluator.set_control(name, value)`. Phase
0e-a supports the parameters most commonly tuned in clinical practice:

| Used as | Live retune actually changes |
|---|---|
| `percentile(target_pct: <control>, …)` | the percentile target (the operant threshold) |
| `smooth(tau: <control>)` | the smoothing time constant |
| `sigmoid(midpoint: <control>, …)` | the sigmoid midpoint |
| `bandpass(center: <control>, …)` | (deferred — Phase 0e-c) |

Other parameters wired to controls accept the update but don't yet
recompute (silently ignored). Filter-coefficient updates that preserve
delay-line state — SPEC §7.7's warm-restart — land in Phase 0e-c.

---

## Research mode (CRED-nf-grade allocation concealment)

> **⚠️ Forthcoming — not in the shipped 0.6.x API.** The `Evaluator.live(...)`
> parameters and properties shown in this section (`chunk_transformer=`,
> `sham=ShamConfig(...)`, `evaluator.allocation_token`) are a *design preview*
> and are **not** part of the current release. The shipped `live()` signature
> beyond the required args is `record_streams=` and `backend=` only. SPEC §7.9
> defines the language-level contract; this host API will land in a later
> version. Don't write integration code against it yet.

For research studies that need blinded comparison of a real protocol
against one or more sham conditions, Refrain can take ownership of the
randomization, signal substitution, and cryptographic concealment. See
SPEC §7.9 for the language-level contract and `docs/RESEARCH-MODE.md`
for the full threat model.

The host has two integration paths:

### Simple: host-owned sham via `chunk_transformer`

Pass any `ChunkTransformer` to `Evaluator.live(...)` and Refrain pipes
every chunk through it before the eval pipeline sees the data. The
patient experiences whatever the transformer emits; tap values and
output events all reflect the transformed signal.

```python
from refrain.research import TimeShiftedSelf

evaluator = Evaluator.live(
    ir, sample_rate_hz=250, channel_names=("Cz",),
    chunk_transformer=TimeShiftedSelf(delay_s=30.0),
)
```

The host decides which condition each session is in — fine for
non-blinded designs (pilot studies, methodology development), not
adequate for CRED-nf-grade allocation concealment because the host
*knows* the condition.

### Full: sealed allocation with `ShamConfig`

For CRED-nf-grade designs, hand the randomization decision to Refrain
and receive an encrypted token the host stores but cannot decrypt.

```python
from refrain.research import (
    ShamConfig, TimeShiftedSelf, PhaseScrambled, YokedReplay,
    open_sealed_token,
)
from refrain.sources import FifSource

# Independent statistician generates an X25519 keypair; the public key
# travels with the study, the private key is held in the unblinding
# vault.
PUBLIC_KEY = bytes.fromhex("…")   # 32 bytes

evaluator = Evaluator.live(
    ir, sample_rate_hz=250, channel_names=("Cz",),
    sham=ShamConfig(
        candidates=[
            TimeShiftedSelf(delay_s=30.0),
            PhaseScrambled(window_s=10.0),
            YokedReplay(candidates=[FifSource(p) for p in control_recordings]),
        ],
        sham_probability=0.5,        # default; host-overridable
        seal_to=PUBLIC_KEY,
    ),
)
sealed_token = evaluator.allocation_token   # opaque bytes

# Host stores `sealed_token` alongside the session record. The host
# never learns which condition was chosen.
```

After the study completes, the holder of the matching X25519 private
key (typically an independent statistician using the unblinding vault)
decrypts each session's token:

```python
PRIVATE_KEY = bytes.fromhex("…")   # 32 bytes

allocation = open_sealed_token(sealed_token, PRIVATE_KEY)
# {
#   "version": 1,
#   "condition": "sham",
#   "sham_type": "phase_scrambled",
#   "sham_params": {"window_s": 10.0},
#   "candidate_index": 1,
#   "seed": "0x1f2e3d...",
#   "timestamp": "2026-05-12T14:23:11Z",
#   "refrain_version": "0.0.5",
#   "protocol_id": "smr_cz_brainbit_v1",
#   "protocol_hash": "sha256:abc123..."
# }
```

The plaintext schema is fixed at the language level (SPEC §7.9.3) so
cross-runtime tokens are interoperable. The `protocol_hash` captures
the resolved IR — two sessions with the same hash ran the same
computation regardless of source-file arrangement.

### Whitelist enforcement

The protocol's `meta.sham_strategies` whitelist controls which sham
types are permitted (SPEC §4.1):

```refrain
meta {
  sham_strategies = ["time_shifted_self", "phase_scrambled"]
}
```

`ShamConfig` candidates whose type isn't on the list are rejected at
`Evaluator.live(...)` time with a clear diagnostic. Absent or empty
list = no sham permitted (strict-by-default).

Static probe before instantiation:

```python
allowed = ir.meta.fields.get("sham_strategies", [])
# host UI greys out sham options the protocol doesn't permit
```

### Constant-time guarantees

By default, Refrain guarantees *within-session* constant time —
clinicians observing the patient cannot distinguish real from sham via
timing patterns inside one session.

For threat models that also worry about *cross-session* timing
attacks, opt into strict mode:

```python
sham=ShamConfig(..., strict_constant_time=True)
```

In strict mode the evaluator runs all candidate transformers on every
chunk and selects the output internally. ~3× CPU cost; chunk-time is
the slowest candidate's chunk-time regardless of condition. See
`docs/RESEARCH-MODE.md` for the full threat-model discussion.

### Reproducibility

The sealed token's `seed` field is sufficient to re-run a session
deterministically given the same recording (or the same yoked-replay
candidate) and the same protocol. Useful for re-analysis and for
catching evaluator bugs that affect a specific allocation.

---

## Introspection: live taps

For host applications that render a clinician observation window —
envelope traces per derive, threshold lines that move with the
envelopes, a dwell-component tape showing which sub-condition is
blocking reward, a pre-gating "how close to reward" overlay — Refrain
exposes per-chunk last-sample values of the internal stream
computations via `Evaluator.last_taps()`.

```python
events = evaluator.step_chunk(chunk)
# Dispatch patient-facing events as before
for ev in events:
    render_to_patient(ev)

# Pull internal values for the clinician observation window
taps = evaluator.last_taps()
plot_envelope.append(taps["derive/smr_envelope"])
plot_threshold.append(taps["threshold/smr_t"])
plot_pre_gating_reward.append(taps["reward/continuous"])
dwell_tape.append([
    taps["reward/condition[0]"],   # SMR > threshold?
    taps["reward/condition[1]"],   # theta < threshold?
    taps["reward/condition[2]"],   # high-beta < threshold?
])
```

### Tap keys

`last_taps()` returns a `dict[str, float | bool]`. Only keys for
entities that exist in the resolved protocol are present:

| Key | Type | What it is |
|---|---|---|
| `input/<name>` | float | last sample of the post-montage input |
| `derive/<name>` | float | last sample of the derive's output |
| `threshold/<name>` | float | current threshold value (last sample) |
| `inhibit/<name>` | boolean | this inhibit currently active |
| `muted` | boolean | combined inhibit-gate state |
| `reward/continuous` | float | pre-gating reward sigmoid value |
| `reward/event` | boolean | dwell fired any sample this chunk |
| `reward/event.holds` | boolean | dwell condition currently held |
| `reward/condition[i]` | boolean | i-th dwell sub-condition. Single-condition dwells uniformly emit `reward/condition[0]` |
| `reward/composite` | float | weighted-composite success in [0,1] (v0.2; only when the protocol declares named reward/suppress components) |
| `reward/component[<name>]` | float | a named component's [0,1] success signal (v0.2; one per component) |
| `output/<channel>` | float \| boolean | post-gating, post-clamp value of the channel |

### Behaviour

- **Empty before first step_chunk.** `last_taps()` returns `{}` until at least one chunk has been pushed.
- **Returns a copy.** Mutating the returned dict has no effect on the evaluator's internal state. Persist or zip-aggregate freely.
- **Populated during warmup.** The taps are populated identically during `warmup` and `run` lifecycle states — hosts legitimately want to plot warmup progress.
- **One read per chunk.** Reading `last_taps()` from a 60-Hz UI thread when chunks arrive at 16 Hz is fine but wasteful (you'll get the same values four times). Cache the snapshot once per chunk arrival and redraw from the cache.

### Naming conventions

- `<kind>/<name>` matches the IR's internal canonical-name scheme — no ambiguity between user-named entities (`derive/my_signal`) and category-level globals (`muted`).
- Bracketed indices (`reward/condition[0]`, `reward/component[smr]`) for arrayed/named sub-items; flat names for everything else.
- The v0.2 weighted-composite keys (`reward/composite`, `reward/component[<name>]`) appear only for protocols that declare named reward/suppress components. In `last_streams()` the same data uses the dotted namespace (`reward.composite`, `reward.component.<name>`), mirroring `reward.continuous`.
- `reward/event` semantics are intentionally `.any()` over the chunk's events (boolean: did anything fire), distinct from the per-sample event Event records that step_chunk returns. Use the Event stream for accurate edge timing; use the tap for "is anything happening" status display.

---

## Channel-order and montage notes

When you call `Evaluator.live(channel_names=(...))`, those names define
how protocol references resolve. If your protocol says
`input "raw" { montage = bipolar(plus: "T3", minus: "T4") }`, you must
pass `"T3"` and `"T4"` somewhere in `channel_names`.

For amps with a hardware reference electrode (BrainBit Flex, OpenBCI
Cyton with the built-in reference, etc.) and no user-placed ear
electrodes, write the input montage as:

```refrain
input "raw" {
  montage = referential(active: "Cz", reference: "device")
}
```

`reference: "device"` means "use the channel as-recorded; the amp's
hardware reference is already baked in." Refrain doesn't re-reference
in software, which is what you want.

---

## Threading model

`step_chunk` is synchronous and not thread-safe. The intended pattern
is: your amp callback fires on the host's audio/data thread, you call
`step_chunk(chunk)` synchronously, and the returned events drive your
renderer (which may have its own thread).

If your amp callback can't tolerate the evaluator's per-chunk latency
(measure it; for SMR on a typical machine it's well under 1 ms per
64-sample chunk), wrap Refrain in a worker thread reading from a
queue. Refrain itself stays single-threaded internally so the worker-
queue pattern is straightforward.

---

## What if the protocol's required channels aren't in my source?

The resolver validates the protocol's `requires.channels` against the
amp profile at resolution time, not against `channel_names` at runtime.
If you change electrode placements between sessions, write (or extend)
an amp profile that lists the placements you actually use, and the
resolver will catch mismatches there.

In a worst case where `channel_names` doesn't include a channel the
protocol's montage references, the evaluator raises `ValueError` from
the relevant `BipolarImpl` / `ReferentialImpl` constructor at
`Evaluator.live(...)` time — clear enough.

---

## Offline replay during development

Before going live, you can record a session as XDF (most LSL-based
recording stacks do this with one button) and replay it through
Refrain offline to verify your integration. The pull-mode API does
exactly this:

```python
from refrain.sources import open_source
from refrain.eval_ import eval_protocol

source = open_source("yesterdays_session.xdf")
for event in eval_protocol(ir, source, chunk_size=64):
    print(event)
```

Push-mode (`step_chunk`) and pull-mode (`eval_protocol` / `run`) are
guaranteed to produce byte-identical event streams given the same
input. So "develop offline, deploy live" is a supported workflow.

---

## What's not yet here

- Live bandpass-coefficient recompute (Phase 0e-c). If your protocol
  uses `bandpass(center: orf, …)` and the clinician retunes `orf`
  mid-session, the change is recorded but the filter coefficients don't
  re-derive until the session restarts. SMR Cz doesn't trigger this
  (its bands are literals); Othmer ILF does.
- Calibration phase (impedance check, baseline measurement). The
  evaluator skips straight to `warmup`. Hosts that want impedance
  checks must drive them separately.
- Session pause / resume. There's `start()` and `stop()`; no `pause()`.
- Multiple simultaneous protocols in one evaluator. Run them as
  separate Evaluator instances if you need that.
