from refrain.parser import parse
from refrain.resolver import resolve
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
