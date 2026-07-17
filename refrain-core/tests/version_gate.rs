use refrain_core::eval::{check_ir_version, MAX_SUPPORTED_IR_VERSION};
use refrain_core::ir::Protocol;

fn minimal(version: &str) -> String {
    format!(
        r#"{{"refrain_ir_version":"{version}","sample_rate_hz":256.0,
            "channels":["Cz"],"inputs":{{}},"derives":{{}}}}"#
    )
}

#[test]
fn refuses_a_newer_schema_version() {
    let p: Protocol = serde_json::from_str(&minimal("99.0")).unwrap();
    let err = check_ir_version(&p).unwrap_err();
    assert!(err.contains("99.0"), "diagnostic must name the version: {err}");
    assert!(err.contains(MAX_SUPPORTED_IR_VERSION));
}

#[test]
fn accepts_supported_versions() {
    for v in ["0.1", "0.2", "0.3"] {
        let p: Protocol = serde_json::from_str(&minimal(v)).unwrap();
        assert!(check_ir_version(&p).is_ok(), "version {v} must load");
    }
}

#[test]
fn missing_version_defaults_to_supported() {
    // Pre-version protocols omit the field; treat as the floor (0.1), not a refusal.
    let p: Protocol = serde_json::from_str(
        r#"{"sample_rate_hz":256.0,"channels":["Cz"],"inputs":{},"derives":{}}"#,
    ).unwrap();
    assert!(check_ir_version(&p).is_ok());
}
