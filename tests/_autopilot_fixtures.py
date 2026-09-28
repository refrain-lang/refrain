# tests/_autopilot_fixtures.py — autopilot protocol fixtures.
# AP's base (all placeholders empty) is compile-verified against the live
# compiler (IR v0.1 in both threshold_style bindings). Substitute with `%`
# (NOT .format — the body is full of literal braces). Use `ap(...)`.

AP = '''protocol "ap_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "autopilot demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "t_env" { from = "raw"; pipeline = [ magnitude() ] }
  derive "a_env" { from = "raw"; pipeline = [ magnitude() ] }
  derive "ratio" { formula = "t_env" / "a_env" }
  threshold "t_t" {
    signal = "t_env"
    type = threshold_style == "baseline"
             ? absolute(value: t_uv)
             : percentile(target_pct: t_pct, window: 2 min)
    live_tunable = true
  }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
    threshold = percentile(target_pct: %(emg_thr)s, window: 2 min)
    action    = mute(release: 200 ms)
  }
  reward {
    event = dwell(
      condition: all_of([
        above("t_env", "t_t")%(theta_as)s,
        above("ratio", %(xover_thr)s)%(xover_as)s,
      ]),
      duration: 1000 ms
    )
  }
  output { audio_chime = reward.event }
  controls {
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
    t_pct  = percent { default = 15; range = (15, 70); live_tunable = true; %(t_pct_ap)s }
    t_uv   = voltage { default = 8.0 uV; range = (2.0 uV, 30.0 uV); live_tunable = true; %(t_uv_ap)s }
    xover  = number  { default = 0.60; range = (0.5, 1.0); live_tunable = true; label = "Crossover target"; %(xover_ap)s }
    emg_pct = percent { default = 95; range = (50, 99); live_tunable = true; %(emg_ap)s }
    %(extra_controls)s
  }
  %(autopilot)s
  session { phases = [
    phase { name = "settle"; duration = 10 s;  output_muted = true },
    phase { name = "train1"; duration = 300 s },
    phase { name = "rest";   duration = 10 s;  output_muted = true },
    phase { name = "train2"; duration = 300 s },
  ] }
}'''

AUTOPILOT_BLOCK = '''autopilot {
    evidence         = "exploratory"
    citation         = "Test policy"
    rationale        = "Test rationale"
    reward_target    = (10%, 35%)
    phases           = ["train1", "train2"]
    watch            = 20 s
    between_moves    = 30 s
    equipment_settle = 5 s
    tighten_first    = ["crossover", "theta"]
    emg = guard { max = 15%; say = "Muscle artifact." }
  }'''

XOVER_AP = ('autopilot = fixed_step { fixes = "crossover"; step = 0.05; higher_is = "harder"; '
            'limits = (0.5, 1.0); apply = "auto"; round_to = 0.01 }')
T_PCT_AP = ('autopilot = fixed_step { fixes = "theta"; step = 5; higher_is = "harder"; '
            'limits = (15, 40); apply = "suggest"; only_when = threshold_style == "adaptive" }')
T_UV_AP = ('autopilot = proportional_step { fixes = "theta"; step = 10%; higher_is = "harder"; '
           'apply = "suggest"; round_to = 0.1 uV; only_when = threshold_style == "baseline" }')

DEFAULTS = {
    "theta_as": ' as "theta"',
    "xover_as": ' as "crossover"',
    "xover_thr": "xover",
    "emg_thr": "emg_pct",
    "t_pct_ap": T_PCT_AP,
    "t_uv_ap": T_UV_AP,
    "xover_ap": XOVER_AP,
    "emg_ap": "",
    "extra_controls": "",
    "autopilot": AUTOPILOT_BLOCK,
}

# Placeholders that yield today's (pre-autopilot) protocol: no names, no policy.
PLAIN = {k: "" for k in DEFAULTS} | {"xover_thr": "xover", "emg_thr": "emg_pct"}


def ap(**overrides: str) -> str:
    """The full-policy protocol with the named placeholders replaced."""
    return AP % (DEFAULTS | overrides)


def plain(**overrides: str) -> str:
    """The pre-autopilot protocol (no names, no policies) with overrides."""
    return AP % (PLAIN | overrides)
