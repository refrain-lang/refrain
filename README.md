# Refrain

*An open description language for clinical neurofeedback protocols.*

**Status:** v0.0 — language design and reference implementation in active development. Phase 0 empirical validation in progress.

A Refrain file (`.refrain`) describes a complete clinical neurofeedback protocol — required hardware, channel montage, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, and clinician-tunable controls — at a level of precision a runtime can execute directly and a peer reviewer can audit directly.

```refrain
protocol "hello_smr" {
  meta {
    version     = "0.1.0"
    evidence    = "demo"
    description = "Minimal single-band SMR uptraining"
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }

  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }

  derive "smr_envelope" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 250 ms),
    ]
  }

  threshold "smr_t" {
    signal = "smr_envelope"
    type   = percentile(target_pct: 70, window: 2 min)
  }

  reward {
    continuous = sigmoid("smr_envelope" / "smr_t",
                         midpoint: 1.0, steepness: 3)
  }

  output {
    audio_gain = reward.continuous
  }
}
```

## Why

Clinical neurofeedback has a documented reproducibility problem driven by protocol heterogeneity across studies and proprietary closed-source software. The CRED-nf reporting checklist (Ros et al., *Brain*, 2020) describes in prose what a NF protocol must contain. Refrain makes that description executable: a single text file that's the protocol, the paper supplement, and the runnable artifact.

See [`docs/CONCEPT.md`](docs/CONCEPT.md) for the full motivation and field context.

## Documents

| File | Purpose |
|---|---|
| [`docs/CONCEPT.md`](docs/CONCEPT.md) | Problem, vision, history of the pattern in adjacent fields |
| [`docs/SPEC.md`](docs/SPEC.md) | Language reference (v0.0r1 strawman) |
| [`docs/TOUR.md`](docs/TOUR.md) | Tutorial-flavored walkthrough |
| [`docs/PRIMITIVES.md`](docs/PRIMITIVES.md) | Standard library reference |
| [`examples/`](examples/) | Complete `.refrain` worked examples |

## Status of the implementation

Active. The reference parser, type system, IR, evaluator, primitive library, and runtime are being built in parallel with the language design. The runtime targets a minimum viable subset:

- **Phase 0 protocols:** SMR Cz (operant SMR/theta-beta), Bilateral A (beta-down at C3 + SMR-up at C4), Othmer ILF (T3-T4 bipolar, infra-low frequency).
- **Skipped initially:** `extends`/`amend`/`remove`/`final` (composition), `formula` form (cross-stream arithmetic), vector streams, custom primitives, source-space montage, CRED-nf export tool. All in the spec; not in v0.0 runtime.

## Status of validation

Pre-validation. The Phase 0 design is to express SMR Cz, Bilateral A, and Othmer ILF as `.refrain` files, run them on the Coherence Workstation recorder via a generic Refrain plugin against a Q21 amplifier, and validate phenomenology in the chair against existing clinical practice (Cygnet, BrainMaster). Validation outcomes feed back into language design before v0.1 is committed.

## License

Apache-2.0.
