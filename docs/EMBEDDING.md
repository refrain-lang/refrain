# Embedding Refrain in a host application

This guide is for someone wiring Refrain into an EEG recorder, an LSL
relay, or any other host that already has its own data acquisition and
its own person-facing renderer. Refrain provides the protocol parser,
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
│  │  Person renderer: audio player, video modulator, ambient    │  │
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
            person.audio.play_chime()
        elif event.channel == "audio_gain" and event.kind == "value":
            person.audio.set_gain(event.value)          # already in [0, 1]
        elif event.channel == "video_clarity":
            person.video.set_clarity(event.value)
        elif event.channel == "ambient_density":
            person.ambient.set_density(event.value)

# === Practitioner tunes a control mid-session ==========================

evaluator.set_control("smr_target_pct", 65)   # was 70 by default

# === Session end =====================================================

evaluator.stop()
```

That's the whole surface. Five `Evaluator` methods: `live`, `start`,
`step_chunk`, `set_control`, `stop`.

---

## Deploy-time: binding a parameterized protocol

A protocol can declare `placement` controls so a *single* `.refrain` artifact
deploys at practitioner-chosen sites without re-authoring (SPEC §4.9). Binding
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
`set` the size bounds `.set_min` / `.set_max`. Build the practitioner's site-picker
UI from exactly these fields.

**2. Bind the practitioner's choices and re-resolve.** The value shape matches the
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
    show_setup_error(str(e))   # e.g. "site 'Fz' not in allowed {...}" / not on this amp
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
session, the warmup is what prevents the person from hearing filter
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
person experiences whatever the transformer emits; tap values and
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
practitioners observing the person cannot distinguish real from sham via
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

For host applications that render a practitioner observation window —
envelope traces per derive, threshold lines that move with the
envelopes, a dwell-component tape showing which sub-condition is
blocking reward, a pre-gating "how close to reward" overlay — Refrain
exposes per-chunk last-sample values of the internal stream
computations via `Evaluator.last_taps()`.

```python
events = evaluator.step_chunk(chunk)
# Dispatch person-facing events as before
for ev in events:
    render_to_person(ev)

# Pull internal values for the practitioner observation window
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

## Introspection: seed reports and cross-session state

Two more `Evaluator` accessors report internal state that, like the live
taps, is a host convenience rather than something that changes the protocol
IR. Both are deliberately **not** taps — they don't share `last_taps()`'s
strict key-set contract — so read them separately.

