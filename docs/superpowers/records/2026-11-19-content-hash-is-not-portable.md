# `content_hash` is not portable across CPU architectures

**Found:** 2026-11-19, while fixing the CI failure that `tests/test_ir_corpus_identity.py`
caused on `main` immediately after 0.25.0 merged.
**Status:** open. Pre-existing — not introduced by 0.25.0.
**Severity:** does not affect any running protocol. Affects anything that treats
`content_hash` as an identity for a protocol.

## The claim and the reality

`compile_json` reports a `content_hash` and documents it as an integrity check:

```python
def _content_hash(canonical: str) -> str:
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

where `canonical` is `json.dumps(ir_to_json_obj(ir, sample_rate_hz=...), indent=2)`
— the verbatim bytes the docstring promises `sha256(bytes) == content_hash` holds
for.

That holds. What does not hold is the implicit assumption that compiling the same
protocol text twice yields the same hash. **It does not, if the two compiles happen
on different CPU architectures.**

## Mechanism, measured

The IR embeds filter coefficients designed by scipy, serialized at full double
precision. Those coefficients differ in their last bits between macOS/arm64 and
Linux/x86_64 — different libm, different BLAS/LAPACK build, different FMA
contraction in the polynomial work. `json.dumps` writes the difference out, and
the hash changes.

The correlation is exact, not probabilistic. Across the 21 bench protocols in the
0.25.0 corpus:

| Protocols | Computed floats in the IR | Hash on Linux/x86_64 vs the macOS/arm64 fixture |
|---|---|---|
| 17 | 10 to 190 | **all differ** |
| 4 | 0 | **all match** |

The four that match are `micro_06_coherence`, `micro_passthrough_identity`,
`seed_exprpos` and `seed_smr_baseline`. Zero contradictions in either direction.

Ruled out along the way: numpy 2.5.4 → 2.5.5, which landed between the last green
CI run and this one. The test passes under both locally, checked in a clean venv.

## What this does and does not break

**Does not break:** protocol behaviour, or backend equivalence. Both engines read
the coefficients off the wire rather than designing their own, which is precisely
why they stay sample-identical — the coefficients are *on* the wire so that they
cannot disagree. A protocol compiled on one machine and run on another runs the
bytes it was given.

**Does break**, for anyone who assumed otherwise:

- A cache or content-addressed store keyed on `content_hash`, which will miss
  across a heterogeneous fleet and store the same protocol twice.
- A conformance bundle that publishes a `content_hash` for a third-party runtime
  to validate against. `docs/CONFORMANCE.md` does not currently state that the
  hash is platform-scoped, so a conforming runtime on another architecture would
  report a mismatch and be right to.
- Any reasoning of the form "same hash ⇒ same protocol, different hash ⇒ changed
  protocol". The first direction holds; the second does not.

## Options

1. **Quantize before hashing.** Round the coefficients to a fixed precision in the
   canonical serialization. Cheap, but it moves the boundary rather than removing
   it: rounding is discontinuous, so two architectures can still straddle a
   boundary. Measured on this corpus at 12 significant digits, a 64-ULP
   perturbation still moved 23 of 34 protocols. Not sufficient on its own.
2. **Hash the declared protocol, not the derived design.** Take the hash over the
   resolved-but-pre-filter-design IR: band edges, orders, declarations. Portable
   by construction, because nothing in it is computed in floating point. Loses the
   property that the hash covers the exact bytes a runtime will execute.
3. **Design the filters deterministically.** Replace the scipy call with an
   implementation specified to the bit, or ship the coefficients as decimal
   literals in the source. Largest change; gives both portability and exact
   coverage of the executed bytes.
4. **Say what is true.** Keep the hash as it is and document it as platform-scoped
   in `docs/CONFORMANCE.md` and in `compile_json`'s docstring. Costs nothing and
   removes the trap, but leaves cross-platform caches and conformance checks with
   no portable identity to use.

(2) and (4) together are probably the shape: a portable `protocol_hash` for
identity alongside the existing platform-scoped `content_hash` for integrity, with
both documented for what they are. **Whatever is chosen moves every published hash
once**, which is why it is not a patch on 0.25.0.

## Why no gate caught it

Nothing in the repository compiled the same protocol on two architectures and
compared. The dual-backend drift gate runs both engines on *one* machine. The
mobile workflow cross-compiles but does not compile protocols. `verify_fixtures`
checks golden vectors, which are generated and checked on the same runner.

The thing that found it was a test written for a different purpose — pinning the
corpus IR across a release — which was itself machine-bound and therefore failed
the moment it ran somewhere else. The useful generalisation: **a fixture of
full-precision floats is a fixture of the machine that wrote it.**

## See also

- `tests/test_ir_corpus_identity.py` — its module docstring records the two
  rejected designs and why the current one has no rounding boundaries.
- `CHANGELOG.md`, 0.25.0, **Fixed** — the test defect and the corrected claim.
- `docs/superpowers/records/2026-10-30-rec-23-followups/` — the 13 findings
  deferred out of REC-23.
