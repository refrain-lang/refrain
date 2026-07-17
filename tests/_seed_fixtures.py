# tests/_seed_fixtures.py — protocol fixtures, compile-verified against the
# real surface syntax. Do not hand-edit the syntax; if a change is needed,
# re-compile via refrain.compile_json.compile_to_ir_json and confirm no errors.

SEEDING = '''protocol "seed_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "seeding demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''

# Identical minus the seed line (control declaration survives; no seed emitted).
NON_SEEDING = SEEDING.replace(
    '\n      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }', '')

# Resolve-validation template. Substitute ONE seed line via `%` (NOT .format —
# the body is full of literal braces): BASE % {"seed": '<seed line or empty>'}.
BASE = '''protocol "seed_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "seeding demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      %(seed)s
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''

GOOD = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }'}

# Short-warmup runtime protocol (3 s warmup, 2 s window); thr_uv default 9.9 uV
# so a seed to ~5 visibly moves it. `magnitude()`-only derive -> constant env
# for a constant input (exact percentile parity).
SEED_PROTO = '''protocol "seed_run" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "runtime seed" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 9.9 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 2 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 3 s; output_muted = true },
    phase { name = "run";    duration = 5 s },
  ] }
}'''

# Mode-conditional for dead-seed elimination (adapted from tests/test_compile_json.py
# MODE_SRC). bindings={"threshold_style":"adaptive"} folds the absolute(thr_uv)
# branch out -> thr_uv unreferenced -> its seed must be dropped. "baseline" keeps it.
MODE_SRC = '''protocol "seed_mode" {
  meta { version = "1.0"; evidence = "clinical"; description = "x" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "env_t" {
    signal = "env"
    type = threshold_style == "baseline"
             ? absolute(value: thr_uv)
             : percentile(target_pct: reward_pct, window: 2 min)
  }
  reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous }
  controls {
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''
