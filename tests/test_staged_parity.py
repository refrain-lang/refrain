"""Python<->Rust parity for staged protocols (R1-R4 + R6).

Drives the SAME staged-session scripts through both evaluator backends and
asserts identical event streams, phase introspection, and taps. This is the
backend-equivalence gate the spec's acceptance criteria require — it only runs
when the compiled `refrain_core` wheel is importable.
"""

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest_staged import HET, PCT_SRC

pytest.importorskip("refrain_core")

SR = 256

# HET (device reference) gives a real, non-zero single-channel signal; the
# alpha block's threshold is bumped so the two bundles drive different audio.
HET_DIFF = HET.replace('threshold "at" { signal = "ae"; type = absolute(5 uV) }',
                       'threshold "at" { signal = "ae"; type = absolute(500 uV) }')

# PCT_SRC needs A1/A2 (held at 0) so the linked-ears montage passes Cz through
# and the percentile window sees a real signal (see test_staged_eval.py).
_PCT_CHANS = ("Cz", "A1", "A2")


def _backends(src, channels):
    for backend in ("python", "rust"):
        ev = Evaluator.live(resolve(parse(src)), sample_rate_hz=SR,
                            channel_names=channels, backend=backend)
        ev.start()
        yield ev


def _phase(ev):
    cp = ev.current_phase()
    # Normalize to a comparable tuple (index/name/mode/block/output_muted/held/
    # clock_frozen + rounded remaining).
    rem = cp["remaining_s"]
    return (
        cp["index"], cp["name"], cp["mode"], cp["block"],
        bool(cp["output_muted"]), bool(cp["clock_frozen"]), bool(cp["held"]),
        None if rem is None else round(float(rem), 9),
    )


def _taps(ev):
    out = {}
    for k, v in ev.last_taps().items():
        out[k] = bool(v) if isinstance(v, bool) else round(float(v), 9)
    return out


def _events(evs):
    return [
        (round(e.timestamp_s, 6), e.channel, e.kind,
         None if e.value is None else round(e.value, 9))
        for e in evs
    ]


def _run(src, channels, script, signal):
    """Run `script` (a list of ops) on both backends; return per-backend
    (events, phase_trace, taps_trace). `signal(n, pushed)` yields a chunk."""
    results = {}
    for ev in _backends(src, channels):
        events, phases, taps = [], [], []
        for op in script:
            if op[0] == "feed":
                total = int(op[1] * SR)
                pushed = 0
                while pushed < total:
                    n = min(64, total - pushed)
                    events += ev.step_chunk(signal(n, pushed))
                    pushed += n
                phases.append(_phase(ev))
                taps.append(_taps(ev))
            elif op[0] == "advance":
                ev.advance_phase()
            elif op[0] == "hold":
                ev.hold(op[1])
            elif op[0] == "freeze":
                ev.set_clock_frozen(op[1])
        results[id(ev)] = (_events(events), phases, taps)
    py, rust = list(results.values())
    return py, rust


def _assert_parity(py, rust):
    pe, pp, pt = py
    re, rp, rt = rust
    assert pe == re, "event streams differ"
    assert pp == rp, "phase introspection differs"
    assert len(pt) == len(rt)
    for a, b in zip(pt, rt):
        assert set(a) == set(b), f"tap key-set differs: {set(a) ^ set(b)}"
        for k in a:
            assert a[k] == b[k], f"tap {k!r} differs: {a[k]} vs {b[k]}"


def _const_cz(n, _pushed):
    return np.full((n, 1), 1.0, dtype=np.float64)


def _sine_3ch(n, pushed, amp=1.0, freq=13.0):
    idx = np.arange(pushed, pushed + n)
    block = np.zeros((n, len(_PCT_CHANS)), dtype=np.float64)
    block[:, 0] = amp * np.sin(2 * np.pi * freq * idx / SR)
    return block


def test_parity_full_staged_session():
    script = [("feed", 1.0), ("feed", 2.0), ("advance",), ("feed", 2.0)]
    _assert_parity(*_run(HET_DIFF, ("Cz",), script, _const_cz))


def test_parity_clock_freeze_and_hold():
    script = [
        ("feed", 1.0), ("feed", 1.0), ("freeze", True), ("feed", 3.0),
        ("freeze", False), ("hold", True), ("feed", 3.0), ("hold", False),
        ("feed", 1.0),
    ]
    _assert_parity(*_run(HET_DIFF, ("Cz",), script, _const_cz))


def test_parity_percentile_freeze():
    # Big spike during the muted rest; both backends must freeze the window.
    def sig(n, pushed):
        # rest is phase 2 (samples 512..768); spike there.
        amp = 50.0 if 512 <= pushed < 768 else 1.0
        return _sine_3ch(n, pushed, amp=amp)

    script = [("feed", 1.0), ("feed", 1.0), ("feed", 1.0), ("feed", 1.0)]
    _assert_parity(*_run(PCT_SRC, _PCT_CHANS, script, sig))
