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
