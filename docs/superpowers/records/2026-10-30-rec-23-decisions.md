# REC-23 — every decision made during execution, and what each costs if wrong

Refrain 0.25.0 (forgiving holds, artifact gates, block re-seeding). Thirteen planned tasks plus one
inserted mid-flight, each implemented and reviewed by a separate agent, coordinated from one session.

A plan that runs without a human in the loop makes decisions the plan did not make. This file is the
record of those: 65 numbered rulings, what each decided, and what it costs if the decision was wrong.
**Read sections 1, 2 and 3. Section 4 is the full index, for when you want to look one up.**

Nothing here needs your approval to have happened — it has all happened, and it is all in git. The point
of the record is that you can find and reverse any of it without reading 23 commits.

---

## 1. The decisions that could actually hurt

Six, in the order I would want them checked.

**R28 — the units of the worked muscle-limit example. Yours, not mine.** You said: *"I don't use squares.
microvolts is where its at. thats our model."* So the example takes a square root and the limit is an
amplitude in µV. The rejected alternative would have declared the control as a power in µV². If I
mistranscribed your intent, the worked example in the spec computes the wrong kind of quantity, and it
is the example a practitioner copies.

**R39 — and this is why R28 mattered more than it looked.** The median commutes with the square root, so
"three times the median **power**" is arithmetically identical to "**1.73** times the median
**amplitude**". A practitioner writing `scale = 3.0` after reading the spec's "three times this person's
warm-up median" would have got a gate at 1.73× their median muscle amplitude — nearly twice as
trigger-happy as advertised, in the exact direction the feature exists to fix. Measured on the fixture's
own signal: 117.18 as committed versus 18.75 µV with the square root. Nothing in the compiler would ever
have caught it, because `rectify` in metric position cannot see its input's type, so the metric reported
µV and matched **by accident**. Cost if wrong: the multiplier does not mean what the prose says.

**R30 — the design's central promise was false of the code.** The design said a re-seed never overrides
a practitioner. Nothing disarmed a seed latch after it had fired, on either engine. So a re-seed at
block three would have silently taken back a hand-set threshold — and because both engines agreed, the
parity gate would have stayed silent. Decided: a host write to a control some block re-seeds disarms it
permanently; every other control behaves exactly as today. Cost if wrong: either a practitioner's number
is still overridden, or it is honoured somewhere it should not be.

**R64 — the two engines disagreed about what a transport pause resets, on six surfaces.** Python
restarted every hold anywhere; Rust restarted only the holds reachable from the output section. Measured
250 ms apart at 256 Hz. I ruled Python correct and Rust to match. Cost if wrong: a pause invalidates more
streaks than a host expects — the conservative direction, where nobody is credited for time nobody
observed. **The thing worth knowing is not the bug.** Three of those six surfaces were invisible to the
measurement the review used to find the first one: a first-chime index cannot see a hold that has already
satisfied when the pause lands. The fixtures were adequate; the probe was not.

**R24 — I overruled a reviewer and fixed a violation of our own invariant instead of documenting it.**
A hold written inside a reward condition received neither mask, so under an artifact label it banked
samples the engine had declared unreadable. The reviewer recommended a spec sentence and a follow-up. The
release's own argument is that crediting unobservable time biases who counts as a non-learner, so a path
that still credits it undercuts the thing being shipped. Cost: one extra commit on the hottest path in
the release, late. Recoverable by dropping it.

**R61 — I changed a validator in another repository.** The new gate found that refrain-editor's own
`validateModel` rejected 24 of its own 41 canonical models. Two edits made it agree with code already
shipping beside it. This sits inside the line I drew at R59 (no feature ports into that repo) and I think
the distinction holds — a validator that rejects its own canonical data is not a gate — but it is the one
place I widened scope into someone else's project. It is on an unpushed branch their PR review will see.

---

## 2. Where I was wrong

Six, and the first is the one I would most want you to know about.

**I contradicted my own verified ruling, 49 rulings later.** In R9 I checked whether `freeze` holds the
last value, found it does not — both engines build the same gate — corrected four documents, and wrote
*"surfaced to the user in the final rulings list so they can overrule."* Then in **R58** I justified the
editor's new refusal by saying a mute silences output while a freeze holds its last value, *"clinically
different."* That contradicted R9, and it reached the changelog, a source comment and a test docstring
before a reviewer caught it. The conclusion survived — the editor must not rewrite an author's `freeze`
as `mute`, because the stored form differs whatever the runtime does — but the reasoning was false. **The
ledger contained the verified answer and I did not read it.** That is what the ledger is for.

**R45's reasoning was invented.** I wrote that the threshold graph is acyclic "because a cycle is already
refused." Cycles are **not** refused; a self-referential threshold and a mutually-referencing pair both
resolve today. What actually makes the walk terminate is different and the implementer found it. The
ruling was right; my defence of it was not.

