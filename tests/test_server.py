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
