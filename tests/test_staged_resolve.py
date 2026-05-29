import pytest

from refrain.parser import parse
from refrain.resolver import resolve, ResolveError
from tests.conftest_staged import BASE   # shared fixtures, already exist & validated


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