**`seed_report()`** — a control declared `seed = percentile { from, window,
target_pct }` (SPEC's baseline-seeding surface) measures its value from the
person's own signal during warmup and writes it once at the warmup→run
edge. `Evaluator.seed_report()` returns the outcome of every such control,
keyed by bare control name (not the tap's `control/<name>` form):

```python
report = evaluator.seed_report()
# { "smr_target_pct": {
#     "status": "seeded",             # "pending" | "seeded" | "insufficient_samples" | "disarmed_by_host"
#     "value": 7.42,
#     "source": "derive/smr_envelope",
#     "target_pct": 70.0,
#     "n_samples": 500,
#     "window_s": 120.0,
#     "at_time_s": 120.0,
#   } }
```

`insufficient_samples` means the seed failed closed — not enough warmup
samples reached the buffer (or they were all non-finite) — and the control
kept its declared default rather than writing a measured value. `pending`
means warmup hasn't reached the run edge yet. `disarmed_by_host` means a
practitioner called `set_control()` on the seeded control before it fired; that
disarms the seed permanently for the session rather than racing it. Empty
for a protocol with no seeded controls.

**`export_state()` / `seed_state`** — separate from control baseline
seeding, this is cross-*session* persistence for the adaptive trackers
behind `percentile` and `auto_range` (SPEC's `percentile`/`auto_range`
primitives, not the control `seed` block above — same word, different
feature). Those trackers start cold every session; to carry an
adaptive ceiling forward, read the compact summary with
`evaluator.export_state()` at session end, persist it to the person
record, and hand it back on the next run via
`Evaluator.live(..., seed_state=<prior export>)`. The state is a small,
rate-independent anchor set (not a raw buffer) and never touches the
protocol IR. See `docs/PRIMITIVES.md` for the shape of `export_state()`'s
per-entity values.

---

## Autopilot advice

Every session, whether or not its protocol declares an `autopilot { }`
block (`docs/SPEC.md` §4.12) or any per-control policy (§4.9.5), carries a
second built-in advisor alongside the reward/output pipeline. It watches the
same per-chunk facts the evaluator already computes and, after every chunk,
produces one structured piece of advice: hold, keep collecting evidence, hint
at a direction, or propose a concrete change to one control. See
`docs/AUTOPILOT-AUTHORING.md` for how to write a policy and `docs/SPEC.md`
§7.10 for the full decision procedure this section is the host-facing view
of.

**Who does what.** Refrain never changes a control on its own — it only ever
proposes. Turning autopilot *on* for a session, and deciding whether an
`adjust` result should be applied automatically or left for the practitioner to
approve, are both host decisions. `apply_advice(id, by="autopilot")` is the
one enforcement point: it refuses to apply any change the protocol declared
suggest-only, so a host cannot accidentally auto-apply something the protocol
author reserved for a practitioner's judgement.

### The six calls

```python
evaluator.advice() -> dict
evaluator.apply_advice(advice_id: str, by: str = "practitioner") -> dict
evaluator.dismiss_advice(advice_id: str) -> dict
evaluator.mark_equipment_change() -> None
evaluator.drain_advice_events() -> list[dict]
evaluator.autopilot_policy() -> dict
```

- **`advice()`** — the current result (shape below). Always present once the
  session has started; it is computed as a side effect of `step_chunk`, so
  this call is a pure read.
- **`apply_advice(id, by="practitioner")`** — applies the current `adjust`
  result's proposed value through the engine's internal control-update path,
  logs an `applied` event, restarts the evidence window, and arms the
  one-shot reversal check. It does **not** go through `set_control`: it logs
  no `changed_manually` event and does not disarm a pending baseline seed
  on that control. Raises `AdviceError` if `id` is not the current `adjust`
  id, or if `by="autopilot"` and the protocol only allows this change as a
  suggestion (`advice()["control"]["auto_allowed"]` is `False`). Returns the
  `applied` event dict.
- **`dismiss_advice(id)`** — logs a `dismissed` event and suppresses that
  same knob-and-direction move for `between_moves`. Raises `AdviceError` on
  an id that isn't the current `adjust`/`hint` result. Returns the
  `dismissed` event dict.
- **`mark_equipment_change()`** — logs an `equipment_change` event, restarts
  the evidence window, and starts the `equipment_settle` hold. Call this
  whenever your UI lets a practitioner adjust the amp or electrodes mid-session.
- **`drain_advice_events()`** — every audit event since the last drain, in
  order. Persist these with the session record; routine `collecting` and
  `on_track` results are not events, only transitions are.
- **`autopilot_policy()`** — the effective policy as data: `advisor_version`,
  every setting with its default filled in, guards, limiters, and each
  knob's policy (units, limits, step, strategy, direction, `apply`, label,
  citation), plus the block's provenance. Use this to render a session setup
  screen or header — it needs no chunks fed yet.

`AdviceError` (raised by `apply_advice`/`dismiss_advice`) is exported from
the top-level `refrain` package alongside `Evaluator`.

### A recorder-shaped loop

```python
for chunk in amp_chunks():
    events = ev.step_chunk(chunk)
    a = ev.advice()
    ui.show_advice(a)                              # every chunk: collecting / hold / hint / adjust
    if a["state"] == "adjust" and session.autopilot_on and a["control"]["auto_allowed"]:
        ev.apply_advice(a["id"], by="autopilot")
    audit.extend(ev.drain_advice_events())          # persist with the session record
```

`session.autopilot_on` is the host's own per-session enable switch and its
persisted audit trail — Refrain does not have an opinion on either; it only
ever tells you what it would do and lets `apply_advice` refuse what the
protocol forbids.

### The `advice()` shape

```json
{
  "advisor_version": "1",
  "id": "adv-0007",
  "state": "adjust",
  "level": "policy",
  "reason": "too_strict",
  "message": "Reward met 6% of clean time (target 10-35%). The limiter is crossover. Lower Crossover target 0.75 -> 0.70.",
  "t_s": 1312.5,
  "limiter": { "check": "crossover", "pass_rate": 0.08 },
  "control": {
    "name": "crossover_target", "label": "Crossover target",
    "units": "", "round_to": 0.01,
    "current": 0.75, "proposed": 0.70,
    "direction": "easier", "strategy": "fixed_step",
    "auto_allowed": true
  },
  "evidence": {
    "window_start_s": 1180.0, "window_end_s": 1312.5,
    "clean_s": 120.0, "required_s": 120.0,
    "reward_rate": 0.06, "target": [0.10, 0.35],
    "checks": { "theta": 0.84, "crossover": 0.08 },
    "guards": { "delta": 0.03, "emg": 0.07 },
    "chimes_per_min": 0.5
  },
  "eligible_at_s": 1312.5
}
```

Every key listed here is present on every call — a field that doesn't apply
to the current state is `null`, never omitted, so a host never needs a
`.get()`-with-default or a key-existence check.

| Key | Type | Meaning |
|---|---|---|
| `advisor_version` | string | Which built-in defaults and decision rules produced this result (currently `"1"`); independent of the protocol's own `meta.version`. |
| `id` | string \| null | `adv-NNNN`, present for `hint` and `adjust`; stable across chunks while the knob, direction, and proposed value are unchanged, so the UI doesn't flicker. |
| `state` | string | `collecting` \| `hold` \| `hint` \| `adjust`. |
| `level` | string | `observation` \| `hint` \| `policy` — a UI affordance: style/gate `policy`-level results more prominently than plain observations. |
| `reason` | string | See "Reason codes" below. |
| `message` | string | English-language fallback. Hosts style or translate by `reason`; `message` is always safe to show as-is. |
| `t_s` | number | Session sample time (seconds) this result was computed at. |
| `limiter` | object \| null | `{ "check": <name or "check N">, "pass_rate": <0..1> }`, present from decision step 8 onward (once a limiting check has been selected). |
| `control` | object \| null | Present for `hint` and `adjust`. For a `hint`, `proposed`, `round_to`, and `strategy` are `null` and `auto_allowed` is `false` — see below. |
| `evidence` | object \| null | Present once an evidence window exists. It can be `null` while `collecting` right after the window restarts (a new training phase, an applied or manual change, an equipment change) until the next chunk arrives. For a protocol with no reward condition (`reason: "observing"`), `reward_rate` is `null`; `target` is still the `[low, high]` pair and `checks` is `{}` (empty, not null). |
| `eligible_at_s` | number \| null | When a currently-blocked knob becomes eligible again (`cooldown`), or the current time for a fresh `adjust`/`reversal`. |

`control`, when present:

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Bare control name (`set_control`'s first argument). |
| `label` | string | Display name — the policy's `say`, else the control's `label`, else its bare name. |
| `units` | string | `""`, `"%"`, `"uV"`, or `"Hz"`. |
| `round_to` | number \| null | The knob's snapping precision, for display formatting. `null` for a hint. |
| `current` | number | The control's current value. |
| `proposed` | number \| null | The proposed value; `null` for a hint or when no move is possible (`at_limit`). |
| `direction` | string | `"harder"` or `"easier"`. |
| `strategy` | string \| null | `"fixed_step"` \| `"proportional_step"` \| `"rebaseline"`; `null` for a hint. |
| `auto_allowed` | boolean | Whether the protocol allows this knob to be changed automatically (`apply = "auto"`, and not a `rebaseline` proposal). It describes the policy, not the moment: it is also `true` on an `at_limit` or `cooldown` hold, where `apply_advice` still refuses because the result is not an `adjust`. Always `false` for a hint and for a `rebaseline` proposal. |

### Reason codes

`not_training_phase`, `equipment_settling`, `guard`, `collecting`,
`observing`, `on_track`, `no_knob`, `at_limit`, `cooldown`, `too_strict`,
`too_easy`, `reversal`. `observing` means the protocol has no reward
condition for the advisor to judge (a continuous-only reward, or a
weighted-composite reward) — it is a normal hold, not an error.

### Audit events

`drain_advice_events()` returns these, each carrying `advisor_version` and
`t_s`:

| `kind` | Emitted by | Fields |
|---|---|---|
| `suggested` | advisor | `id`, `reason`, `control`, and (for `adjust`) `from`/`to`. |
| `superseded` | advisor | `id`, `reason` — a standing id was replaced by a different result, or by a manual control change. |
| `blocked` | advisor | `id`, `reason` — a standing id was replaced by a hold. |
| `applied` | `apply_advice` | `id`, `control`, `from`, `to`, `by` (`"practitioner"` or `"autopilot"`). |
| `dismissed` | `dismiss_advice` | `id`, `control`. |
| `changed_manually` | `set_control` | `control`, `from`, `to` — only for a control that feeds a reward check or an inhibit; `set_control` on any other control logs nothing, and writing the value a control already has changes nothing (no event, no window restart, no cooldown). |
| `equipment_change` | `mark_equipment_change` | — |

### Backends

With `backend="rust"`, every one of the six calls delegates to the Rust core
the same way `seed_report()` does, and results/events are identical once
parsed as JSON values (numbers compared as `f64` — Python and Rust print
floats differently, so parity is checked on parsed values, not raw bytes)
— see `docs/SPEC.md` §7.10.7 for the rounding
rules that make the two engines agree exactly.

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
  uses `bandpass(center: orf, …)` and the practitioner retunes `orf`
  mid-session, the change is recorded but the filter coefficients don't
  re-derive until the session restarts. SMR Cz doesn't trigger this
  (its bands are literals); Othmer ILF does.
- Calibration phase (impedance check, baseline measurement). The
  evaluator skips straight to `warmup`. Hosts that want impedance
  checks must drive them separately.
- Session pause / resume. There's `start()` and `stop()`; no `pause()`.
- Multiple simultaneous protocols in one evaluator. Run them as
  separate Evaluator instances if you need that.
