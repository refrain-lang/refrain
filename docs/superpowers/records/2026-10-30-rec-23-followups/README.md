# REC-23 deferred findings

Thirteen things found while building refrain 0.25.0 and deliberately **not** fixed in it. Each says what
it is, how it was verified, what it costs to leave, and what fixing it would involve.

They are here rather than in a tracker because they started in a git-ignored scratch workspace that was
about to be deleted, and a finding nobody can find is not a finding. Move any of them into a ticket
freely; this directory is a record, not a queue.

**The ones worth reading first are not the bugs.** Four of these are about why a defect was invisible,
and those generalise past the defect.

| File | What it is | Why it matters |
|---|---|---|
| `grammar-ambiguity.md` | A bare number swallows the first letter of the next field name when that letter is a unit | **It silently mis-parsed three worked examples in our own living docs.** One made a muscle gate three times too tight with no error at any stage |
| `resolver-accepts-an-event-stream-in-a-boolean-position.md` | Ten spellings put an event stream where a boolean belongs and the language accepts them | Reach stated as a probe: 14+3 spellings tried, 4 refusals found, 4 resolve and diverge, 6 resolve and raise in Python while Rust answers |
| `engines-disagree-on-all-of-over-a-dwell.md` | The two engines disagree on a shape that resolves **today**, with no new declaration involved | Python reads a channel fully on from sample 0 where Rust fires once at 63 — a silent always-on tone. Mechanism verified by running it, not inferred |
| `micro09-squared-units.md` | A threshold written in µV² and a resolver that cannot see through a `rectify` in metric position | The unit error matched **by accident**, so nothing in the compiler could ever have caught it. Also carries a correction of a claim the controller got wrong |
| `freeze-does-not-reset-the-reward-dwell.md` | A transport pause resets holds but not a top-level reward dwell, on either engine | The principle says it should; doing it moves 44 of 45 live protocols with nothing gating it |
| `reseed-rescue-of-a-failed-warmup.md` | Should a later successful measurement rescue a session whose warm-up failed? | A product question, not a bug. A trainee who loses three minutes to a loose electrode currently sits through the rest in silence |
| `schema-null-range-bounds.md` | Released IR schemas 0.1–0.5 reject a null range bound, which the emitter always writes for a rangeless control | Also: the schema suite validates golden vectors but **never the `examples/` tree** |
| `threshold-self-and-dangling-refs.md` | The resolver accepts a self-referential threshold, a mutually-referencing pair, and a dangling name | Found while closing a dependency walk transitively |
| `seed-scale-range-clamp-compares-face-values.md` | A seeded control's range clamp compares face values, so a time-typed control with a range in seconds would clamp 1000× wrong | Latent: no such control exists, and only the time dimension has multiple units |
| `editor-roundtrip-drops-a-unit.md` | A compound reward's threshold loses its unit on an editor round-trip | The round-trip suite compares **resolved output, not source text**, so it cannot see this class at all |
| `editor-validator-is-stale.md` | refrain-editor's validator rejected 24 of its own 41 canonical models (fixed in this release) | Kept for the shape: a duplicated constant is a smell; **a duplicate of a *derived* value guarantees drift** |
| `clinician-in-engine-source.md` | A retired word survives in 15 lines of engine source, one of which prints it and two of which become published mobile API docs | The vocabulary gate reads `docs/` only, so the source is where the next doc violation comes from |
| `release-zero-divergence.md` | An inhibit's `release: 0 ms` has a 200 ms hangover in Python and none in Rust | Pre-existing; the one place the two engines are known to disagree on a documented field |

Every decision made while building the release, with what each costs if wrong, is in
`../2026-10-30-rec-23-decisions.md`.
