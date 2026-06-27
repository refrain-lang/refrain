import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest_staged import BASE, HET, PCT_SRC

SR = 256

# All-`timed` 4-phase protocol (no blocks, no open phase) — exercises the cursor,
# mid-session mute, and state mapping without needing advance_phase (Task 9).
TIMED = BASE % '''session { phases = [
  phase { name = "warm"; duration = 1 s; output_muted = true },
  phase { name = "run1"; duration = 1 s },
  phase { name = "rest"; duration = 1 s; output_muted = true },
  phase { name = "run2"; duration = 1 s },
] }'''


def _live(src):
    ev = Evaluator.live(resolve(parse(src)), sample_rate_hz=SR,
                        channel_names=("Cz",), backend="python")
    ev.start()
    return ev


def _feed(ev, seconds, val=1.0):
    total = int(seconds * SR)
    pushed = 0
    while pushed < total:
        n = min(64, total - pushed)
        ev.step_chunk(np.full((n, 1), val, dtype=np.float64))
        pushed += n


def test_phases_run_in_order_to_stopped():
    ev = _live(TIMED)
    # Before any chunk, the snapshot is the initial phase.
    assert ev.current_phase()["name"] == "warm"
    assert ev.state == "warmup"
    # current_phase() is aligned with the chunk just processed (recorder seam
    # #2), so it reports the phase each fed chunk RAN UNDER. Feed the whole
    # 4 s session in 0.5 s steps and collect the phase observed after each.
    names = []
    for _ in range(8):                     # 8 * 0.5 s = 4 s = full session
        _feed(ev, 0.5)
        names.append(ev.current_phase()["name"])
        if ev.state == "stopped":
            break
    distinct = [n for i, n in enumerate(names) if i == 0 or n != names[i - 1]]
    assert distinct == ["warm", "run1", "rest", "run2"]   # phases in order
    assert ev.state == "stopped"


def test_current_phase_aligned_with_last_chunk_at_boundary():
    # Recorder seam #2: on the chunk that crosses warm -> run1, current_phase()
    # reflects the phase the chunk JUST processed ran under (warm), even though
    # the cursor has advanced (state == "run"). This keeps current_phase()
    # coherent with last_taps for a host polling both after step_chunk.
    ev = _live(TIMED)
    n = int(1.0 * SR)                      # warm = 256 samples
    pushed = 0
    while pushed < n:
        c = min(64, n - pushed)
        ev.step_chunk(np.full((c, 1), 1.0, dtype=np.float64))
        pushed += c
    assert ev.state == "run"               # cursor advanced past warm
    assert ev.current_phase()["name"] == "warm"   # last chunk still ran under warm


def test_state_never_returns_to_warmup():
    ev = _live(TIMED)
    seen = []
    for _ in range(int(5 * SR / 64) + 4):
        ev.step_chunk(np.full((64, 1), 1.0))
        seen.append(ev.state)
        if ev.state == "stopped":
            break
    first_run = seen.index("run")
    assert "warmup" not in seen[first_run:]   # never re-enters warmup


def test_midsession_rest_mutes_output():
    ev = _live(TIMED)
    _feed(ev, 1.0)                        # warm (muted)
    _feed(ev, 0.5)                        # processing run1
    assert ev.current_phase()["name"] == "run1"
    assert ev.last_taps()["output/audio"] > 0.0     # run1 emits (not muted)
    _feed(ev, 0.5)                        # finish run1; cursor -> rest
    _feed(ev, 0.5)                        # processing the muted mid-session rest
    assert ev.current_phase()["name"] == "rest"
    assert ev.last_taps()["output/audio"] == 0.0    # mid-session rest mutes


# -- Task 9: host transport methods + phase tap -----------------------------
def test_open_phase_needs_advance():
    ev = _live(HET)
    _feed(ev, 1.0)                 # warm -> b1
    _feed(ev, 2.0)                 # b1 auto-advances (timed_with_floor) -> rest (open)
    _feed(ev, 3.0)                 # open never auto-advances no matter how long
    assert ev.current_phase()["name"] == "rest"
    assert ev.advance_phase() is True
    _feed(ev, 0.1)                 # process a b2 chunk so the snapshot reflects b2
    assert ev.current_phase()["name"] == "b2"


def test_timed_with_floor_hold_extends_then_releases():
    ev = _live(HET)
    _feed(ev, 1.0)                 # warm -> b1
    assert ev.hold() is True       # extend b1 past its 2 s floor
    _feed(ev, 5.0)                 # would normally have auto-advanced at 2 s
    assert ev.current_phase()["name"] == "b1"   # still in b1 (held)
    assert ev.hold(False) is True  # release: re-arm auto-advance
    _feed(ev, 0.1)                 # already past floor -> advances on next chunk
    assert ev.current_phase()["name"] in ("rest", "b1")  # advanced to rest (open)
    # robust: drive to rest deterministically
    if ev.current_phase()["name"] != "rest":
        _feed(ev, 0.1)
    assert ev.current_phase()["name"] == "rest"


