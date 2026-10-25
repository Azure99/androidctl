# Repository Guidelines

## Project Structure & Module Organization

AndroidCtl has one host package and one Android app:

- `androidctl/`: CLI, local daemon, protocol models, and host state. Source lives in `androidctl/src/androidctl`; tests live in `androidctl/tests`.
- `android/`: Kotlin Android agent app. Source is under `android/app/src/main`; JVM tests are under `android/app/src/test`.

Public agent instructions live in `skills/androidctl`; `contract/wire-contract.json` supplies the shared Kotlin/Python wire samples.

Release tooling lives in `tools/`; version lockstep is anchored by root `VERSION`.
See [Release Operations](tools/release/README.md) for signing, candidate preparation, and manual publishing. Candidate tasks are `release:android` and `release:pypi`; `android:build:release` only runs Gradle.

## Build, Test, and Development Commands

Python requires 3.12 or newer. Use the existing `.conda` environment; otherwise
create it with `conda create -p ./.conda python=3.12`. Install
[Task](https://taskfile.dev/docs/installation) to run the commands below.

Use JDK 21, the Gradle wrapper, Android SDK platform 36 and SDK build-tools. Set
the SDK location in `android/local.properties` or `ANDROID_HOME`. APK builds need
the NDK revision named by `androidctlNdkVersion` in
[`android/gradle.properties`](android/gradle.properties); install it with SDK
Manager under the SDK's `ndk/` directory. JVM tests and lint do not require the NDK.

Run from the repository root:

```sh
conda run -p ./.conda python -m pip install -c constraints-ci.txt -e ".[dev]"
conda run -p ./.conda task quality
conda run -p ./.conda task ci
conda run -p ./.conda task --list
```

Task uses the selected environment's `python`. `quality` covers host, CLI process,
Android and release tooling; `ci` also checks Linux/Darwin type branches. Signed
candidate preparation is separate. Use `task --list` for individual targets and
their scope. Runtime dependencies are in `pyproject.toml`; `constraints-ci.txt`
pins the directly tested development tools. The [CI workflow](.github/workflows/quality.yml)
defines platform/version jobs and triggers.

## Coding Style & Naming Conventions

Python uses Ruff for linting and formatting with line length 88, and the mypy checks configured in `pyproject.toml`. Prefer typed functions and snake_case identifiers. Wire models expose camelCase aliases; construct them with snake_case fields.

Kotlin uses ktlint, detekt, and Android lint. Keep Android wire results aligned with Python protocol models and observable behavior tests.

## Testing Guidelines

Python tests use pytest and `test_*.py` names. Android tests use JVM classes named `*Test.kt`. Verify affected cross-language contracts when changing result shapes, observations, or RPC tokens. Use real process/device calls where practical; keep test coverage proportional to the affected behavior.

Run the narrow module test first, then `task quality` before submitting broad changes.

To regenerate the shared contract after an intentional change, run this from the
repository root in PowerShell:

```powershell
$env:ANDROIDCTL_CONTRACT_OUTPUT = Join-Path (Get-Location) 'android/app/build/wire-contract.candidate.json'
try {
    & .\android\gradlew.bat -p .\android :app:testDebugUnitTest --tests 'com.rainng.androidctl.WireContractTest.deviceProjectionAndTransferMatchTheSharedContract' --rerun-tasks
} finally {
    Remove-Item Env:ANDROIDCTL_CONTRACT_OUTPUT
}
```

Export ends with an intentional test failure saying the candidate was exported and
the reviewed contract was not validated. Check that message and the generated file,
review the differences, then replace `contract/wire-contract.json`. With the output
environment variable unset, validate both producers and consumers:

```powershell
& .\android\gradlew.bat -p .\android :app:testDebugUnitTest --tests 'com.rainng.androidctl.WireContractTest' --rerun-tasks
conda run -p ./.conda python -m pytest androidctl/tests/test_wire_contract.py
```

## Commit & Pull Request Guidelines

Use concise Conventional Commit style, for example `fix(files): preserve publication facts`. Use `type(scope): imperative summary` with a scope and keep commits focused.

Pull requests should include behavior changes, affected modules, test commands, and device/Shizuku assumptions. Include JSON result samples or screenshots for CLI and Android UI changes.

## Security & Configuration Tips

Daemon configuration and credentials are private to the OS user under `%LOCALAPPDATA%/AndroidCtl` on Windows or `~/.local/state/androidctl` on macOS/Linux. Sessions and device execution state live in memory. Do not commit generated runtime state, screenshots, staged release artifacts, keystores, or device credentials. Android release signing requires environment variables such as `ANDROIDCTL_RELEASE_STORE_FILE`; keep them out of source control. Public documentation covers positioning and usage; detailed internal design stays in the separate docs repository.
