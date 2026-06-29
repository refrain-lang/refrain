import json

from refrain.cli import main

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


def _write(tmp_path, src):
    p = tmp_path / "p.refrain"
    p.write_text(src)
    return str(p)


def test_compile_json_prints_ir(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, SMOKE_SRC), "--sample-rate", "256"])
    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["name"] == "svc_smoke"
    assert obj["sample_rate_hz"] == 256.0


def test_compile_json_meta_flag(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, SMOKE_SRC), "--meta"])
    assert code == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["content_hash"].startswith("sha256:")


def test_compile_json_resolve_error_exits_1(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, BAD_RESOLVE_SRC)])
    assert code == 1
    assert "resolve" in capsys.readouterr().err


def test_compile_json_missing_file_exits_2(tmp_path, capsys):
    code = main(["compile-json", str(tmp_path / "nope.refrain")])
    assert code == 2
