# Amp reference abstraction (`amp.reference`) — Design

- **Date:** 2026-07-16
- **Status:** design (approved; ready to plan)
- **Scope:** `refrain` engine only. No portal, mobile, or recorder change ships in
  this sub-project, and no protocol content changes. The single change outside the
  engine is a one-line CI version pin in `refrain-protocols`, which exists to
  *verify* the release is additive (rollout step 4).
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
| `src/refrain/primitive_impls.py` | `linked_ears` raises instead of degrading to common-average | **Yes** |
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

### Breaking change: `linked_ears` stops guessing

`ReferentialImpl._resolve_reference` (`primitive_impls.py:116`) raises instead of
returning `None`:

```
ValueError: referential: reference 'linked_ears' needs >= 2 of
  A1/A2/M1/M2/T9/T10; source has ('Cz','F3','F4','Pz').
  Use reference: "common_average" explicitly if that is what you want.
```

The class docstring (`primitive_impls.py:86`) is updated to drop the
"falls back to common_average" sentence.

- **Who breaks:** file-replay or live sessions using `linked_ears` on a source
  without ear channels. The existing comment states this case is real.
- **Migration:** declare `reference: "common_average"` explicitly — a one-word
  change that makes the intent stated rather than inferred.
- **Where the check lives:** runtime (`ReferentialImpl`), where `channel_names`
  is authoritative.

**Deliberately out of scope: a resolve-time pre-check against
`profile.channels`.** `brainbit_flex.json` documents its channels as placeholders
overridable via `Evaluator.live(channel_names=...)`, so a compile-time check
against them would reject valid setups.

This leaves one honest gap: a literal-`linked_ears` protocol compiled for BrainBit
passes compile and fails at session start. That is the generic set on BrainBit —
the combination nobody runs today (it is why the fork exists), and it disappears
when sub-project #4 re-authors those protocols onto `amp.reference`, which is
realizable by construction. Closing it here would cost more than it buys.

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
| `linked_ears` on an earless source raises | The break is intentional |
| Explicit `common_average` still works | The migration path exists |

The first row is the load-bearing test: if the fold is transparent, everything
downstream (IR-JSON, signatures, `content_hash`) is unaffected by construction.

## Rollout

1. `refrain` PR: profile field + load validation, `amp` namespace + allowlist,
   `linked_ears` fix, tests.
2. Populate `reference` on the three shipped profiles.
3. Release **v0.15.0**. `refrain` and `refrain_core` are lockstep since v0.14.0:
   bump **both** `pyproject.toml` + CHANGELOG in a `release: v0.15.0` PR, then tag
   the merge commit. Never tag before that PR merges or the published wheels are
   mislabeled. The CHANGELOG needs an explicit **BREAKING** entry for
   `linked_ears`.
4. `refrain-protocols`: repin CI from v0.14.0 to v0.15.0 and confirm the suite,
   the catalog gate, and the fuzz gate (26/38 fuzzed, 0 violations) stay green
   **with zero protocol edits**.

Step 4 is the acceptance gate: it proves against 39 real protocols that the
release is additive.

### Precondition (owner action, before step 3)

Audit the replay paths in `coherence-recorder` and `coherence-workstation` for
`linked_ears` against recordings without ear channels. The `linked_ears` break is
the one change that bites on upgrade **independently** of sub-project #4, and the
existing code comment asserts such recordings exist. This audit has not been done.

## Non-goals

- **`requires` neutrality (coupling, sample rate).** Not an engine gap. A neutral
  protocol states its true DSP minimum once and the resolver validates it against
  the profile. This is authoring work and belongs to sub-project #4. Evidence that
  the current numbers are drift rather than hardware: `openbci_cyton` runs at
  250 Hz while 21 generic protocols require `>= 256 Hz`, so the generic set is
  silently OpenBCI-incompatible today, and 256 is a power of two rather than a
  DSP-derived bound.
- **Capability-conditional decls** (`when amp.clean_hf_floor`) — sub-project #2.
  This spec deliberately chooses a syntax that makes #2 an extension of the same
  namespace.
- **Library re-authoring or retiring `brainbit/`** — sub-projects #4 and #7.
- **A resolve-time `linked_ears` realizability check** — see §"Breaking change".
- **Exposing further amp fields.** Additions to the allowlist are a deliberate,
  versioned decision, not an implementation detail.

## Appendix: findings that shaped this design

1. **`reference` is a union type**, not a label: three operation selectors
   (`REFERENCE_KEYWORDS`, `primitive_impls.py:77`) or a physical channel name
   (`primitive_impls.py:134`). It selects a montage *operation*, which is why the
   exposed vocabulary must stay closed.
2. **`linked_ears` silently degrades** to common-average on earless sources
   (`primitive_impls.py:129`). This is the third instance in this platform of one
   bug class — a plausible default silently substituted: baseline seeding falling
   back to the 60th percentile instead of the 40th; `thr_uv` missing from the
   host seed tables after the Phase-2 collapse; and this. The class, not the
   instance, is what this design refuses.
3. **Omission already means something.** `reference` defaults to `"linked_ears"`
   (`src/refrain/editor/catalog.json:24`), so "omit the arg to ask the amp" was
   rejected: it would silently change the montage of every existing protocol.
4. **The engine already models three amps** (`brainbit_flex`, `openbci_cyton`,
   `q21`) while the library forks for one. The O(protocols × devices) cost is
   already accrued and unpaid for Q21 and OpenBCI.
