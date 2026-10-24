# Repository Guidelines

## Project Structure & Module Organization

AndroidCtl has one host package and one Android app. Host source lives in
`androidctl/src/androidctl`; tests live in `androidctl/tests`. Android source is
under `android/app/src/main`; JVM tests are under `android/app/src/test`.
Public instructions live in `skills/androidctl`. The shared
`contract/wire-contract.json` records current wire limits and query methods.
Root `VERSION` anchors host/app version lockstep.
Release tooling lives in `tools/`. See [Release Operations](tools/release/README.md)
for signing, Android and Python candidate preparation with `task release:android` /
`task release:pypi`, and manual publishing.

## Build, Test, and Development Commands

Python requires 3.12 or newer. Use the existing `.conda` environment; otherwise
create it with `conda create -p ./.conda python=3.12`. Install Task to use Taskfiles.
Use JDK 21, the Gradle wrapper, Android SDK platform 36 and SDK build-tools. Set
the SDK location in `android/local.properties` or `ANDROID_HOME`. APK builds need
the NDK revision named by `androidctlNdkVersion` in `android/gradle.properties`.

Run from the repository root:

```sh
conda run -p ./.conda python -m pip install -e ".[dev]"
conda run -p ./.conda python -m pytest androidctl/tests
conda run -p ./.conda task --list
```

On Windows, run JVM tests with `android/gradlew.bat -p android :app:testDebugUnitTest`.
`task android:build:release` invokes Gradle directly. Release signing requires
`ANDROIDCTL_RELEASE_STORE_FILE`, `ANDROIDCTL_RELEASE_STORE_PASSWORD`,
`ANDROIDCTL_RELEASE_KEY_ALIAS` and `ANDROIDCTL_RELEASE_KEY_PASSWORD`.

## Coding Style & Testing

Prefer typed Python functions and snake_case identifiers. Wire models expose
camelCase aliases; construct them with snake_case fields. Keep Kotlin and Python
wire results aligned. Test observable behavior and affected cross-language contracts.
See [contract instructions](contract/README.md) for intentional fixture changes.

Use concise Conventional Commit style, for example
`fix(core): preserve publication facts`. Describe behavior, validation and relevant
device/Shizuku assumptions when reviewing changes.

## Security & Configuration Tips

Credentials are private to the OS user under `%LOCALAPPDATA%/AndroidCtl` on Windows
or `~/.local/state/androidctl` on macOS/Linux. Sessions live in worker memory.
Do not commit generated runtime state, keystores or device credentials. Keep
release signing values out of source control.