def test_clock_freeze_pauses_then_resumes():
    ev = _live(HET)
    _feed(ev, 1.0)                 # warm -> b1
    _feed(ev, 1.0)                 # 1 s into b1 (of 2 s)
    ev.set_clock_frozen(True)
    _feed(ev, 5.0)                 # frozen: must NOT advance
    assert ev.current_phase()["name"] == "b1"
    assert ev.advance_phase() is True   # Next works while frozen
    ev.set_clock_frozen(False)
    _feed(ev, 0.1)
    assert ev.current_phase()["name"] == "rest"


def test_hold_noop_on_timed_phase():
    # plain `timed` phase: hold() is a no-op returning False (firm clock)
    ev = _live(TIMED)              # TIMED is defined at top of this test module
    _feed(ev, 1.0)                # warm -> run1 (mode "timed")
    _feed(ev, 0.1)                # processing run1
    assert ev.hold() is False     # firm; not held


def test_advance_past_last_is_noop():
    ev = _live(HET)
    _feed(ev, 1.0)                # warm -> b1
    ev.advance_phase()            # b1 -> rest
    ev.advance_phase()            # rest -> b2
    ev.advance_phase()            # b2 -> stopped
    assert ev.state == "stopped"
    assert ev.advance_phase() is False


def test_phase_index_tap_present():
    ev = _live(HET)
    _feed(ev, 0.5)                # in warm (index 0)
    taps = ev.last_taps()
    assert taps["phase/index"] == 0.0
    assert taps["phase/output_muted"] == 1.0   # warm is muted


# -- Task 10: active-block masking + reward-bundle selection (R4) -----------
# HET uses a `device` (hardware) reference, so a fed step transient produces a
# real, non-zero envelope. HET_DIFF gives the alpha block a huge threshold so
# the beta and alpha bundles drive observably different audio.
HET_DIFF = HET.replace('threshold "at" { signal = "ae"; type = absolute(5 uV) }',
                       'threshold "at" { signal = "ae"; type = absolute(500 uV) }')

# Two output channels; beta_up's output set lists only "audio", so "extra" must
# be muted while the beta block is active (exercises the output-mask branch).
HET_2CH = HET_DIFF.replace(
    'output { audio = reward.continuous }',
    'output {\n    audio = reward.continuous\n    extra = reward.continuous\n  }',
)


def test_active_block_selects_reward_bundle():
    # audio = reward.continuous. During b1 it must track the beta bundle; during
    # b2 the alpha bundle. With at=500 uV the alpha sigmoid sits near 0, so the
    # two blocks produce clearly different audio.
    ev = _live(HET_DIFF)
    _feed(ev, 1.0)                 # warm -> b1
    _feed(ev, 0.5)                 # processing b1 (beta bundle active)
    assert ev.current_phase()["name"] == "b1"
    beta_audio = ev.last_taps()["output/audio"]
    ev.advance_phase()             # b1 -> rest
    ev.advance_phase()             # rest -> b2
    _feed(ev, 0.5)                 # processing b2 (alpha bundle active)
    assert ev.current_phase()["name"] == "b2"
    alpha_audio = ev.last_taps()["output/audio"]
    assert beta_audio != alpha_audio          # different bundle drives audio
    assert beta_audio > alpha_audio           # alpha threshold huge -> ~0


def test_nonactive_output_channel_is_muted():
    # beta_up's output set is ["audio"]; "extra" is not a member, so during b1
    # the active "audio" channel emits while "extra" is forced silent.
    ev = _live(HET_2CH)
    _feed(ev, 1.0)                 # warm -> b1
    _feed(ev, 0.5)                 # processing b1
    assert ev.current_phase()["name"] == "b1"
    assert ev.last_taps()["output/audio"] > 0.0     # active channel emits
    assert ev.last_taps()["output/extra"] == 0.0    # non-member channel muted


def test_derives_stay_warm_regardless_of_active_block():
    ev = _live(HET)
    _feed(ev, 1.0, val=1.0)        # warm
    _feed(ev, 0.1, val=0.5)        # step transient into b1 (beta active);
    #                                the alpha derive must still run
    assert ev.last_taps()["derive/ae"] != 0.0   # alpha envelope computed though beta block active
    assert ev.last_taps()["derive/be"] != 0.0


