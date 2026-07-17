# Amp reference abstraction (`amp.reference`) — Design

- **Date:** 2026-07-16
- **Status:** design (approved; ready to plan)
- **Scope:** `refrain` engine, plus a `refrain-protocols` follow-on (make the 21
  generic `linked_ears` protocols declare the electrodes they depend on, and repin
  CI). The live flatline defect (§"Audit") is **not** closed by this spec — its fix
  is a device-compatibility gate in the portal/mobile layer, tracked separately.
- **Target release:** `refrain` v0.15.0 (lockstep with `refrain_core`)
- **Relationship:** sub-project #1 of the device-agnostic protocol platform
  (Linear **WOR-142**; umbrella design:
  `refrain-protocols/docs/superpowers/specs/2026-07-06-device-agnostic-protocol-platform-design.md`).
  It is the first of three independently-startable pieces, and the library
  re-authoring is gated on it.

## The goal, in one sentence

**A protocol declares its montage reference once, hardware-neutral; the connected
amp profile supplies the actual reference at resolve time — and when it cannot,
resolution fails loudly rather than guessing.**

## Why

The protocol library is forked per amplifier. Diffing the same concept across the
fork (`smr_up_c4` vs `smr_up_c4_brainbit`) shows only three genuinely
hardware-driven deltas:

| Delta | Generic | BrainBit | Real hardware fact? |
|---|---|---|---|
| Montage reference | `linked_ears` | `device` | **Yes** |
| Coupling | *(absent)* | `coupling = "ac"` | No — over-constraint (see Non-goals) |
| Sample rate | `>= 256 Hz` | `>= 250 Hz` | No — drift (see Non-goals) |

The reference is the one delta that is a true property of the amplifier and
cannot be authored away. It is therefore the narrowest change that removes the
largest reason the fork exists.

### The fork may exist because the engine guesses

`ReferentialImpl._resolve_reference` (`src/refrain/primitive_impls.py:129`)
silently degrades `linked_ears` to common-average when the source carries fewer
than two ear electrodes:

```python
if len(candidates) < 2:
    # Some recordings ship without explicit ear channels.
    # Fall back to common-average; the evaluator logs this.
    return None
```

`brainbit_flex.json` declares exactly four channels — `Cz, F3, F4, Pz` — and no
ear or mastoid electrodes. **21 of the 22 generic protocols declare
`reference: "linked_ears"`.** Running any of them on a BrainBit today therefore
does not fail; it quietly computes a different montage and logs a line.

Forking the library was a rational response to an engine that would rather
substitute than complain. Fixing the substitution is a precondition for
un-forking it.

### The amp profile already knows the answer, in prose

`src/refrain/amp_profiles/brainbit_flex.json` carries the fact in a comment
string a machine cannot read:

> `"_comment_reference"`: *"The BrainBit's dedicated reference electrode is
> ear-mounted, and the SDK delivers data already referenced to it. Protocols
> should use `referential(active: "X", reference: "device")` …"*

This design promotes that prose to a field.

## Design

### The seam: resolve-time folding

`amp.reference` folds to a string literal during resolution and never reaches the
IR:

```
source:  referential(active: site, reference: amp.reference)
             │
             ▼   resolve(amp=brainbit_flex)
         _resolve_member_access → amp namespace → profile.reference
             │
             ▼
IR:      referential(active: "Cz", reference: "device")   ← a plain StringLit
             │
             ▼
IR-JSON → evaluator → ReferentialImpl(reference="device")
```

The resulting IR is **identical to what a literal-authored protocol produces
today**. This is the third instance of an established pattern, not a new
subsystem:

- `_substitute_placement_args` (`resolver.py:483`) folds a `placement` control to
  a concrete channel string. Its call site in `_resolve_input`
  (`resolver.py:469-473`) states the intent: *"rewrite any active-placement
  NameRef in the montage call's args to a concrete StringLit before passing to
  `_resolve_call`. This keeps `_resolve_call` unchanged and produces an IR with a
  concrete channel string — identical to a literal-site protocol."*
- `mode` control folding (shipped in v0.12.0) folds a mode choice away at
  resolve time.

Consequences of the fold, all of which are why this design is cheap:

