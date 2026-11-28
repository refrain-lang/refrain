# Follow-up: an ambiguous grammar silently renames an output channel

> **Closed 2026-11-28.** `UNIT` carries the word-boundary guard suggested below, a seed block refuses
> a field it does not define, and `seed.target_pct` refuses a literal in a unit other than `%`. It was
> reopened by a trial protocol written one field per line, which muted a whole replayed session. The
> docs test at the end of this note is still not written. What follows is the record as filed.

Found while implementing REC-23 Task 4. **Not a REC-23 bug** — pre-existing, latent, nothing in
the repo trips it today. Filed here so it does not die with this session's workspace.

## It is a grammar ambiguity, not a lexer bug

`src/refrain/parser.py` builds Lark with `parser="earley"`, whose default lexer is *dynamic* —
there is no tokenizing pass to probe. Two optional pieces collide:

```
assignment: NAME "=" expression ";"?      # the ";" is OPTIONAL
number_with_unit: NUMBER UNIT?            # the UNIT is OPTIONAL
UNIT: "uV2" | "Hz" | "ms" | "min" | "s" | "uV" | "C" | "%"
```

So `a = 1.0` followed by `sustained = 2.0` genuinely parses two ways, and Earley's ambiguity
resolution picks the wrong one. With `ambiguity="explicit"` both appear:

```
_ambig
  start                          start
    assignment a (1.0 s)           assignment a (1.0)
    assignment ustained (2.0)      assignment sustained (2.0)
```

## Minimal reproduction

```python
from refrain.parser import _parser, _strip_block_comments
MIN = 'protocol "p" { output { a = 1.0\n sustained = 2.0 } }'
tree = _parser().parse(_strip_block_comments(MIN))
print([t.value for t in tree.scan_values(lambda v: getattr(v, "type", None) == "NAME")])
# -> ['a', 'ustained']        and the UNIT list is ['s']
```

End to end, `list(resolve(parse(...)).output)` gives `['a', 'ustained']`, and `a` silently
acquires `unit='s'`.

## How it surfaces

Silently, as a renamed output channel. No warning, no error. The host asks for `sustained`,
finds nothing, and the preceding binding has gained a time unit it never declared.

## Trigger condition

The **preceding** assignment must end in a bare number with no unit and no semicolon, and the
next identifier must start with one of the seven UNIT strings that can begin an identifier
(`%` cannot). Avoided by: a `;` on the previous line, a unit on that number, a non-numeric
right-hand side, or being first in its block.

| name | becomes | | name | becomes |
|---|---|---|---|---|
| `sustained` | `ustained` | | `Cz_gain` | `z_gain` |
| `smr_gain` | `mr_gain` | | `Hz_meter` | `_meter` |
| `minutes` | `utes` | | `uV_gain` | `_gain` |
| `msg` | `g` | | `uV2_gain` | `_gain` |

`Cz_gain -> z_gain` and `smr_gain -> mr_gain` are the realistic ones for this codebase.
Lowercase `h` and `v` are NOT units, so the affected set is `uV2/Hz/ms/min/s/uV/C`.

## State of the docs

`docs/PRIMITIVES.md`'s `sustained_cue` example is safe **as written**, because it is the first
binding in its `output` block. Inserting any bare-number assignment above it makes it
`ustained_cue`. One line away from being wrong.

## Suggested fix

A word-boundary guard on `UNIT` (it must not be followed by `[A-Za-z0-9_]`), or make the
assignment separator mandatory. The first is smaller and does not touch existing protocols.

---

## Addendum (REC-23 Task 11): it is no longer latent — the SPEC's own `scale` example trips it

The section above says "nothing in the repo trips it today". That is still true of every
`.refrain` file, and it is **no longer true of the documentation**. `docs/SPEC.md:769-782` is
the worked example of `scale`, a feature shipping in 0.25.0, and it is written in the
multi-line form with a bare-number `target_pct` immediately above `scale`:

```refrain
    seed = percentile {
      from       = "emg_envelope"
      window     = 60 s
      target_pct = 50                // the median
      scale      = 3.0               // ...times three
    }
```

Parsed verbatim (comments and all — a `//` comment does not break the collision), that gives:

```
from = "emg_envelope"      window = 60 s
target_pct = 50.0 s        cale = 3.0          # <- the field is called "cale"
```

and resolves to `target_pct = 50 s`, `scale = 1.0`. A practitioner who copies the spec's own
example gets a muscle gate at their warm-up **median** instead of three times it, with no
warning — the precise mis-calibration `scale` exists to prevent, in the more trigger-happy
direction.

Two independent checks would each have caught it, and neither exists:

- `_resolve_control_seeds` has no unknown-field check on a `percentile` seed block, so the
  field named `cale` is accepted in silence. (`_resolve_inhibit` had the same hole; Task 6
  closed it for `kind` by validating the value set, and the comment there says why.)
- `target_pct` accepts a time-dimensioned literal (`50 s`) against a dimensionless percentile
  slot without complaint.

**Affected identifier set, narrowed.** Only a field name beginning with `s` or `ms` and
following a bare number is at risk; `min`, `max`, `mode`, `unit`, `label` and `range` are all
safe in practice. In the language as it stands that means `scale` (a seed rule), and `step` /
`say` (an autopilot block) — every shipped autopilot block uses the one-line `;` form, so none
is affected today.

