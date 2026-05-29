"""Shared protocol-string fixtures for the staged-protocols test suite.

Single source of truth so the resolve / eval / parity test modules agree on the
exact DSL. Syntax mirrors examples/smr_cz.refrain (case-sensitive units Hz/uV/
ms/min/s; `input "<name>"`; derive `from` + `pipeline = [...]`; bandpass
`band: (lo, hi)`; `absolute(N uV)`; `percentile(target_pct:, window:)`).

- BASE: blockless single-band protocol with a `%s` slot for a `session {...}`
  block. Resolves today (no staged-protocol features required).
- HET: heterogeneous two-block protocol (beta_up / alpha_up). Requires the
  block / reward-bundle / phase-mode features (Tasks 1-4); until Task 4 it
  fails to parse on the `block` keyword.
- PCT_SRC: percentile-threshold staged protocol for the freeze test (Task 11).
"""

BASE = '''
protocol "p" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "e" {
    from = "raw"
    pipeline = [
      bandpass(band: (11 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 200 ms),
    ]
  }
  threshold "t" { signal = "e"; type = absolute(5 uV) }
  reward { continuous = sigmoid("e" / "t", midpoint: 1.0, steepness: 3) }
  output { audio = reward.continuous }
  %s
}
'''

HET = '''
protocol "p" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "device") }
  derive "be" {
    from = "raw"
    pipeline = [
      bandpass(band: (15 Hz, 18 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 200 ms),
    ]
  }
  derive "ae" {
    from = "raw"
    pipeline = [
      bandpass(band: (8 Hz, 12 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 200 ms),
    ]
  }
  threshold "bt" { signal = "be"; type = absolute(5 uV) }
  threshold "at" { signal = "ae"; type = absolute(5 uV) }
  reward "br" { continuous = sigmoid("be" / "bt", midpoint: 1.0, steepness: 3) }
  reward "ar" { continuous = sigmoid("ae" / "at", midpoint: 1.0, steepness: 3) }
  output { audio = reward.continuous }
  block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"] }
  block "alpha_up" { threshold = "at"; reward = "ar"; output = ["audio"] }
  session {
    phases = [
      phase { name = "warm"; duration = 1 s; output_muted = true },
      phase { name = "b1";   duration = 2 s; block = "beta_up";  mode = timed_with_floor },
      phase { name = "rest"; output_muted = true; mode = open },
      phase { name = "b2";   duration = 2 s; block = "alpha_up"; mode = timed_with_floor },
    ]
  }
}
'''

PCT_SRC = '''
protocol "p" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "e" {
    from = "raw"
    pipeline = [
      bandpass(band: (11 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 50 ms),
    ]
  }
  threshold "t" { signal = "e"; type = percentile(target_pct: 75, window: 2 s) }
  reward { continuous = sigmoid("e" / "t", midpoint: 1.0, steepness: 3) }
  output { audio = reward.continuous }
  session {
    phases = [
      phase { name = "warm"; duration = 1 s; output_muted = true },
      phase { name = "b1";   duration = 1 s; mode = timed },
      phase { name = "rest"; duration = 1 s; output_muted = true; mode = timed },
      phase { name = "b2";   duration = 1 s; mode = timed },
    ]
  }
}
'''
