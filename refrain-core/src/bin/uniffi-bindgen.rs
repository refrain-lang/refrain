//! Thin wrapper around uniffi's bindings generator CLI. Building this bin via
//! the crate (rather than `cargo install uniffi-bindgen`) guarantees the
//! generator version always matches the pinned `uniffi` library version, so
//! the generated Swift/Kotlin can never drift from the scaffolding the cdylib
//! actually exports. Built only under `--features uniffi` (see Cargo.toml).
//!
//! Usage (from `refrain-core/`):
//!   cargo run --features uniffi --bin uniffi-bindgen -- \
//!       generate --library <path-to-cdylib> --language swift  --out-dir bindings/swift
//!   cargo run --features uniffi --bin uniffi-bindgen -- \
//!       generate --library <path-to-cdylib> --language kotlin --out-dir bindings/kotlin

fn main() {
    uniffi::uniffi_bindgen_main()
}
