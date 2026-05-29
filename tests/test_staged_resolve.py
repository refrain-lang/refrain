import pytest

from refrain.parser import parse
from refrain.resolver import ResolveError, resolve
from tests.conftest_staged import BASE, HET  # shared fixtures, already exist & validated


def _resolve(src: str):
    return resolve(parse(src))


def test_phase_mode_and_block_default_and_explicit():
    ir = _resolve(BASE % '''
      session { phases = [
        phase { name = "warm"; duration = 1 s; output_muted = true },
        phase { name = "go";   duration = 2 s; mode = timed_with_floor },
      ] }
    ''')
    phases = ir.session.phases
    assert phases[0].mode == "timed"            # default
    assert phases[0].block is None
    assert phases[1].mode == "timed_with_floor"


def test_open_phase_may_omit_duration():
    ir = _resolve(BASE % '''
      session { phases = [
        phase { name = "warm"; duration = 1 s; output_muted = true },
        phase { name = "rest"; output_muted = true; mode = open },
      ] }
    ''')
    rest = ir.session.phases[1]
    assert rest.mode == "open"
    assert rest.duration_ms == 0.0


def test_invalid_mode_raises():
    with pytest.raises(ResolveError):
        _resolve(BASE % 'session { phases = [ phase { name="x"; duration=1 s; mode = bogus } ] }')


def test_non_open_phase_missing_duration_raises():
    with pytest.raises(ResolveError):
        _resolve(BASE % 'session { phases = [ phase { name="x"; mode = timed } ] }')


def test_block_must_be_string():
    with pytest.raises(ResolveError):
        _resolve(BASE % 'session { phases = [ phase { name="x"; duration=1 s; block = 3 } ] }')


def test_phase_block_string_stored():
    # BASE has no block declarations, but Task 1 does not validate block existence;
    # the string should be stored as-is on the IRPhase.
    ir = _resolve(BASE % '''
      session { phases = [
        phase { name = "warm"; duration = 1 s; output_muted = true },
        phase { name = "go";   duration = 2 s; block = "beta_up" },
      ] }
    ''')
    assert ir.session.phases[1].block == "beta_up"


def test_phase_mode_string_literal():
    # mode = "timed" as a StringLit (quoted) should resolve to mode == "timed".
    ir = _resolve(BASE % '''
      session { phases = [
        phase { name = "warm"; duration = 1 s; mode = "timed" },
      ] }
    ''')
    assert ir.session.phases[0].mode == "timed"


def test_protocol_has_blocks_and_reward_bundles_maps():
    ir = _resolve(
        BASE % 'session { phases = [ phase { name="w"; duration=1 s; output_muted=true } ] }'
    )
    assert ir.blocks == {}               # no blocks declared
    assert ir.reward_bundles == {}       # no named bundles declared


def test_reward_bundle_disambiguated_from_component():
    ir = _resolve('''
    protocol "p" {
      requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "e" {
        from = "raw"
        pipeline = [
          bandpass(band: (11 Hz, 15 Hz), order: 4),
          hilbert(), magnitude(), smooth(tau: 200 ms),
        ]
      }
      threshold "t" { signal = "e"; type = absolute(5 uV) }
      reward "beta_reward" { continuous = sigmoid("e" / "t", midpoint: 1.0, steepness: 3) }
      output { audio = reward.continuous }
      session { phases = [ phase { name="w"; duration=1 s; output_muted=true } ] }
    }
    ''')
    assert "beta_reward" in ir.reward_bundles
    assert ir.reward_bundles["beta_reward"].continuous is not None
    assert ir.reward.components == ()   # existing weighted-component path untouched
    # The default top-level reward stays empty (no "first bundle" synthesis);
    # `reward.continuous` in output resolves via the bundle for its type, and the
    # active bundle is bound per-block at eval time.
    assert ir.reward.continuous is None
    assert ir.reward.event is None


def test_blocks_resolved():
    ir = _resolve(HET)
    assert set(ir.blocks) == {"beta_up", "alpha_up"}
    assert ir.blocks["beta_up"].reward == "br"
    assert ir.blocks["beta_up"].thresholds == ("bt",)
    assert ir.blocks["beta_up"].outputs == ("audio",)


def test_nonmuted_phase_without_block_errors():
    src = HET.replace(
        'phase { name = "b1";   duration = 2 s; block = "beta_up";  mode = timed_with_floor },',
        'phase { name = "b1";   duration = 2 s; mode = timed_with_floor },',
    )
    with pytest.raises(ResolveError, match="non-muted phase"):
        _resolve(src)


def test_block_unknown_reward_errors():
    src = HET.replace('reward = "br"', 'reward = "nope"', 1)
    with pytest.raises(ResolveError, match="unknown reward bundle"):
        _resolve(src)


def test_block_unknown_threshold_errors():
    src = HET.replace('threshold = "bt"', 'threshold = "nope"', 1)
    with pytest.raises(ResolveError, match="unknown threshold"):
        _resolve(src)


def test_block_unknown_output_errors():
    src = HET.replace('output = ["audio"]', 'output = ["nope"]', 1)
    with pytest.raises(ResolveError, match="unknown output channel"):
        _resolve(src)


def test_block_unknown_inhibit_errors():
    src = HET.replace(
        'block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"] }',
        'block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"]; '
        'inhibit = ["nope"] }',
        1,
    )
    with pytest.raises(ResolveError, match="unknown inhibit"):
        _resolve(src)


def test_phase_unknown_block_errors():
    src = HET.replace(
        'block = "beta_up";  mode = timed_with_floor',
        'block = "ghost";  mode = timed_with_floor',
        1,
    )
    with pytest.raises(ResolveError, match="unknown block"):
        _resolve(src)
