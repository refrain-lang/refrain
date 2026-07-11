import hashlib
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


def test_bundled_schema_loads_for_both_versions():
    from refrain.compile_json import _load_schema

    assert _load_schema("0.1")["$id"].endswith("ir-json-v0.1.schema.json")
    assert _load_schema("0.2")["$id"].endswith("ir-json-v0.2.schema.json")


def test_valid_compile_has_no_schema_error():
    result = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert result.schema_error is None


def test_validate_flags_nonconformant_ir():
    from refrain.compile_json import _validate

    # An object that claims v0.1 but is missing every required field.
    err = _validate({"refrain_ir_version": "0.1"})
    assert err is not None
    assert "0.1" in err


def test_validate_unknown_version_is_reported():
    from refrain.compile_json import _validate

    err = _validate({"refrain_ir_version": "9.9"})
    assert err is not None


def test_ir_json_text_is_canonical_and_matches_content_hash():
    result = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    # ir_json_text is the exact canonical serialization content_hash covers.
    assert result.ir_json_text == json.dumps(result.ir_json, indent=2)
    # The integrity invariant: sha256(canonical bytes) == content_hash.
    digest = hashlib.sha256(result.ir_json_text.encode("utf-8")).hexdigest()
    assert result.meta["content_hash"] == f"sha256:{digest}"


def test_ir_json_text_is_none_on_error():
    result = compile_to_ir_json(BAD_PARSE_SRC)
    assert result.ir_json_text is None


PARENT_SRC = '''protocol "smr_base" {
  meta {
    version  = "1.0.0"
    evidence = "demo"
    description = "base"
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

CHILD_SRC = '''protocol "smr_child" extends "smr_base" {
  meta {
    description = "child override"
  }
}'''


def test_extends_request_supplied_parent_merges():
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0, parents={"smr_base": PARENT_SRC})
    assert r.errors == []
    assert r.unresolved_parents == []
    assert r.ir_json is not None
    assert r.ir_json["name"] == "smr_child"      # child's identity
    assert r.ir_json["channels"] == ["Cz"]        # parent's requires merged in
    assert r.meta["extends"] == "smr_base"


def test_missing_parent_reported_as_unresolved():
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0)  # no parents supplied
    assert r.ir_json is None
    assert r.errors == []
    assert r.unresolved_parents == ["smr_base"]
    assert r.meta["extends"] == "smr_base"


def test_standalone_protocol_has_null_extends():
    r = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert r.meta["extends"] is None
    assert r.unresolved_parents == []


def test_composition_error_is_compose_diagnostic():
    bad_child = '''protocol "bad" extends "smr_base" {
  amend input "ghost" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
}'''
    r = compile_to_ir_json(bad_child, sample_rate_hz=256.0, parents={"smr_base": PARENT_SRC})
    assert r.ir_json is None
    assert r.unresolved_parents == []
    assert len(r.errors) == 1
    assert r.errors[0].stage == "compose"


def test_malformed_supplied_parent_is_compose_diagnostic():
    r = compile_to_ir_json(
        CHILD_SRC, sample_rate_hz=256.0, parents={"smr_base": 'protocol "smr_base" {'}
    )
    assert r.ir_json is None
    assert len(r.errors) == 1
    assert r.errors[0].stage == "compose"


def test_extends_filesystem_library_parent(tmp_path):
    (tmp_path / "smr_base.refrain").write_text(PARENT_SRC)
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0, library_dirs=[str(tmp_path)])
    assert r.ir_json is not None
    assert r.ir_json["name"] == "smr_child"
    assert r.ir_json["channels"] == ["Cz"]


# ---------------------------------------------------------------------------
# Resolve-time bindings (mode variants) — passthrough + meta echo
# ---------------------------------------------------------------------------

MODE_SRC = '''protocol "styled" {
  meta { version = "1.0"; evidence = "clinical"; description = "x" }
  requires {
    sample_rate = ">= 250 Hz"
    channels    = ["Cz"]
  }
  controls {
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
    reward_pct = percent { default = 70; range = (50, 90) }
    thr_uv = voltage { default = 2.0 uV; range = (0.5 uV, 10 uV) }
  }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "env_t" {
    signal = "env"
    type = threshold_style == "baseline"
             ? absolute(value: thr_uv)
             : percentile(target_pct: reward_pct, window: 2 min)
  }
  reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous }
}'''


def _threshold_callee(ir_json):
    return ir_json["thresholds"]["env_t"]["threshold_call"]["callee"]


def test_bindings_select_baseline_branch():
    r = compile_to_ir_json(
        MODE_SRC, sample_rate_hz=250.0, bindings={"threshold_style": "baseline"}
    )
    assert r.errors == []
    assert _threshold_callee(r.ir_json) == "absolute"
    assert r.meta["bindings"] == {"threshold_style": "baseline"}


def test_no_bindings_is_default_branch_with_stable_hash():
    r = compile_to_ir_json(MODE_SRC, sample_rate_hz=250.0)
    assert r.errors == []
    assert _threshold_callee(r.ir_json) == "percentile"  # default (no-bindings) branch
    assert r.meta["bindings"] == {}
    # The portal's compile-once cache keys on content_hash; adding the bindings
    # passthrough must not move the no-bindings hash. We assert that property as
    # an EQUIVALENCE (no-bindings == explicit bindings={}) plus a well-formed
    # digest, rather than pinning an absolute literal. content_hash is derived
    # from baked filter coefficients, which differ across scipy versions, and
    # scipy>=1.16 dropped Python 3.10 — so no single literal is reproducible
    # across the CI matrix. The prior golden (captured on a dev machine's scipy)
    # never reproduced on CI and left `tests` red on every run since it landed.
    assert r.meta["content_hash"].startswith("sha256:")
    r_empty = compile_to_ir_json(MODE_SRC, sample_rate_hz=250.0, bindings={})
    assert r.meta["content_hash"] == r_empty.meta["content_hash"]


def test_bindings_none_and_empty_are_byte_identical():
    r_omitted = compile_to_ir_json(MODE_SRC, sample_rate_hz=250.0)
    r_none = compile_to_ir_json(MODE_SRC, sample_rate_hz=250.0, bindings=None)
    r_empty = compile_to_ir_json(MODE_SRC, sample_rate_hz=250.0, bindings={})
    assert r_omitted.ir_json_text == r_none.ir_json_text == r_empty.ir_json_text
    assert (
        r_omitted.meta["content_hash"]
        == r_none.meta["content_hash"]
        == r_empty.meta["content_hash"]
    )
    assert r_none.meta["bindings"] == {} and r_empty.meta["bindings"] == {}


def test_invalid_binding_choice_is_resolve_diagnostic():
    r = compile_to_ir_json(
        MODE_SRC, sample_rate_hz=250.0, bindings={"threshold_style": "nonsense"}
    )
    assert r.ir_json is None
    assert len(r.errors) == 1
    assert r.errors[0].stage == "resolve"
    assert "threshold_style" in r.errors[0].message
    assert "choices" in r.errors[0].message
    assert r.meta["bindings"] == {"threshold_style": "nonsense"}


def test_unknown_binding_name_is_resolve_diagnostic():
    r = compile_to_ir_json(
        MODE_SRC, sample_rate_hz=250.0, bindings={"no_such_control": "x"}
    )
    assert r.ir_json is None
    assert len(r.errors) == 1
    assert r.errors[0].stage == "resolve"
    assert "no_such_control" in r.errors[0].message
