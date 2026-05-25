# Android AAR Gradle module — deferred implementation plan

> **Status:** deferred (D2). Ready to execute once two prerequisites exist:
> (1) an Android build environment (Android SDK + Gradle) to validate against,
> and (2) an NDK-provisioned CI runner so the `android-aar` job in
> `.github/workflows/mobile.yml` can actually run. Neither can be validated from
> the desktop dev box, which is why this is a plan, not a PR.

**Goal:** Assemble the uniffi-generated Kotlin bindings + the per-ABI native
`.so` files into a distributable Android `.aar`, and wire its assembly into CI.

## Current state (already wired, do not rebuild)

`.github/workflows/mobile.yml` job `android-aar` already:
- adds the three Android Rust targets,
- sets up NDK r27c (`nttld/setup-ndk`) + `cargo-ndk`,
- builds per-ABI `.so` into `refrain-core/android/src/main/jniLibs/{arm64-v8a,armeabi-v7a,x86_64}/` via `cargo ndk … build --release --lib --features uniffi`,
- uploads `jniLibs/` + `refrain-core/bindings/kotlin/` as the `android-jni-libs` artifact.

The uniffi Kotlin lives at `refrain-core/bindings/kotlin/uniffi/refrain_core/refrain_core.kt` (package `uniffi.refrain_core`, JNA-based — it imports `com.sun.jna.*`).

**The only gap is the Gradle library module + the `assembleRelease` CI step.**

## What to add

### 1. Gradle Android library module at `refrain-core/android/`

```
refrain-core/android/
  build.gradle.kts
  settings.gradle.kts          # or add to a top-level settings if a root Gradle is introduced
  gradle.properties
  gradlew, gradlew.bat, gradle/wrapper/…   # `gradle wrapper --gradle-version 8.7`
  src/main/
    AndroidManifest.xml
    kotlin/uniffi/refrain_core/refrain_core.kt   # copied/symlinked from bindings/kotlin/
    jniLibs/{arm64-v8a,armeabi-v7a,x86_64}/librefrain_core.so   # produced by cargo-ndk
```

**`build.gradle.kts`** (Android library; pin versions to whatever the runner provides — these are current-reasonable defaults, the #1 iteration point):

```kotlin
plugins {
    id("com.android.library") version "8.5.0"
    id("org.jetbrains.kotlin.android") version "1.9.24"
}
android {
    namespace = "lang.refrain.core"
    compileSdk = 34
    defaultConfig { minSdk = 24 }
    sourceSets["main"].jniLibs.srcDirs("src/main/jniLibs")
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    // uniffi's Kotlin runtime is JNA-based. Use the @aar artifact so the
    // native JNA dispatcher .so ships inside the AAR.
    implementation("net.java.dev.jna:jna:5.14.0@aar")
    // Only if the generated bindings use async (they currently do not):
    // implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.1")
}
```

**`AndroidManifest.xml`** — minimal (namespace supplies the package on AGP 8):

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android" />
```

**`gradle.properties`** — `android.useAndroidX=true`, `org.gradle.jvmargs=-Xmx2g`.

### 2. CI wiring (`mobile.yml`, `android-aar` job)

After the `cargo ndk … build` step, before/instead of the artifact upload:
- Place the generated Kotlin into `android/src/main/kotlin/` (copy from `bindings/kotlin/`).
- `actions/setup-java@v4` (temurin 17).
- `./gradlew :android:assembleRelease` (working-directory `refrain-core`).
- Upload `refrain-core/android/build/outputs/aar/android-release.aar` as the `RefrainCore.aar` artifact (mirror the iOS `RefrainCore.xcframework` upload).

### 3. Consumer doc

Add an "Android — AAR" section to `refrain-core/bindings/README.md`: how an app depends on the `.aar`, the JNA requirement, and `System.loadLibrary`/uniffi load expectations (uniffi loads `librefrain_core.so` by name).

## Validation / exit criteria (all require the Android env)

- `./gradlew :android:assembleRelease` succeeds on the NDK-provisioned runner.
- The produced `.aar` contains `classes.jar` (with `uniffi/refrain_core/…`) and `jni/{arm64-v8a,armeabi-v7a,x86_64}/librefrain_core.so` + JNA's `.so`.
- Smoke test (stretch): load the `.aar` in a minimal Android app / Robolectric and call one `RefrainCore` method, comparing one event against a golden vector (reuse the conformance fixtures).

## Risks / iteration points

- **Version matrix** (AGP / Gradle / Kotlin / compileSdk / JNA) must match the runner's installed SDK — expect 1–3 CI iterations here.
- **JNA `.so` packaging**: the `@aar` JNA artifact is required so the JNA dispatcher native lib is bundled; a plain `jna` jar will `UnsatisfiedLinkError` at runtime on device.
- **uniffi runtime version** must match the `uniffi` crate version used to generate `refrain_core.kt` (check `refrain-core/Cargo.toml`); a mismatch causes FFI ABI errors.
- **NDK version**: keep `r27c` in sync between the cargo-ndk step and whatever the farm installs.