**What Task 11 did about it.** Nothing to the grammar: out of scope. `render.py`'s
`_render_seed` emits `scale` *before* `target_pct`, where it can only ever follow a `window`
line that always carries a unit, with a comment saying why and a round-trip test that is red
if the two lines are swapped back. The editor therefore cannot emit the broken form. The
SPEC example, the missing unknown-field check and the `target_pct` dimension hole are all
still open.

---

## Controller addendum (REC-23, after Task 11's interim report): two more living-doc instances, and the asymmetry that matters

I verified the addendum above by parsing the committed fixture against the spec's formatting — the
committed `bench/protocols/seed_scale_emg.refrain` uses the one-line `;` form and resolves
`scale = 3.0` with a dimensionless `target_pct`; the spec's multi-line form resolves `scale = 1.0`
with `target_pct` carrying `unit='s'` and `time=1`. Confirmed exactly as reported.

Then I swept for the shape rather than for the one instance.

### The sweep, and its reach

A bare number at end of line (optionally trailed by a `//` comment) followed by a line whose first
token is an identifier beginning with `s`, over **125 `.refrain` and `.md` files** under `docs/`,
`examples/`, `bench/`, `tests/fixtures/`, `src/refrain/` and `refrain-core/tests/`. Ten hits. Six are
Python inside plan documents (`scn =`, `scenario =`, `scenarios =`, `seen_first_muted_done =`,
`sorted_iv =`) and are not refrain source. **Four are refrain:**

| File | Pair | Status |
|---|---|---|
| `docs/SPEC.md:776-777` | `target_pct = 50` → `scale = 3.0` | **silently wrong** |
| `docs/SPEC.md:852-853` | `round_to = 0.01` → `say = "Crossover target"` | **fails loudly** |
| `docs/AUTOPILOT-AUTHORING.md:182-183` | the same pair | **fails loudly** |
| `docs/superpowers/specs/2026-09-24-protocol-autopilot-design.md:197-198` | the same pair | historical design doc, left alone |

That is the whole of those six trees for that shape. It is not a claim about any other shape, and
`%` cannot begin an identifier so it is excluded by construction.

### The asymmetry is the actionable finding

Both pairs hit the identical grammar collision. They end differently, and the difference is not in the
grammar:

```
$ # the SPEC's autopilot example, parsed verbatim
ResolveError: line 271:13: control 'crossover_target'.autopilot: unexpected field(s) ['ay']

$ # the SPEC's seed example, parsed verbatim
RESOLVED. scale = 1.0   target_pct = IRNumberLit(value=50.0, dims=Dimensions(time=1, ...), unit='s')
```

**The autopilot block validates its field names and refuses `ay`. The seed block does not, so it
accepts `cale` in silence and falls back to `scale`'s 1.0 default.** A practitioner who copies the
autopilot example gets an error message naming the field; one who copies the `scale` example gets a
working protocol that mutes at their median instead of three times it.

So the grammar hazard is the cause, but the missing unknown-field check on a seed block is what
converts a loud failure into a silently mis-calibrated session — and the autopilot block is the proof
that the check is cheap and already precedented in this resolver. **That is the single highest-value
line of this whole write-up**: close the seed block's unknown-field check and this class of bug becomes
noisy everywhere, whatever happens to the grammar.

### Verified fixes, held rather than applied

Both reorders were verified by parsing, and are deliberately **not yet applied** — Task 11's
implementer was mid mutation-sweep with a harness that manipulates the working tree, and editing files
under a running sweep is how you get a false result. They are applied as a separate commit once Task 11
has committed.

- `docs/SPEC.md:769-782` — put `scale` above `target_pct`, so it follows `window = 60 s`, which always
  carries a unit. Verified: `scale=3.0`, `target_pct.unit=None`. (A semicolon between the two also
  works and was verified, but the reorder keeps the multi-line style the surrounding spec uses.)
- `docs/SPEC.md:852-853` and `docs/AUTOPILOT-AUTHORING.md:182-183` — put `say` above `round_to`, so the
  bare number is last in its block and is followed by `}`. Verified: the `['ay']` error is gone.

In both cases the ordering is **load-bearing and invisible**, which is why the next item matters more
than either edit.

### The fix that would actually have caught all of this

A test that extracts every ```refrain block from the living docs and parses it. It would have caught
both examples, and it generalises past this hazard to every other way a worked example can rot.

It does not exist, and not out of negligence: many ```refrain blocks in `docs/SPEC.md` are **fragments**
— a bare `controls { }` section with no surrounding `protocol`, shown to illustrate one field — so the
test needs a way to know which blocks are complete protocols. A fenced-block info string
(```` ```refrain-complete ````) or a one-line marker comment would do it. That is a small, well-shaped
piece of work and it is the recommendation of this document.

### One more instance of a pattern, recorded without blame

The addendum above corrects the original claim "nothing in the repo trips it today", which was true of
`.refrain` files and false of the docs. Its own narrowing then says of `step` / `say` that "every
shipped autopilot block uses the one-line `;` form, so none is affected today" — which is true of
`.refrain` files and false of the docs, in the same two places this sweep found.

That is the fifth sentence in this release to assert a list was exhaustive and be wrong, and it is the
most instructive of the five, because it is inside the correction of the fourth. The lesson is not
"check harder". It is that **"nothing is affected" is not a checkable sentence** — "no `.refrain` file
under these four trees is affected, and I did not sweep the docs" is.