def test_setcontrol_on_inactive_block_threshold_during_warmup():
    # control-backed absolute() threshold for the alpha block, seeded during
    # warmup while the alpha block is NOT active. The update must take effect.
    src = '''
    protocol "p" {
      requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
      controls { at_uv = voltage { default = 5 uV; live_tunable = true } }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "be" { from = "raw"
        pipeline = [ bandpass(band: (15 Hz, 18 Hz), order: 4), hilbert(),
                     magnitude(), smooth(tau: 200 ms) ] }
      derive "ae" { from = "raw"
        pipeline = [ bandpass(band: (8 Hz, 12 Hz), order: 4), hilbert(),
                     magnitude(), smooth(tau: 200 ms) ] }
      threshold "bt" { signal = "be"; type = absolute(5 uV) }
      threshold "at" { signal = "ae"; type = absolute(at_uv) }
      reward "br" { continuous = sigmoid("be" / "bt", midpoint: 1.0, steepness: 3) }
      reward "ar" { continuous = sigmoid("ae" / "at", midpoint: 1.0, steepness: 3) }
      output { audio = reward.continuous }
      block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"] }
      block "alpha_up" { threshold = "at"; reward = "ar"; output = ["audio"] }
      session { phases = [
        phase { name="warm"; duration=1 s; output_muted=true },
        phase { name="b1";   duration=1 s; block="beta_up";  mode=timed },
        phase { name="b2";   duration=1 s; block="alpha_up"; mode=timed },
      ] }
    }
    '''
    new_at = 42.0
    ev = _live(src)
    _feed(ev, 0.5)                  # mid-warmup; alpha block inactive
    ev.set_control("at_uv", new_at)  # seed inactive block's threshold-control
    _feed(ev, 0.5)
    assert ev.last_taps()["threshold/at"] == new_at  # took effect though b2 inactive


# PCT_SRC tracks a percentile threshold over the derived envelope `e`, which
# is built from `referential(active:"Cz", reference:"linked_ears")`. With a
# single-channel source the linked-ears fallback is common-average over the
# one channel, so `Cz - mean(Cz) == 0` and the envelope is identically zero
# regardless of input — the percentile window would only ever see zeros.
# Supplying ear channels (A1/A2 held at 0) makes the montage pass Cz through,
# so an in-band sine produces a real, non-zero envelope to populate the window.
_PCT_CHANS = ("Cz", "A1", "A2")


def _live_pct():
    ev = Evaluator.live(resolve(parse(PCT_SRC)), sample_rate_hz=SR,
                        channel_names=_PCT_CHANS, backend="python")
    ev.start()
    return ev


def _feed_pct(ev, seconds, amp, freq=13.0):
    """Feed an in-band (13 Hz, inside the 11-15 Hz bandpass) sine on Cz with
    the ear channels at 0, so the referential montage yields a real signal."""
    total = int(seconds * SR)
    pushed = 0
    while pushed < total:
        n = min(64, total - pushed)
        idx = np.arange(pushed, pushed + n)
        cz = amp * np.sin(2 * np.pi * freq * idx / SR)
        block = np.zeros((n, len(_PCT_CHANS)), dtype=np.float64)
        block[:, 0] = cz
        ev.step_chunk(block)
        pushed += n


def test_percentile_window_freezes_during_midsession_rest():
    # PCT_SRC: warm(1s,muted) / b1(1s) / rest(1s,muted) / b2(1s), all timed,
    # threshold "t" = percentile(target_pct: 75, window: 2 s). Feed a large
    # spike ONLY during the muted rest; the frozen window must not ingest it,
    # so the threshold value is unchanged across the rest.
    ev = _live_pct()
    _feed_pct(ev, 1.0, amp=1.0)       # warm (populates window)
    _feed_pct(ev, 1.0, amp=1.0)       # b1 (ingests)
    t_before = ev.last_taps()["threshold/t"]
    _feed_pct(ev, 1.0, amp=50.0)      # rest (muted, index 2) — huge artifact, frozen
    assert ev.current_phase()["name"] == "rest"
    t_after = ev.last_taps()["threshold/t"]
    # window did NOT move during the rest
    assert np.isclose(t_after, t_before, rtol=0.0, atol=1e-9)


def test_percentile_window_ingests_during_active_phase():
    ev = _live_pct()
    _feed_pct(ev, 1.0, amp=1.0)       # warm
    t0 = ev.last_taps()["threshold/t"]
    _feed_pct(ev, 1.0, amp=50.0)      # b1 (NOT muted) — spike SHOULD move the window
    assert ev.current_phase()["name"] == "b1"
    assert ev.last_taps()["threshold/t"] != t0
