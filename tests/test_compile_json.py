import json

import refrain
from refrain.compile_json import compile_to_ir_json

SMOKE_SRC = '''protocol "svc_smoke" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "compile-service smoke protocol"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  output {
    audio_gain = 0
  }
}'''


def test_happy_path_emits_ir_and_meta():
    result = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert result.errors == []
    assert result.ir_json is not None
    assert result.ir_json["name"] == "svc_smoke"
    assert result.meta["refrain_version"] == refrain.__version__
    assert result.meta["ir_version"] == "0.1"
    assert result.meta["sample_rate_hz"] == 256.0
    assert result.meta["content_hash"].startswith("sha256:")


def test_content_hash_is_deterministic_and_rate_sensitive():
    h1 = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0).meta["content_hash"]
    h1b = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0).meta["content_hash"]
    h2 = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=512.0).meta["content_hash"]
    assert h1 == h1b
    assert h1 != h2


BAD_RESOLVE_SRC = '''protocol "svc_bad" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "bad"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  derive "filtered" {
    from = "raw"
    pipeline = [bandpass(center: nonexistent, bandwidth: ratio(0.5))]
  }
  output {
    audio_gain = 0
  }
}'''

BAD_PARSE_SRC = 'protocol "oops" {'


def test_resolve_error_returns_located_diagnostic():
    result = compile_to_ir_json(BAD_RESOLVE_SRC, sample_rate_hz=256.0)
    assert result.ir_json is None
    assert result.meta["content_hash"] is None
    assert result.meta["ir_version"] is None
    assert len(result.errors) == 1
    diag = result.errors[0]
    assert diag.stage == "resolve"
    assert diag.severity == "error"
    assert diag.line is not None and diag.col is not None


def test_parse_error_returns_diagnostic_without_location():
    result = compile_to_ir_json(BAD_PARSE_SRC)
    assert result.ir_json is None
    assert len(result.errors) == 1
    assert result.errors[0].stage == "parse"
    assert result.errors[0].message  # non-empty (Lark's message)