**R49's premise was false.** I accepted that the mobile bindings stay untouched and their gate stays
green. Both halves were untrue at that commit: six doc-comment lines added to explain why a field was
*absent* had themselves staled the generated Swift and Kotlin. A doc comment on an exported type is code.

**A test I mandated could not fail.** I specified `expect(() => validateModel(model)).not.toThrow()`
against a function that returns a result object and never throws. It would have passed on all 41 models
while 24 were invalid — inside the test written to find exactly that.

**I asserted a missing language feature that had existed for three commits.** I said a thermal protocol
has to lie about its units because refrain has no temperature type. It has one, plus a °C unit and a way
to declare a channel's real unit — added in a commit whose own code comments name that exact protocol as
the motivation. I took the claim from the file's header, written before the feature landed. A comment is
not a source.

**I relayed "byte-identical" from a reviewer without checking it.** The two gate functions have the same
semantics and different bytes. Checkable claims should be checked, not passed along.

Implementers corrected me seven times and were right seven times. That is the loop working, not failing —
but every one of those seven cost a round.

---

## 3. Still open, and yours to decide

| Question | Why it is yours |
|---|---|
| Should a successful re-seed **rescue** a session whose warm-up measurement failed? | A trainee who loses three minutes to a loose electrode currently sits through the rest in silence. Rescuing is kinder and less legible. `2026-10-30-rec-23-followups/reseed-rescue-of-a-failed-warmup.md` |
| Should released IR schemas 0.1–0.5 be widened to accept a null range bound? | Retroactively changes what already-shipped documents claim to be valid against. R27 |
| Should the resolver reject an event stream written into a boolean position? | Three spellings resolve today, and one of them makes the two engines disagree with no pause involved. |
| Should a transport pause also reset a top-level reward dwell? | The principle says yes; doing it moves 44 of 45 live protocols with nothing gating it. R64's follow-up |
| Should the grammar stop a bare number swallowing the next field's first letter? | It silently mis-parsed three worked examples in our own living docs. **Decided 2026-11-28: yes, done.** |
| Should `validateModel`'s missing field-name check be added to seed blocks? | It is the difference between a loud failure and a silently mis-calibrated session. R55. **Decided 2026-11-28: yes, done in the resolver.** |

---

## 4. The full index

Terse by design. Every ruling's full text, with its reasoning, is in the commits and the task reports.

