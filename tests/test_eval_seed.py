import math
import numpy as np
from refrain.primitive_impls import PercentileImpl
from refrain.parser import parse
from refrain.resolver import resolve
from refrain.eval_ import Evaluator
from tests._seed_fixtures import SEED_PROTO


def test_ingest_appends_without_computing_and_skips_nonfinite():
    p = PercentileImpl(target_pct=70.0, window_ms=1000.0, sample_rate_hz=10.0)  # 10 samples
    p.ingest(np.array([1.0, 2.0, np.nan, 3.0, np.inf, 4.0]))
    st = p.export_state()
    assert st["n_eff"] == 4                    # nan/inf skipped, not counted
    assert st["value"] == np.percentile([1.0, 2.0, 3.0, 4.0], 70.0)


def _build(src=SEED_PROTO, *, bindings=None):
    ir = resolve(parse(src), bindings=bindings) if bindings else resolve(parse(src))
    return Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz",), backend="python")


def _run(ev, value, n_chunks, chunk=256):
    for _ in range(n_chunks):
        ev.step_chunk(np.full((chunk, 1), value, dtype=np.float64))


def test_seed_writes_control_at_run_edge_and_holds():
    ev = _build()
    ev.start(skip_warmup=False)
    # 3 s warmup at 256 Hz = 768 samples = 3 chunks (ingest); the 4th chunk is the
    # first `run` chunk, where the seed fires before any threshold steps.
    _run(ev, value=5.0, n_chunks=4)
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.status == "seeded"
    # The seed writes percentile(env); for a constant input env is constant, so
    # the written value equals env's last tap — montage arithmetic is irrelevant.
    assert abs(latch.value - ev.last_taps()["derive/env"]) < 1e-9
    assert ev._controls["control/thr_uv"] == latch.value


def test_seed_fires_exactly_once():
    ev = _build()
    ev.start(skip_warmup=False)
    _run(ev, value=5.0, n_chunks=4)  # fires on the 4th chunk (first run chunk)
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.fired is True
    seeded_value = latch.value
    # Step several more `run` chunks (staying inside the 5 s run phase — 4 more
    # chunks lands exactly at its end) with a DIFFERENT constant value; if the
    # latch re-fired it would seed to a new percentile derived from this value.
    _run(ev, value=9.0, n_chunks=4)
    assert latch.fired is True
    assert latch.value == seeded_value
    assert ev._controls["control/thr_uv"] == seeded_value


def test_skip_warmup_fails_closed():
    ev = _build()
    ev.start(skip_warmup=True)          # warmup skipped -> measurement never happens
    events = ev.step_chunk(np.full((256, 1), 5.0))
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.status == "insufficient_samples"
    assert ev._seed_failed_mute is True
    assert events == []                  # output suppressed for the session


def test_host_write_during_warmup_disarms_not_fails():
    ev = _build()
    ev.start(skip_warmup=False)
    ev.step_chunk(np.full((256, 1), 5.0))   # one warmup chunk
    ev.set_control("thr_uv", 1.5)            # clinician takes over
    _run(ev, value=5.0, n_chunks=4)          # cross into run
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.status == "disarmed_by_host"
    assert latch.fired is False
    assert ev._seed_failed_mute is False     # disarmed != failed -> runs normally


def test_nonfinite_samples_are_skipped_not_counted():
    ev = _build()
    ev.start(skip_warmup=False)
    good = np.full((256, 1), 5.0); good[:10] = np.nan   # NaNs must not poison/crash
    ev.step_chunk(good)
    _run(ev, value=5.0, n_chunks=4)
    assert ev._seed_latches["control/thr_uv"].status == "seeded"


def test_seed_report_shape():
    ev = _build()
    ev.start(skip_warmup=False)
    _run(ev, value=5.0, n_chunks=4)   # 3 warmup chunks + 1 run chunk -> fires
    r = ev.seed_report()["thr_uv"]
    assert r["status"] == "seeded"
    assert r["source"] == "derive/env"
    assert r["target_pct"] == 70.0
    assert r["window_s"] == 2.0          # 512 samples / 256 Hz
    assert r["n_samples"] >= 512          # 2 s window at 256 Hz
    assert r["at_time_s"] is not None


def test_seed_report_empty_for_non_seeding_protocol():
    from tests._seed_fixtures import NON_SEEDING   # verified fixture
    ev = _build(NON_SEEDING)
    ev.start(skip_warmup=True)
    assert ev.seed_report() == {}


EXPRPOS_SEED = '''protocol "exprpos_seed" {
  meta { version="1.0.0"; evidence="clinical"; description="seeded control in expr position" }
  requires { sample_rate=">= 256 Hz"; channels=["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from="raw"; pipeline=[ magnitude() ] }
  reward { continuous = sigmoid("env" / thr_uv, midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default=70; range=(50,90); live_tunable=true }
    thr_uv = voltage { default=9.9 uV; range=(0.5 uV,10 uV); live_tunable=true
      seed = percentile { from="env"; window=2 s; target_pct=reward_pct } }
  }
  session { phases=[ phase{name="warmup"; duration=3 s; output_muted=true}, phase{name="run"; mode=open} ] }
}'''


def test_expression_position_seeded_control_is_fresh_on_fire_chunk():
    import numpy as np
    from refrain.parser import parse
    from refrain.resolver import resolve
    from refrain.eval_ import Evaluator
    ir = resolve(parse(EXPRPOS_SEED))
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz",), record_streams=True, backend="python")
    ev.start(skip_warmup=False)
    fb = None
    for i in range(4):  # chunks 0-2 warmup, chunk 3 = first run chunk = fire
        ev.step_chunk(np.full((256, 1), 5.0, dtype=np.float64))
        fb = float(np.asarray(ev.last_streams()["output/fb"])[-1])
    # On the fire chunk the seed wrote thr_uv=5.0, so env/thr_uv=1.0 -> sigmoid=0.5,
    # NOT the stale-default 0.1847 (sigmoid(5/9.9)). This is the Rust-matching value.
    assert abs(fb - 0.5) < 1e-9, f"fire-chunk output must reflect the seeded control: {fb}"