- No IR schema change.
- No IR-JSON change; no change to signature or `content_hash` **shape**.
- No evaluator change (except the deliberate break in §"Breaking change").
- No host change until protocols opt in (sub-projects #4–#6).

The IR remains device-baked, which is correct rather than a compromise: filter
coefficients already bake the sample rate (Linear **DEV-255**), so the portal
already compiles per `(protocol × device)`. The reference rides that existing
path at no additional cost. **The library becomes amp-neutral at the source
level; the IR was never going to be neutral and does not need to be.**

### Component changes

| Component | Change | Breaking? |
|---|---|---|
| `src/refrain/amp_profile.py` | `AmpProfile` gains optional `reference: str \| None = None` | No |
| `src/refrain/resolver.py` | `amp` namespace root in `_resolve_member_access`, with a field allowlist | No |
| `src/refrain/resolver.py` | **Montage/`requires` consistency lint** (§"The consistency lint") | **Yes** |
| `src/refrain/primitive_impls.py` | `linked_ears` raises instead of degrading to common-average (Python evaluator) | **Yes** |
| `refrain-core/src/eval.rs` | The same fix in the Rust evaluator; `Evaluator::new` becomes fallible | **Yes** |
| `refrain-core/src/mobile.rs` | New `RefrainError::UnrealizableMontage` variant | No (additive) |
| `src/refrain/amp_profiles/*.json` | Populate `reference` on the three shipped profiles | No |

### Profile schema

`AmpProfile` (`amp_profile.py:46`) gains:

```python
reference: str | None = None
```

The field is **optional**, so `AMP_PROFILE_SCHEMA` stays at
`"refrain-amp-profile/v0"` (`amp_profile.py:26`) and every existing or
host-supplied profile keeps loading unchanged. An absent field is not a default —
it is a fail-closed condition (see below).

**Validated at load** (`load_amp_profile`, `amp_profile.py:88`): if `reference`
is present it must be either a member of `REFERENCE_KEYWORDS`
(`primitive_impls.py:77` — `{"linked_ears", "common_average", "device"}`) or the
name of a channel the profile itself declares. A violation raises
`AmpProfileError` (`amp_profile.py:84`) at load rather than mid-session.

This check is early, not authoritative: `Evaluator.live(channel_names=...)` can
override the channel list at runtime, which is why §"Breaking change" keeps a
runtime backstop.

Values for the three shipped profiles:

| Profile | `reference` | Basis |
|---|---|---|
| `brainbit_flex` | `"device"` | Stated in its own `_comment_reference`; hardware reference is ear-mounted and pre-applied by the SDK |
| `q21` | `"linked_ears"` | Declares A1/A2 (confirmed by owner, 2026-07-16) |
| `openbci_cyton` | `"linked_ears"` | Declares A1/A2 (confirmed by owner, 2026-07-16) |

`brainbit_flex.json`'s `_comment_reference` is trimmed to the human explanation
once the machine-readable field exists.

### Language surface: the `amp` namespace

Syntax reuses member access, which is already implemented end to end — grammar
(`grammar.lark:84-85`, `member_chain: ("." NAME)+`), parser (`A.MemberAccess`),
and resolver (`_resolve_member_access`, `resolver.py:1901`) — and is exercised by
real protocols today (`reward.event.holds`, `reward.continuous`).

```refrain
input "raw" {
  montage = referential(active: site, reference: amp.reference)
}
```

`amp` joins `reward` (`resolver.py:1616`) as a **namespace root**. It becomes a
reserved root name; nothing in the current corpus uses `amp` as an identifier, so
the cost today is zero.

#### The allowlist

Exposed fields are an explicit, versioned set:

| Field | Ships in |
|---|---|
| `amp.reference` | this spec |
| `amp.clean_hf_floor` | sub-project #2 (capability-conditional decls) |

Nothing else is readable. The rule that decides membership:

> **The profile's other fields are constraints the resolver checks a protocol
> against; the exposed fields are facts a protocol adopts.**

`adc_bits`, `input_range_uv`, and `max_simultaneous_channels` are the former. If a
protocol could branch on them it would become hardware-specific in exactly the way
this project exists to prevent — the fork would be rebuilt inside the language.

This choice is also why the syntax is `amp.<field>` rather than a `from_amp`
bareword: sub-project #2's capability guard (`when amp.clean_hf_floor`) resolves
through the *same* namespace and the *same* code path, so it is an extension
rather than a second mechanism for the same idea.

### Resolution and error handling

`amp.<field>` resolves in this order, every branch fail-closed:

1. No profile (`resolve(amp=None)`) → `ResolveError`
2. Field not in the allowlist → `ResolveError`
3. Field present on the dataclass but `None` → `ResolveError`
4. Otherwise → fold to `A.StringLit(value)` and continue as a literal would

Fail-closed is **additive**, not breaking: `amp.reference` is opt-in, so every
existing literal-reference protocol continues to resolve with `amp=None`
unchanged. Only protocols that ask for the amp's reference require a profile —
and those genuinely cannot be resolved without one.

Required error text (the spec is about refusing to guess; the messages must say
what is missing and where):

```
ResolveError: 'amp.reference' requires an amp profile, but resolve() was
  called with amp=None  (protocol "smr_up_c4", input "raw")

ResolveError: amp profile 'openbci-cyton' declares no 'reference'
  (protocol "smr_up_c4", input "raw")

ResolveError: 'amp.adc_bits' is not an exposed amp field; allowed: reference
```

### Breaking change: `linked_ears` stops guessing — in **both** evaluators

The silent degradation exists in **two independent implementations**, and the
more dangerous one is the one that ships to patients:

| Evaluator | Location | Behaviour today | Runs on |
|---|---|---|---|
| Python | `primitive_impls.py:129` | Degrades to common-average, **logs it** | `refrain run`, recorder |
| Rust | `refrain-core/src/eval.rs:250` | Degrades to common-average, **no log at all** | **cc-mobile**, via `mobile.rs` |

```rust
// refrain-core/src/eval.rs:250 — no logging, no diagnostic
if cand.len() < 2 {
    None            // ← silently becomes common_average
} else {
    Some(cand)
}
```

cc-mobile is the host that actually ships the BrainBit set — the set whose amp
declares no ear electrodes. So the silent path is live in the product, in the
implementation that does not even log.

Both raise instead:

```
referential: reference 'linked_ears' needs >= 2 of A1/A2/M1/M2/T9/T10;
  source has ('Cz','F3','F4','Pz').
  Use reference: "common_average" explicitly if that is what you want.
```

Python raises `ValueError` from `ReferentialImpl._resolve_reference`
(`primitive_impls.py:116`). The Python class docstring (`primitive_impls.py:86`)
drops its "falls back to common_average" sentence.

**Rust must return a typed error, not panic.** `RefrainCore::new`
(`mobile.rs:108`) is already fallible — `Result<Arc<Self>, RefrainError>` with
`InvalidIr` and `UnknownControl` — but `Evaluator::new` (`eval.rs:822`) returns a
plain `Self`. A `panic!` inside `Referential::new` would therefore cross the
uniffi boundary as a panic rather than a typed error, giving cc-mobile a crash or
an opaque internal error for a case that **will** occur (see §"Audit"). So:

- Add `RefrainError::UnrealizableMontage { message }` (`mobile.rs:22`).
- Make `Evaluator::new` fallible and propagate `Result` to `RefrainCore::new`.

This deliberately does *not* follow the neighbouring `panic!`s for an unknown
active channel (`eval.rs:236`) or unknown reference (`eval.rs:261`). Those
predate the mobile FFI; a third panic on a reachable path would be a regression
in host-facing behaviour. Migrating those two is out of scope here.

Fixing only Python would **widen** the Python/Rust divergence rather than close
it, and `refrain-core/tests/equivalence.rs` would not reliably catch it: it is a
golden-vector output comparison over a fixed IR corpus, not a test of this
branch. The two crates are already lockstep since v0.14.0, so both fixes ship in
the same release by construction.

- **Who breaks:** file-replay or live sessions using `linked_ears` on a source
  without ear channels, on either evaluator. The existing Python comment states
  this case is real.
- **Migration:** declare `reference: "common_average"` explicitly — a one-word
  change that makes the intent stated rather than inferred.
- **Where the check lives:** runtime, in both evaluators, where `channel_names`
  is authoritative.

**Deliberately out of scope: a resolve-time pre-check against
`profile.channels`.** `brainbit_flex.json` documents its channels as placeholders
overridable via `Evaluator.live(channel_names=...)`, so a compile-time check
against them would reject valid setups. The lint below is a different check: it
is protocol-internal and needs no profile.

### The consistency lint

The audit (below) showed these protocols are **internally inconsistent**, and
that the inconsistency is checkable without an amp profile at all:

> `reference: "linked_ears"` requires ear electrodes to exist in the source, but
> `requires.channels = ["Pz"]` never asks for them. The protocol depends on
> channels it does not declare.

The resolver therefore gains a lint:

> If an `input`'s montage resolves to `reference: "linked_ears"`, then
> `requires.channels` must declare at least two of A1/A2/M1/M2/T9/T10.

```
ResolveError: input "raw" uses reference: "linked_ears", which needs >= 2 of
  A1/A2/M1/M2/T9/T10, but requires.channels declares ["Pz"].
  Add the reference electrodes to requires.channels, or use
  reference: amp.reference / "common_average".
```

This fires with `amp=None`, so it runs in `refrain-protocols` CI and catches all
21 affected protocols **at compile time in CI** rather than at session start on a
patient's device. It makes the runtime raise a backstop rather than the only
defence.

It is **breaking for the corpus**: all 21 generic `linked_ears` protocols fail it
today. That is the finding, not a side effect — but it means the lint cannot land
before those declarations are fixed. See §"Rollout" for the sequencing.

Scope note: the lint covers `linked_ears` only. `common_average` needs no
particular channel, `device` needs none, and a literal channel name is already
checked at runtime. Generalising it is not needed.

## Testing

New: `tests/test_amp_reference_fold.py`, mirroring the structure of
`refrain-protocols`' `tests/test_brainbit_mode_collapse.py` — the precedent that
proved the `mode` fold is equivalence-preserving.

| Test | Proves |
|---|---|
| `amp.reference` + `brainbit_flex` resolves to an IR byte-identical to literal `reference: "device"` | The fold is transparent |
| `amp.reference` + `q21` ≡ literal `reference: "linked_ears"` | The fold is transparent across profiles |
| `resolve(amp=None)` + `amp.reference` → `ResolveError` | Fail closed (no profile) |
| Profile without `reference` + `amp.reference` → `ResolveError` | Fail closed (no field) |
| `amp.adc_bits` → `ResolveError` naming allowed fields | The allowlist holds |
| Profile with `reference: "bogus"` → `AmpProfileError` at load | Load-time validation |
| **Whole corpus resolves unchanged with `amp=None`** | **The release is additive** |
| `linked_ears` on an earless source raises (Python) | The break is intentional |
| `linked_ears` on an earless source returns `RefrainError::UnrealizableMontage` (Rust) | The fix is not Python-only, and reaches the host as a typed error |
| Explicit `common_average` still works, both evaluators | The migration path exists |
| `linked_ears` + `requires.channels` without ears → `ResolveError` at `amp=None` | The lint fires in CI, no profile needed |
| `linked_ears` + `requires.channels` declaring A1/A2 → resolves | The lint does not over-fire |
| A single-channel `linked_ears` source never yields an all-zero stream | The flatline is gone, not relocated |

The first row is the load-bearing test: if the fold is transparent, everything
downstream (IR-JSON, signatures, `content_hash`) is unaffected by construction.

## Rollout

**The flatline fix does not live in this repo.** A verification pass on
2026-07-17 (see §"Why the protocol edit is not the fix") disproved an earlier
claim that honest `requires.channels` declarations would close the exposure. The
compile pipeline is hardcoded to `resolve(amp=None)` (`compile_json.py:4`) and the
sidecar `/compile` API has no amp field (`server.py:24`), so the channel guard at
`resolver.py:313` never runs for an assigned protocol regardless of what the
protocol declares. The immediate defect fix is a device-compatibility check at the
**portal or mobile** layer, tracked separately; it is out of scope for this
engine spec.

What this spec's steps below actually accomplish: they make montage resolution
honest inside the engine and the library, which is a *prerequisite* for the
portal/mobile fix to be correct (the compat check needs protocols to declare the
channels they depend on) — not a substitute for it.

1. `refrain` PR: profile field + load validation, `amp` namespace + allowlist,
   the consistency lint, the `linked_ears` runtime fix (both evaluators),
   `RefrainError::UnrealizableMontage` + fallible `Evaluator::new`, tests.
2. Populate `reference` on the three shipped profiles.
3. Release **v0.15.0**. `refrain` and `refrain_core` are lockstep since v0.14.0:
   bump **both** `pyproject.toml` + CHANGELOG in a `release: v0.15.0` PR, then tag
   the merge commit. Never tag before that PR merges or the published wheels are
   mislabeled. The CHANGELOG needs explicit **BREAKING** entries for the
   `linked_ears` runtime change and the consistency lint.
4. `refrain-protocols`: make the 21 generic `linked_ears` protocols honest
   (`["Pz"]` → `["Pz", "A1", "A2"]`) so the step-1 lint passes, then repin CI from
   v0.14.0 to v0.15.0 and confirm the suite, the catalog gate, and the fuzz gate
   (26/38 fuzzed, 0 violations) stay green. The declaration edit and the lint must
   land together: the lint rejects exactly the declarations this step fixes.

**Ordering is load-bearing.** The `refrain-protocols` declaration edit (step 4)
must not precede the lint (step 1/3) into `main` alone — the guard that would give
it teeth (`resolver.py:313`) only fires when an amp is passed, which the portal
does not do, so on its own the edit changes nothing observable and can drift back.
Land it with the lint, which enforces it in CI.

### The separate, higher-priority defect

The silent flatline (§"Audit") is a live patient-facing defect that this engine
release does **not** close. It needs a device-compatibility gate where assignment
actually happens — the portal passing the target amp to a sidecar that accepts one
(which itself needs the amp added to `CompileRequest`, a separate `refrain` change),
or a mobile-side check that the assigned IR's required channels are a subset of the
device's channels. That work is not scheduled and has no Linear issue. It should be
filed and prioritised independently of, and ahead of, this spec.

## Audit (completed 2026-07-16)

Run before planning, to decide whether the `linked_ears` break was safe to ship.
It found the opposite question was the right one: **the current behaviour is a
silent flatline.** The engine break fixes the montage-substitution half of it; the
half that admits a generic protocol onto a BrainBit in the first place is a
portal/mobile gap this spec does not close (see §"Root cause").

### cc-mobile

**Bundled protocols: unaffected.** All seven `assets/nf/*.refrain` use
`reference: "device"`; `linked_ears` appears nowhere in the app.

**Assigned protocols: exposed.** The chain:

1. `src/nf/assignedNfb.ts:27-30` — channel names come from the IR when it declares
   them, else `BRAINBIT_FLEX.channelMap`.
2. Generic `smr_up_c4.refrain` declares `channels = ["C4"]` with
   `reference: "linked_ears"`.
3. `Referential::new(active="C4", reference="linked_ears", channels=["C4"])` finds
   no ear electrodes → `cand.len() < 2` → `None`.
4. `run()` (`eval.rs:276`): `None => row.iter().sum() / row.len()` — the mean of a
   **single** channel is that channel.
5. `active - refv` = **C4 − C4 = 0**.

The protocol does not run on a different montage. It runs on **identically zero**,
silently, with no log. Python does the same (`raw_chunk.mean(axis=1)` over one
column).

This is not one protocol:

> **All 21 generic `linked_ears` protocols declare zero ear electrodes in
> `requires.channels`. Nineteen are single-channel — dead signal. The two
> two-channel cases (`faa_f3f4`, `alpha_coherence_c3c4`) degrade to a half-bipolar
> `(C3 − C4)/2` — wrong, but non-zero.**

Several (`alpha_theta_pz`, `faa_f3f4`) do not use `mode`, so the staging
version-skew 422 does not mask them. Any protection today is accidental.

### Root cause

Two independent faults compound:

1. **Nothing validates the protocol against the target device at assignment
   time.** The portal sidecar compiles with `resolve(amp=None)` (`compile_json.py:4`),
   and its `/compile` API has no amp field (`server.py:24`), so the channel guard
   at `resolver.py:313` never runs. A generic protocol authored for a 256 Hz,
   ear-referenced amp is compiled and assigned to a BrainBit client with no
   objection.
2. **The montage layer then substitutes instead of failing.** `linked_ears` finds
   no ear electrodes and silently degrades (§"Breaking change"), and for a
   single-channel source that degradation is an all-zero stream.

Fault 2 is what this spec fixes. **Fault 1 is the one that actually admits the bad
assignment, and it lives in the portal/mobile, not here** — see §"Why the protocol
edit is not the fix".

### Why the protocol edit is not the fix

An earlier draft proposed adding the reference electrodes to `requires.channels`
("step 0") on the theory that honest declarations would activate the
`resolver.py:313` guard. A verification pass on 2026-07-17 disproved it:

- `resolve(<generic protocol>, amp=None)` — the portal's actual path — **compiles
  cleanly**; no channel check runs. Confirmed live.
- `resolve(<generic protocol>, amp=brainbit_flex)` **does** reject — but on
  *sample rate* (`>= 256 Hz` vs the profile's 250), before the channel check is
  even reached. Confirmed live.
- The sidecar cannot pass an amp anyway: `compile_json.compile_to_ir_json` hardcodes
  `resolve(amp=None)` and `CompileRequest` (`server.py:24`) exposes only
  `sample_rate_hz` and `bindings`.

So the guard is real and would already reject these protocols for BrainBit *if the
amp were passed* — with or without the electrode edit. The gap is entirely that
the amp is never passed. Editing the 21 protocols is still correct hygiene (it is
required for the lint to pass and for eventual re-authoring), but it does not, by
itself, stop the flatline. It is therefore folded into rollout step 4, not
promoted to an emergency pre-release "step 0".

### Other surfaces

- **`coherence-portal`: not affected.** It compiles only (`resolve()` → IR-JSON)
  and never constructs a `Referential`.
- **`coherence-recorder` / `refrain run`:** Python path. Same exposure in
  principle; the Python evaluator at least logs the substitution. Not audited in
  depth — the fix is identical and step 0 closes the library-side cause for both.

## Non-goals

- **`requires` neutrality (coupling, sample rate).** Not an engine gap. A neutral
  protocol states its true DSP minimum once and the resolver validates it against
  the profile. This is authoring work and belongs to sub-project #4.

  Evidence that the generic set's number is drift rather than a derived bound:
  its highest band edge is **45 Hz** (the corpus edges are 20/22/30/45), which
  needs on the order of 120 Hz — yet it requires `>= 256 Hz`, roughly double.
  256 is a power of two, not a filter constraint. The cost is real: `openbci_cyton`
  runs at 250 Hz, so all 21 of those protocols are **silently OpenBCI-incompatible
  today** on an amp the engine already ships a profile for.

  The 250-vs-256 delta itself is inert. The only 100 Hz band edges in the corpus
  are the four BrainBit EMG guards, and they sit at 0.800 × Nyquist at 250 Hz
  versus 0.781 × at 256 Hz — indistinguishable, and both in poorly-conditioned
  filter territory for an order-4 bandpass. That marginality is independent
  corroboration for sub-project #2's `clean_hf_floor`: the BrainBit EMG guard is
  questionable on filter-design grounds as well as on the amplifier-noise-floor
  grounds its own protocol comment argues.
- **Capability-conditional decls** (`when amp.clean_hf_floor`) — sub-project #2.
  This spec deliberately chooses a syntax that makes #2 an extension of the same
  namespace.
- **Library re-authoring or retiring `brainbit/`** — sub-projects #4 and #7.
- **A resolve-time `linked_ears` check against `profile.channels`** — the profile's
  channels are overridable placeholders. The consistency lint is a different
  thing: protocol-internal, profile-free, and in scope.
- **Migrating the pre-existing `panic!`s** at `eval.rs:236` / `eval.rs:261` to
  typed errors. They predate the mobile FFI and deserve the same treatment, but
  not here.
- **Exposing further amp fields.** Additions to the allowlist are a deliberate,
  versioned decision, not an implementation detail.

## Appendix: findings that shaped this design

1. **`reference` is a union type**, not a label: three operation selectors
   (`REFERENCE_KEYWORDS`, `primitive_impls.py:77`) or a physical channel name
   (`primitive_impls.py:134`). It selects a montage *operation*, which is why the
   exposed vocabulary must stay closed.
2. **`linked_ears` silently degrades** to common-average on earless sources — in
   both evaluators (`primitive_impls.py:129`, `refrain-core/src/eval.rs:250`), and
   the Rust one does not even log it. This is the third instance in this platform
   of one bug class — a plausible default silently substituted: baseline seeding
   falling back to the 60th percentile instead of the 40th; `thr_uv` missing from
   the host seed tables after the Phase-2 collapse; and this. The class, not the
   instance, is what this design refuses.
3. **Omission already means something.** `reference` defaults to `"linked_ears"`
   (`src/refrain/editor/catalog.json:24`), so "omit the arg to ask the amp" was
   rejected: it would silently change the montage of every existing protocol.
4. **The engine already models three amps** (`brainbit_flex`, `openbci_cyton`,
   `q21`) while the library forks for one. The O(protocols × devices) cost is
   already accrued and unpaid for Q21 and OpenBCI.
5. **The guard was already there.** `resolver.py:313` has always rejected a
   protocol whose `requires.channels` the connected amp cannot supply. The
   flatline is not a missing check — it is a correct check routed around by
   protocols that under-declare what they depend on. That is worth remembering
   the next time something silently misbehaves here: look for the guard that
   exists and is being bypassed before adding a new one.