| # | Decision | Cost if wrong |
|---|---|---|
| R1 | Corpus guard compares only paths already in the snapshot, plus an explicit `added` list | A new fixture's IR is pinned from the commit that adds it, not from 0.24.0 |
| R2 | Two hold tests assert `[9:].all()`, not `.all()` | A bug that never raises holds could pass; mitigated by exact-index siblings |
| R3 | A test helper's three contested call sites follow the docstrings, not the expressions | Two-thirds right — the implementer showed my third amendment double-negated |
| R4 | The seed range clamp attaches to `scale`, not to every seed | An unscaled seed can still write outside its declared range, as today |
| R5 | The catalog slot is `tolerance` and carries its own leading separator | The model carries a DSL fragment, as every other raw slot already does |
| R6 | Two design constants are not binding; derive them and report a contradiction | The design's headline measurement is not a literal in the suite |
| R7 | Which refrain-editor checkout, which branch, never commit on main, never push | A local branch you delete with one command |
| R8 | A test helper keeps `duration` above every `tolerance` it passes | Caught immediately by a resolve error |
| R9 | `freeze` does not hold the last value; four documents corrected to match the engines | If it was meant to, we recorded shipped behaviour as intended |
| R10 | R9's scope widened to three more files | Three prose lines change meaning; no behaviour moves |
| R12 | A two-line change outside the brief's file list accepted | Two lines of argument renaming in a shared helper |
| R13 | A control-bound `duration` below `tolerance` is documented, not refused | A retuned control can produce a hold that never drops |
| R14 | A white-box assertion on the dip counter is correct here | A unit test reaches into a private attribute |
| R15 | A mask-length check promoted into the fix round | One guard clause on a warm path |
| R16 | The version predicate reads the primitive registry and walks bundles | A document could bump version unnecessarily |
| R17 | **The approved design contained a false invariant**; corrected in the document | An approved design was edited during implementation; reversible |
| R18 | The fuzzer oracle fires on a rising edge | The oracle's prediction shape changed with no brief requesting it |
| R19 | Two one-line fixes batched rather than deferred | Two trivial edits |
| R20 | A scoped re-review skipped for a two-line post-approval cleanup | Two lines went in without a dedicated review seat |
| R21 | An implementer's departure ratified — the brief taken literally was a bug | The artifact sub-mask reads a cached gate |
| R22 | Python left two hold masks on the evaluator permanently; made it match Rust | It was worse than I described — also a hard crash on a shorter chunk |
| R23 | The IR-0.6 trigger rule was understated in two places; routed to two tasks | A protocol author would not find their trigger in the table |
| R24 | **Fixed an invariant violation rather than documenting it** (see §1) | One extra commit on the hottest path, late |
| R25 | The mask covers the whole reward stage, not just the condition | A hold in a continuous reward freezes where someone expected it to run |
| R26 | A pre-existing Rust panic on a positional `absolute(<control>)` fixed | One match arm on a path that could only panic before |
| R27 | Released schemas 0.1–0.5 left alone | A rangeless control fails its own schema, as it already does |
| R28 | **The worked example's units — your call** (see §1) | The example computes the wrong kind of quantity |
| R29 | A compile-time window check refuses only when the budget is knowable and short | An open-mode protocol fails closed at run time instead |
| R30 | **A host write disarms a re-seeding control** (see §1) | A practitioner's number is overridden, or honoured wrongly |
| R31 | An explicit per-phase marker, not elapsed time | A frozen clock re-fires the re-seed every chunk |
| R32 | A test must not pass on a backend it cannot observe | A vacuous pass; an honest skip is better |
| R33 | Both corpus counters move whenever a protocol is added | A stale counter survives to the release gate |
| R34 | **A catalog change must leave 41 parity tests in another repo byte-identical** | 41 of 41 red in a repo this release only resyncs |
| R35 | Four reward-matching arms, not three; a case per family | One reward family silently drops the new field |
| R36 | A fixture-count floor was arithmetically impossible; assert the real count | A loop with zero assertions passes |
| R37 | Read the previous task's report to learn your own scope | A source change in another repo goes unmade |
| R38 | Verify both counters against git, not memory, as a release gate | A stale counter ships |
| R39 | **Keep `scale = 3.0`; put muscle in the signal** (see §1) | The multiplier does not mean what the prose says |
| R40 | Upgraded a Minor: the arithmetic the fix round existed to prove was pinned by nothing | The one defect you caught by eye is the one no test guards |
| R41 | Refuse only when **every** route into a block is short | A protocol that cannot fill fails closed at run time |
| R42 | A cross-task contract written into the brief, not held in my head | A compile check computes a budget against a rule the engine ignores |
| R43 | The spec had no blocks section at all; one was added | A documentation gap that predates the release |
| R44 | The downstream-hold set is scoped to the control, not the block | A hold reached only through a documented limit keeps a stale streak |
| R45 | Close the threshold walk transitively — **over-resetting is safe, under-resetting is the bug** | More holds reset than strictly needed |
| R46 | The hold set stays coarse for this release | Documented in both specs; a wire-format change belongs in its own release |
| R47 | Accepting advice disarms a re-seeding control; the autopilot applying its own does not | A person stops being re-measured after a human intervenes |
| R48 | A history field means "the measurement replaced", because Rust cannot reproduce the live value | The warm-up record reads null where a default might be expected |
| R49 | **The mobile bindings deliberately do not carry the new field** (premise was false, see §2) | A mobile host infers from status alone, as today |
| R50 | The corpus revision question dissolved — all three candidates are fixture-identical | Fixtures from a revision nobody named |
| R51 | The advice door takes only the post-fire arm | A seeded control with no re-seed behaves exactly as today |
| R52 | Those six doc-comment lines become `//` | A mobile host learns the field is absent from the docs, not the API |
| R53 | **A warm-up seed that fails closed is terminal** | A session is never rescued by a later measurement |
| R54 | A mandated schema test could not fail; give it the negative case | Documentation masquerading as a gate |
| R55 | The resolver's missing seed field-name check is **not** fixed in this release | A mis-parse stays silent instead of becoming loud |
| R56 | Three mis-parsing worked examples in living docs fixed | 0.25.0 would ship with its own example mis-compiling |
| R57 | Doc fixes held while a review was in flight | An amend underneath a reviewer corrupts its result |
| R58 | **The editor refuses a `freeze` artifact inhibit** (reasoning was false, see §2) | The editor returns source the author did not write |
| R59 | A tripwire in the other repo, not a feature port | The port stays undone, with a red build as the reminder |
| R60 | Two checkouts exist; the pre-flight gate names both | A destructive script succeeds against the wrong tree |
| R61 | **Fixed another repo's validator** (see §1) | Two edits their own review will see |
| R62 | A validator's checks must match what the renderer dereferences, in both directions | Accepts what throws, or refuses what works |
| R63 | Accepted a test-only round without a fourth re-review | A test-only change on an unpushed branch |
| R64 | **Python is correct about what a pause resets; Rust matches it** (see §1) | More streaks invalidated than expected — the safe direction |
| R65 | The six surfaces get committed per-surface coverage, asserting sample counts not first indices | A regression returns five of six surfaces to disagreeing silently |

R11 does not exist. It is a numbering skip, not a lost decision — checked against this workspace and the
whole session transcript. Said plainly because inventing one to fill the gap would be the exact failure
this release spent itself finding.
