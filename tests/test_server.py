import hashlib
import json

from fastapi.testclient import TestClient

import refrain
from refrain.server import app

client = TestClient(app)

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


def test_compile_success_returns_ir_and_meta():
    r = client.post("/compile", json={"refrain": SMOKE_SRC, "sample_rate_hz": 256.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"]["name"] == "svc_smoke"
    assert body["meta"]["content_hash"].startswith("sha256:")
    assert body["errors"] == []


def test_compile_error_is_200_with_diagnostics():
    bad = 'protocol "oops" {'
    r = client.post("/compile", json={"refrain": bad})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "parse"


def test_malformed_request_is_422():
    r = client.post("/compile", json={"not_refrain": "x"})
    assert r.status_code == 422


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_version():
    body = client.get("/version").json()
    assert body["refrain_version"] == refrain.__version__
    assert body["ir_versions_supported"] == ["0.1", "0.2"]


def test_compile_returns_canonical_text_matching_hash():
    r = client.post("/compile", json={"refrain": SMOKE_SRC, "sample_rate_hz": 256.0})
    body = r.json()
    text = body["ir_json_text"]
    # The canonical string parses back to the same object the editor sees.
    assert json.loads(text) == body["ir_json"]
    # Cross-language integrity: sha256(canonical bytes) == content_hash.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert body["meta"]["content_hash"] == f"sha256:{digest}"


def test_compile_error_has_null_ir_json_text():
    r = client.post("/compile", json={"refrain": 'protocol "oops" {'})
    assert r.json()["ir_json_text"] is None


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


def test_compile_resolves_supplied_parent():
    r = client.post("/compile", json={
        "refrain": CHILD_SRC, "sample_rate_hz": 256.0, "parents": {"smr_base": PARENT_SRC}})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"]["name"] == "smr_child"
    assert body["unresolved_parents"] == []
    assert body["meta"]["extends"] == "smr_base"


def test_compile_missing_parent_is_200_unresolved():
    r = client.post("/compile", json={"refrain": CHILD_SRC, "sample_rate_hz": 256.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["unresolved_parents"] == ["smr_base"]
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# Resolve-time bindings (mode variants) — the portal's variant-baking path.
# meta.bindings is the portal's capability gate: an old image silently drops
# the request field and returns default IR, so the portal fails closed unless
# the echo matches what it sent.
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

def _threshold_callee(body):
    return body["ir_json"]["thresholds"]["env_t"]["threshold_call"]["callee"]


def test_compile_bindings_baseline_selects_absolute_and_echoes():
    r = client.post("/compile", json={
        "refrain": MODE_SRC, "sample_rate_hz": 250.0,
        "bindings": {"threshold_style": "baseline"}})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert _threshold_callee(body) == "absolute"
    assert body["meta"]["bindings"] == {"threshold_style": "baseline"}
    # Mode controls resolve away: still excluded from the IR controls map.
    assert "threshold_style" not in body["ir_json"]["controls"]


def test_compile_without_bindings_is_adaptive_with_empty_echo():
    r = client.post("/compile", json={"refrain": MODE_SRC, "sample_rate_hz": 250.0})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert _threshold_callee(body) == "percentile"
    assert body["meta"]["bindings"] == {}
    # content_hash is derived from baked filter coefficients (scipy-version
    # dependent; scipy>=1.16 dropped Python 3.10), so the no-bindings hash is
    # asserted as an EQUIVALENCE against an explicit bindings={} compile — the
    # property the portal's compile-once cache actually relies on — rather than
    # a machine-specific literal that never reproduced across the CI matrix.
    assert body["meta"]["content_hash"].startswith("sha256:")
    r_empty = client.post(
        "/compile",
        json={"refrain": MODE_SRC, "sample_rate_hz": 250.0, "bindings": {}},
    )
    assert r_empty.status_code == 200
    assert body["meta"]["content_hash"] == r_empty.json()["meta"]["content_hash"]


def test_compile_invalid_binding_choice_is_resolve_diagnostic():
    r = client.post("/compile", json={
        "refrain": MODE_SRC, "sample_rate_hz": 250.0,
        "bindings": {"threshold_style": "nonsense"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "resolve"
    # The diagnostic names the control and its valid choices.
    msg = body["errors"][0]["message"]
    assert "threshold_style" in msg
    assert "adaptive" in msg and "baseline" in msg


def test_compile_unknown_binding_name_is_resolve_diagnostic():
    r = client.post("/compile", json={
        "refrain": MODE_SRC, "sample_rate_hz": 250.0,
        "bindings": {"no_such_control": "x"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "resolve"
    assert "no_such_control" in body["errors"][0]["message"]


def test_compile_bindings_without_mode_controls_is_resolve_diagnostic():
    # SMOKE_SRC declares no controls at all: any binding must fail loudly
    # rather than silently baking the default variant.
    r = client.post("/compile", json={
        "refrain": SMOKE_SRC, "sample_rate_hz": 256.0,
        "bindings": {"threshold_style": "baseline"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "resolve"
    assert "threshold_style" in body["errors"][0]["message"]
