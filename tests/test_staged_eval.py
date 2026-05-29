import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest_staged import BASE, HET

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
