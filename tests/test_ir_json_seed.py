from refrain.ir import IRControl, IRControlSeed, IRNumberLit
from refrain.ir_json import _emit_control, _EmitCtx
from refrain.types_ import Dimensions


def test_ircontrol_carries_an_optional_seed():
    seed = IRControlSeed(
        statistic="percentile",
        from_entity="derive/env",
        window_samples=15360,
        target_pct=IRNumberLit(value=70.0, dims=Dimensions(), unit=None),
    )
    ctrl = IRControl(
        name="thr_uv", canonical_name="control/thr_uv", type_kind="voltage",
        dims=Dimensions(), default=None, range_low=None, range_high=None,
        log_scale=False, label=None, live_tunable=True, tune_strategy=None,
        seed=seed,
    )
    assert ctrl.seed.window_samples == 15360
    assert ctrl.seed.from_entity == "derive/env"


def test_seed_defaults_to_none():
    ctrl = IRControl(
        name="reward_pct", canonical_name="control/reward_pct", type_kind="percent",
        dims=Dimensions(), default=None, range_low=None, range_high=None,
        log_scale=False, label=None, live_tunable=True, tune_strategy=None,
    )
    assert ctrl.seed is None


def _ctx():
    return _EmitCtx(sample_rate_hz=256.0, channel_names=("Cz",), controls={})


def test_emit_control_includes_seed_when_present():
    seed = IRControlSeed("percentile", "derive/env", 15360,
                         IRNumberLit(value=70.0, dims=Dimensions(), unit=None))
    ctrl = IRControl("thr_uv", "control/thr_uv", "voltage", Dimensions(),
                     None, None, None, False, None, True, None, seed=seed)
    out = _emit_control(ctrl, _ctx())
    assert out["seed"]["from"] == "derive/env"
    assert out["seed"]["window_samples"] == 15360


def test_emit_control_omits_seed_when_absent():
    ctrl = IRControl("reward_pct", "control/reward_pct", "percent", Dimensions(),
                     None, None, None, False, None, True, None)
    assert "seed" not in _emit_control(ctrl, _ctx())
