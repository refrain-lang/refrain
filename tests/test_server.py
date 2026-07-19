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
    assert body["ir_versions_supported"] == ["0.1", "0.2", "0.3"]
    assert body["schema_versions"] == ["0.1", "0.2", "0.3"]


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


# ---------------------------------------------------------------------------
# Amp-neutral resolution (`reference: amp.reference`). The portal passes the
# bundled amp-profile NAME; the compiler resolves the protocol against it so
# the montage reference folds to the device's real reference. meta.amp is the
# capability probe (parallel to meta.bindings): an old image drops the field
# and returns fail-closed/unfolded IR with no echo, so callers fail closed.
# ---------------------------------------------------------------------------

# `reference: amp.reference` — hardware-neutral. Active site Cz and a 250 Hz
# floor are hostable by both brainbit_flex (250 Hz) and q21 (256 Hz).
AMP_NEUTRAL_SRC = '''protocol "smr_neutral" {
  meta { version = "1.0"; evidence = "clinical"; description = "amp-neutral smr at Cz" }
  requires {
    sample_rate = ">= 250 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: amp.reference)
  }
  output { audio_gain = 0 }
}'''

# Same neutral form, but sited at Fz — an electrode brainbit_flex (Cz/F3/F4/Pz)
# cannot host. The 250 Hz floor clears brainbit's rate check, so resolution
# reaches — and fails at — the channel guard rather than the sample-rate guard.
AMP_NEUTRAL_FZ_SRC = '''protocol "smr_neutral_fz" {
  meta { version = "1.0"; evidence = "clinical"; description = "amp-neutral smr at Fz" }
  requires {
    sample_rate = ">= 250 Hz"
    channels    = ["Fz"]
  }
  input "raw" {
    montage = referential(active: "Fz", reference: amp.reference)
  }
  output { audio_gain = 0 }
}'''


def _montage_reference(body):
    args = body["ir_json"]["inputs"]["raw"]["montage"]["args"]
    ref = next(a for a in args if a["name"] == "reference")
    return ref["value"]["value"]


def test_compile_amp_brainbit_folds_reference_to_device():
    r = client.post("/compile", json={
        "refrain": AMP_NEUTRAL_SRC, "sample_rate_hz": 250.0, "amp": "brainbit_flex"})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert body["meta"]["amp"] == "brainbit_flex"
    # BrainBit's dedicated reference is device-applied → folds to "device".
    assert _montage_reference(body) == "device"


def test_compile_amp_q21_folds_reference_to_linked_ears():
    r = client.post("/compile", json={
        "refrain": AMP_NEUTRAL_SRC, "sample_rate_hz": 256.0, "amp": "q21"})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert body["meta"]["amp"] == "q21"
    # Q21 declares A1/A2 → its reference is linked_ears.
    assert _montage_reference(body) == "linked_ears"


def test_compile_amp_neutral_without_amp_is_fail_closed():
    # No amp: `amp.reference` cannot resolve. This is the fail-closed contract,
    # not a regression — the existing resolve-error path (200 + diagnostic).
    r = client.post("/compile", json={
        "refrain": AMP_NEUTRAL_SRC, "sample_rate_hz": 250.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "resolve"
    assert "amp profile" in body["errors"][0]["message"]
    # No amp requested → echoed as null.
    assert body["meta"]["amp"] is None


def test_compile_amp_cannot_host_site_is_missing_channels_error():
    # The flatline fix: an amp that cannot host the protocol's site is rejected
    # loudly at compile, not silently flatlined at session start.
    r = client.post("/compile", json={
        "refrain": AMP_NEUTRAL_FZ_SRC, "sample_rate_hz": 250.0, "amp": "brainbit_flex"})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "resolve"
    assert "missing required channels" in body["errors"][0]["message"]


def test_compile_unknown_amp_is_typed_4xx():
    r = client.post("/compile", json={
        "refrain": AMP_NEUTRAL_SRC, "sample_rate_hz": 250.0, "amp": "no_such_amp"})
    assert 400 <= r.status_code < 500
    detail = r.json()["detail"]
    # Typed, not opaque: names the bad amp and the bundled set it must be from.
    assert detail["error"] == "unknown_amp"
    assert detail["amp"] == "no_such_amp"
    assert "brainbit_flex" in detail["allowed"]


def test_compile_amp_path_traversal_is_typed_4xx():
    # A name is validated against the bundled set — never joined as a path, so
    # traversal/separators are rejected exactly like any other unknown name.
    for bad in ("../secret", "amp_profiles/q21", "q21/../q21", "/etc/passwd"):
        r = client.post("/compile", json={
            "refrain": AMP_NEUTRAL_SRC, "sample_rate_hz": 250.0, "amp": bad})
        assert 400 <= r.status_code < 500, bad
        assert r.json()["detail"]["error"] == "unknown_amp"


def test_compile_without_amp_echoes_null_and_is_unchanged():
    # Omitting amp must not change existing behaviour: a literal-reference
    # protocol compiles exactly as before, with meta.amp echoed as null.
    r = client.post("/compile", json={"refrain": SMOKE_SRC, "sample_rate_hz": 256.0})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert body["meta"]["amp"] is None
    assert body["ir_json"]["name"] == "svc_smoke"
