# Changelog (refrain-core)

Changes to `refrain-core` — the Rust port of the Refrain evaluator, exposed to
Python over PyO3 and to mobile hosts (Swift/Kotlin) over uniffi. `refrain-core`
is versioned in lockstep with `refrain` (see the root `CHANGELOG.md` and
`tests/test_version_lockstep.py`); this file tracks the Rust-core-specific
detail behind each shared version number. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

## [0.22.0] — 2026-09-27

### Added
- **`advisor.rs`** — a Rust port of the Python autopilot advisor
  (`src/refrain/advisor.py`), mirrored function-for-function: same
  snake_case names, same order of operations, and the same message strings
  byte for byte. Verified against the Python reference over tracer output,
  scripted scenarios, and a whole staged session (`tests/advisor_parity.rs`).
- **`Evaluator` advice accessors**, exposed over both PyO3 and uniffi:
  `advice()`, `apply_advice(id, by)`, `dismiss_advice(id)`,
  `mark_equipment_change()`, `drain_advice_events()`, `autopilot_policy()`.
  Advice is deliberately not a tap — `tests/taps.rs` still asserts exact
  tap key-set equality — so these are new accessors, not additions to
  `last_taps()`.
- **`RefrainError::Advice { message }`** — a new variant on the uniffi-exposed
  `RefrainError` enum, returned when `apply_advice`/`dismiss_advice` fails
  (mirrors the Python evaluator raising `AdviceError`). **This is a uniffi
  enum change: mobile consumers (Swift/Kotlin) must regenerate their
  bindings** before picking up this release.
- **IR-JSON 0.4 deserialization.** The Rust IR loader accepts the new
  `autopilot` block, per-control `autopilot` policy, and `reward.check_names`
  fields; `SUPPORTED_IR_VERSIONS` gains `"0.4"`. A protocol that uses none of
  the new keys still deserializes to a byte-identical structure, and an older
  core built without this change refuses a `"0.4"` document at load rather
  than silently ignoring the fields it doesn't understand.
