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
| `output/<channel>` | float \| boolean | post-gating, post-clamp value of the channel |

### Behaviour

- **Empty before first step_chunk.** `last_taps()` returns `{}` until at least one chunk has been pushed.
- **Returns a copy.** Mutating the returned dict has no effect on the evaluator's internal state. Persist or zip-aggregate freely.
- **Populated during warmup.** The taps are populated identically during `warmup` and `run` lifecycle states — hosts legitimately want to plot warmup progress.
- **One read per chunk.** Reading `last_taps()` from a 60-Hz UI thread when chunks arrive at 16 Hz is fine but wasteful (you'll get the same values four times). Cache the snapshot once per chunk arrival and redraw from the cache.

### Naming conventions

- `<kind>/<name>` matches the IR's internal canonical-name scheme — no ambiguity between user-named entities (`derive/my_signal`) and category-level globals (`muted`).
- Bracketed indices (`reward/condition[0]`) for arrayed sub-conditions; flat names for everything else.
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
