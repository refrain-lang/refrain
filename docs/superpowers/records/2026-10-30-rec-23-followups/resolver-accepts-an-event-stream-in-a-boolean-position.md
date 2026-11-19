# Follow-up: the resolver accepts an event stream in a boolean position almost everywhere

**Status:** open. Pre-existing, nothing to do with the 0.25.0 declarations. The companion
document, `followup-engines-disagree-on-all-of-over-a-dwell.md`, is about the two engines
answering differently on four of these shapes; this one is about the language accepting
them at all.

## The hole

`dwell` and `dwell_rearm` are the only primitives whose output type is `event_stream`
(`src/refrain/primitives.py:725,747`). The resolver has exactly one check that an
`event_stream` is not being used where a boolean stream belongs — the ternary
*condition* position:

```
ternary condition must produce a boolean stream, got event_stream
```

Every other boolean position accepts one. Measured across 14 spellings:

| Position | Resolver |
|---|---|
| ternary condition, in `output { }` | **refuses** |
| ternary condition, in `reward { continuous }` | **refuses** |
| ternary condition, in a derive formula | **refuses** |
| `reward "<name>" { signal = dwell(...) }` | **refuses**, by a different check — "must be a [0,1] success metric" |
| `all_of` element, in `output { }` | accepts |
| `any_of` element, in `output { }` | accepts |
| `all_of` element with a genuine condition beside it | accepts |
| `all_of` bound bare to an output channel | accepts |
| `hold_for(condition: dwell(...))`, in `output { }` | accepts |
| `dwell(condition: dwell(...))`, a reward event | accepts |
| `all_of([dwell(...), above(...)])`, a reward condition | accepts |
| `hold_for(condition: dwell(...))`, a reward condition | accepts |
| `metric = dwell(...)`, on an inhibit | accepts |

So the type system knows the distinction — `event_stream` is a distinct `StreamType`, the
ternary check reads it, and the component-signal check catches it incidentally while
enforcing a `[0,1]` scalar — and ten places do not ask.

**It is not only boolean positions.** A `dwell` as the whole top-level expression of a
derive formula, of `reward { continuous }`, of a reward bundle's `continuous`, or of an
inhibit's `metric` also resolves: four *numeric* positions where an event stream is
accepted as a number. Rust evaluates each as the dwell's rising-edge stream; Python
raises on the first chunk (`'DwellResult' object has no attribute 'size'`, or a
`TypeError` from the inhibit comparison). Those four are also the positions where the
Rust core's `reset_held_outputs` over-reaches relative to the Python reference, which is
stated in `refrain-core/src/eval.rs` — so closing this hole would also make that
parity note moot rather than merely unobservable.

## What the engines then do with them

None of the ten is usable, and the failure is different on each side, which is the part
that makes this worth closing in the resolver rather than in either engine. Measured, no
freeze involved, never-dipping signal:

- **Four diverge silently.** `all_of`/`any_of` over a dwell in an output binding reads
  fully on from sample 0 in Python (`np.all` over a one-element *object* array folds
  `DwellResult` truthiness into a 0-d `True`, which broadcasts across the chunk) and
  fires once at sample 63 in Rust. Detail and the verified mechanism are in the
  companion document.
- **Six raise in Python and produce something in Rust**: `ValueError: all input arrays
  must have the same shape`, `AttributeError: 'DwellResult' object has no attribute
  'shape'`, `AttributeError: ... no attribute 'astype'`, or `TypeError`, against a dead
  (all-false) stream in Rust for five of them and 318 unmuted samples for the inhibit
  metric.

A resolve-time refusal turns all ten into one clear compile error at the position the
author wrote, which is where the mistake is.

## Reach of a refusal

**It moves no protocol that exists.** All 40 tracked `.refrain` files use `all_of` /
`any_of` over `above` / `below` / `inside` comparisons only; the nearest shapes are the
ordinary `dwell(condition: all_of([above(...) as "name", ...]))` reward conditions in
`realistic_smr`, `autopilot_alpha_theta`, `autopilot_staged`, `micro_05_reward`,
`micro_multi_leaf_control_absolute`, `smr_cz`, `smr_cz_brainbit` and
`alpha_theta_autopilot`. None nests an event stream. So the refusal is additive in the
only sense that matters for this release's invariant: no existing IR changes, because no
existing protocol reaches it.

Note what a refusal would *not* break: `output { fb = dwell(...) }` at the top level,
which is legal, documented (`docs/PRIMITIVES.md` — "the primitive is supported as a
direct `output` binding") and agrees across engines. The refusal is about an event
stream appearing as an *input* to something expecting a boolean, not about where a dwell
may be bound.

## Where it would go

`src/refrain/resolver.py` already has the ternary check; the same test wants to run for:

1. every element of an `all_of` / `any_of` array;
2. the `condition` argument of `hold_for`, `dwell` and `dwell_rearm` — a `condition`
   param accepting an `event_stream` is the root of five of the ten boolean spellings;
3. an inhibit's `metric`, which must be a numeric stream (it is already required to be a
   primitive call, but `dwell` is a primitive call);
4. a derive's whole expression, and `reward { continuous }` and a bundle's `continuous` —
   the three numeric positions where a bare top-level `dwell` resolves today. The ternary
   check already covers the ternary spelling in the first two of those, so what is
   missing is the bare one.

(2) is the general fix: the `"condition"` parameter kind is declared in the primitive
specs, so one check at argument-resolution time — "a parameter declared `condition` may
not receive `event_stream`" — covers `all_of`'s elements as well, since those resolve
through the same path. Worth confirming against the Rust IR deserialiser too: it accepts
whatever the wire carries, and if the refusal lands only in the Python resolver, a
hand-written IR-JSON could still reach it. `refrain-core`'s own validation is the
backstop, and `docs/CONFORMANCE.md` would be the place to state that an `event_stream`
in a condition slot is invalid IR rather than engine-defined.
