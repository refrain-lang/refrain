# Mobile bindings (Swift + Kotlin) via uniffi

These are the generated, committed FFI proof artifacts the iOS and Android apps
integrate. They are produced by [uniffi](https://mozilla.github.io/uniffi-rs/)
from the **`uniffi` cargo feature** of `refrain-core` (a thin wrapper over
`eval::Evaluator` — see `src/mobile.rs`). The feature is fully isolated from the
default build and the `python` (pyo3) feature: there is no scaffolding collision.

| Path | What |
|------|------|
| `swift/refrain_core.swift` | Swift bindings (`RefrainCore`, `Event`, `RefrainError`) |
| `swift/refrain_coreFFI.h` | C header for the static library |
| `swift/refrain_coreFFI.modulemap` | Clang module map referenced by the header |
| `kotlin/uniffi/refrain_core/refrain_core.kt` | Kotlin/JVM bindings (JNA-based) |

The exposed API (mirrors the pyo3 binding):

- `RefrainCore(irJson, sampleRateHz, channelNames)` — constructor; throws
  `RefrainError.InvalidIr` if the IR-JSON fails to deserialize.
- `start(skipWarmup)`, `stop()`
- `stepChunkEvents(chunk: [Double], nChannels: UInt32) -> [Event]` — uniffi has
  no 2-D type, so the chunk is a **flat, row-major** `n_samples * n_channels`
  buffer plus `nChannels`. It is reshaped to rows internally via the shared
  `eval::rows_from_flat` helper and forwarded to the unchanged evaluator.
- `Event { timestampS, channel, kind, value }` (value is nullable / optional).

> `setControl` / `lastTaps` are intentionally **not** exposed yet (later
> milestone).

---

## Pinned versions

- `uniffi = "0.29.5"` (library + `cli` feature), pinned in `refrain-core/Cargo.toml`.
- The bindings **generator** is built in-tree as the `uniffi-bindgen` bin
  (`src/bin/uniffi-bindgen.rs`, `required-features = ["uniffi"]`). Building it
  from the crate guarantees the generator version can never drift from the
  `uniffi` library version the cdylib was compiled against. (Equivalent to
  `cargo install uniffi-bindgen --version 0.29.5`, but version-locked for free.)

---

## Regenerate the bindings

Run from `refrain-core/`. The generator reads the FFI metadata directly out of
the compiled library (`--library` mode), so the cdylib must be built first.

```sh
. "$HOME/.cargo/env"

# 1. Build the cdylib (host) WITH the uniffi feature.
cargo build --release --features uniffi          # -> target/release/librefrain_core.dylib (macOS)
                                                  #    target/release/librefrain_core.so   (Linux)

# 2. Generate Swift + Kotlin from that library.
cargo run --release --features uniffi --bin uniffi-bindgen -- \
    generate --library target/release/librefrain_core.dylib \
    --language swift  --out-dir bindings/swift

cargo run --release --features uniffi --bin uniffi-bindgen -- \
    generate --library target/release/librefrain_core.dylib \
    --language kotlin --out-dir bindings/kotlin
```

On Linux CI, swap `librefrain_core.dylib` → `librefrain_core.so`.

> Note: the Kotlin generator tries to auto-format with `ktlint`; if `ktlint`
> isn't on PATH it prints a harmless warning and emits unformatted (but valid)
> Kotlin. Pass `--no-format` to silence it.

---

## Cross-compile the static library for mobile

The `--crate-type staticlib` override clashes with the `uniffi-bindgen` bin
target, so scope it with `--lib`:

```sh
# iOS device (arm64)
cargo rustc --release --lib --target aarch64-apple-ios \
    --crate-type staticlib --features uniffi
# -> target/aarch64-apple-ios/release/librefrain_core.a

# Android arm64
cargo rustc --release --lib --target aarch64-linux-android \
    --crate-type staticlib --features uniffi
# -> target/aarch64-linux-android/release/librefrain_core.a
```

### Built & verified in this environment

| Target | Artifact | Size (unstripped `.a`) |
|--------|----------|------------------------|
| `aarch64-apple-ios` | `target/aarch64-apple-ios/release/librefrain_core.a` | ~32 MB |
| `aarch64-linux-android` | `target/aarch64-linux-android/release/librefrain_core.a` | ~45 MB |

These `.a` archives are large because they are un-stripped release archives;
the linker discards unreferenced objects when producing the final app binary /
`.so`, so the shipped footprint is far smaller.

---

## Packaging — steps for CI (NOT run here)

The steps below need toolchains that are **not present in the dev sandbox**
(Xcode, the Android NDK + cargo-ndk). They must run on the CI farm. The Rust
cross-compiles above and the binding generation above **were** run here; the
packaging below was **not**.

### iOS — `xcframework` (Mac mini CI with Xcode)

Build the static lib for both the device and the **simulator** target, then
combine them into an `.xcframework` alongside the generated Swift + module map.

```sh
# The simulator target is not installed in the dev sandbox — install on CI:
rustup target add aarch64-apple-ios-sim

cargo rustc --release --lib --target aarch64-apple-ios \
    --crate-type staticlib --features uniffi
cargo rustc --release --lib --target aarch64-apple-ios-sim \
    --crate-type staticlib --features uniffi

# uniffi emits refrain_coreFFI.modulemap; xcframework expects it named
# `module.modulemap` inside a headers dir.
mkdir -p Headers
cp bindings/swift/refrain_coreFFI.h Headers/
cp bindings/swift/refrain_coreFFI.modulemap Headers/module.modulemap

xcodebuild -create-xcframework \
    -library target/aarch64-apple-ios/release/librefrain_core.a \
    -headers Headers \
    -library target/aarch64-apple-ios-sim/release/librefrain_core.a \
    -headers Headers \
    -output RefrainCore.xcframework
```

Add `RefrainCore.xcframework` **and** `bindings/swift/refrain_core.swift` to the
Xcode project (the `.swift` file `import`s the `refrain_coreFFI` module the
xcframework provides).

### Android — AAR (needs Android NDK + cargo-ndk — likely NOT installed yet)

> ⚠️ **Caveat for the farm:** building the Android `.so` requires the **Android
> NDK** and **`cargo-ndk`** (`cargo install cargo-ndk`). Neither is assumed
> present on the CI farm — provision them before wiring this up. `aarch64-linux-android`
> is the only Android Rust target installed in the dev sandbox; the other ABIs
> below also need their rustup targets added.

```sh
# One-time setup on CI:
cargo install cargo-ndk
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android
# plus: install the Android NDK and export ANDROID_NDK_HOME

# Build the JNI .so for each ABI into the Android jniLibs layout:
cargo ndk \
    -t arm64-v8a -t armeabi-v7a -t x86_64 \
    -o android/src/main/jniLibs \
    build --release --lib --features uniffi
```

Then assemble the AAR with Gradle, placing the generated Kotlin under
`android/src/main/kotlin/` (it already has the `uniffi.refrain_core` package
path) and the `.so` files under `android/src/main/jniLibs/<abi>/`:

```sh
./gradlew :refrain-core:assembleRelease   # -> refrain-core-release.aar
```

The Kotlin bindings use **JNA**, so the AAR's Gradle module must depend on
`net.java.dev.jna:jna:<version>@aar`.
```

---

## What ran here vs. deferred to CI

| Step | Where |
|------|-------|
| `uniffi` feature builds; cdylib compiles | ✅ here |
| Swift + Kotlin bindings generated | ✅ here |
| `aarch64-apple-ios` staticlib cross-compile | ✅ here |
| `aarch64-linux-android` staticlib cross-compile | ✅ here |
| `aarch64-apple-ios-sim` staticlib | ⏭ CI (target not installed) |
| `xcframework` assembly (`xcodebuild`) | ⏭ CI (needs Xcode) |
| Android `.so` per ABI (`cargo-ndk`) + AAR (Gradle) | ⏭ CI (needs NDK + cargo-ndk) |
