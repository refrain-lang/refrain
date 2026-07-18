from refrain.ir import IRControl, IRControlSeed, IRNumberLit
from refrain.ir_json import _emit_control, _EmitCtx
from refrain.types_ import Dimensions


def test_ircontrol_carries_an_optional_seed():
    seed = IRControlSeed(
        statistic="percentile",
        from_entity="derive/env",
        window_ms=60000.0,
        target_pct=IRNumberLit(value=70.0, dims=Dimensions(), unit=None),
    )
    ctrl = IRControl(
        name="thr_uv", canonical_name="control/thr_uv", type_kind="voltage",
        dims=Dimensions(), default=None, range_low=None, range_high=None,
        log_scale=False, label=None, live_tunable=True, tune_strategy=None,
        seed=seed,
    )
    assert ctrl.seed.window_ms == 60000.0
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
    seed = IRControlSeed("percentile", "derive/env", 60000.0,
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


from refrain.compile_json import compile_to_ir_json
from tests._seed_fixtures import SEEDING, NON_SEEDING  # verified fixtures (Task 4)


def test_seeding_protocol_emits_seed_and_v03():
    res = compile_to_ir_json(SEEDING)
    assert not res.errors, res.errors
    obj = res.ir_json
    assert obj["refrain_ir_version"] == "0.3"
    seed = obj["controls"]["thr_uv"]["seed"]
    assert seed["statistic"] == "percentile"
    assert seed["from"] == "derive/env"
    assert seed["window_samples"] == int(round(60 * 256))  # baked at 256 Hz
    assert seed["target_pct"]["node"] == "control_ref"
    assert seed["target_pct"]["target"] == "control/reward_pct"


def test_non_seeding_control_omits_seed_and_keeps_low_version():
    obj = compile_to_ir_json(NON_SEEDING).ir_json
    assert "seed" not in obj["controls"]["thr_uv"]
    assert obj["refrain_ir_version"] == "0.1"


def test_seed_window_rebakes_at_emit_rate():
    from tests._seed_fixtures import SEED_PROTO  # 2 s window

    for rate, expected in [(256.0, 512), (512.0, 1024), (1024.0, 2048)]:
        obj = compile_to_ir_json(SEED_PROTO, sample_rate_hz=rate).ir_json
        assert obj["controls"]["thr_uv"]["seed"]["window_samples"] == expected, rate
