# Refrain

*An open description language for biosignal training paradigms.*

[![tests](https://github.com/refrain-lang/refrain/actions/workflows/test.yml/badge.svg)](https://github.com/refrain-lang/refrain/actions/workflows/test.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**Status:** v0.1.0 — reference implementation shipped end-to-end (parser, resolver, evaluator, embedding API). Pre-validation — see the positioning note below. See [CHANGELOG.md](CHANGELOG.md).

A Refrain file (`.refrain`) describes a complete biosignal training protocol — EEG neurofeedback, HRV coherence, GSR or temperature biofeedback — covering required hardware, signal-source binding, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, and practitioner-tunable controls, at a level of precision a runtime can execute directly and a peer reviewer can audit directly.

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

Biosignal training has a documented reproducibility problem driven by protocol heterogeneity across studies and proprietary closed-source software. In EEG neurofeedback the CRED-nf reporting checklist (Ros et al., *Brain*, 2020) describes in prose what a protocol must contain; HRV biofeedback has its own reporting norms (Lehrer & Gevirtz). Refrain makes either description executable: a single text file that's the protocol, the paper supplement, and the runnable artifact. CRED-nf is one supported reporting standard, not the reason Refrain exists.

See [`docs/CONCEPT.md`](docs/CONCEPT.md) for the full motivation and field context.

## Installation

Pin to a release tag, never to `main`:

```bash
# via git tag (works today)
pip install git+https://github.com/refrain-lang/refrain@v0.1.0

# via PyPI (when published)
pip install refrain==0.1.0
```

The `[eval]` extra adds `mne` and `pyxdf` for the evaluator's recording-source readers (FIF/EDF/XDF). The parser, resolver, and IR work without the extra:

```bash
pip install "refrain[eval]==0.1.0"
```

**`main` is the active development trunk.** It may contain unreleased changes, work-in-progress, or breaking changes for the next version. Production deployments should pin to a release tag.

## Documents

| File | Purpose |
|---|---|
| [`docs/CONCEPT.md`](docs/CONCEPT.md) | Problem, vision, history of the pattern in adjacent fields |
| [`docs/SPEC.md`](docs/SPEC.md) | Language reference |
| [`docs/TOUR.md`](docs/TOUR.md) | Tutorial-flavored walkthrough |
| [`docs/PRIMITIVES.md`](docs/PRIMITIVES.md) | Standard library reference |
| [`docs/EMBEDDING.md`](docs/EMBEDDING.md) | Integration guide for host applications |
| [`docs/RESEARCH-MODE.md`](docs/RESEARCH-MODE.md) | CRED-nf-grade allocation concealment: sham types, sealed allocation, threat model |
| [`docs/HOST-PLUGIN-BRIEF.md`](docs/HOST-PLUGIN-BRIEF.md) | Prompt template for briefing an AI session in a host repo |
| [`docs/DESIGN-NOTES.md`](docs/DESIGN-NOTES.md) | Implementation-side scratchpad and v0.1 spec proposals |
| [`examples/`](examples/) | Complete `.refrain` worked examples |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes |

## Status

Shipped on `main`:

- Parser, AST, unparser (round-trip-identity preserved)
- Resolver, type checker, IR, CRED-nf supplement export
- Composition: `extends`, `amend`, `remove`, `final`
- Streaming evaluator with 25 primitives — SMR / theta-beta, Othmer ILF, alpha-theta, coherence
- Multi-format input sources (FIF, EDF, XDF, synthetic)
- Embedding API (`Evaluator.live` / `start` / `step_chunk` / `set_control` / `stop`)
- Host-introspection tap API (`Evaluator.last_taps()`)
- CLI: `refrain check`, `refrain resolve`, `refrain run`
- 350+ tests passing on Python 3.10–3.13

Specified but not yet implemented:

- **Research mode** (CRED-nf-grade allocation concealment): chunk-transformer abstraction, three first-class sham types, sealed allocation via libsodium `crypto_box_seal`, `meta.sham_strategies` whitelist. Contract documented in [`docs/RESEARCH-MODE.md`](docs/RESEARCH-MODE.md) and [`SPEC §7.9`](docs/SPEC.md); reference implementation tracked for the next phase.

## Positioning and intended use

**Refrain is research and general-wellness software. It is not a medical device.** It has not been cleared by the FDA, CE-marked, or approved by any regulatory authority, and it makes no diagnostic or therapeutic claim. The Apache-2.0 license disclaims all warranties; use is at your own risk and subject to applicable law, institutional policy, and — for research use — IRB requirements.

The reference implementation has not been validated against any specific outcome. Validation is the responsibility of the host application and whoever runs the protocol. Refrain captures *what a protocol computes*; it does not assert that any protocol is appropriate for any given person or purpose.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and well-scoped feature requests are welcome.

## Security

See [SECURITY.md](SECURITY.md). Please do not file public issues for security concerns.

## License

Apache-2.0. See [LICENSE](LICENSE).
