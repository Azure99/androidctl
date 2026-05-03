# Repository Guidelines

## Project Structure & Module Organization

AndroidCtl is split into four surfaces:

- `contracts/`: shared Python wire models, command catalog, result schemas, and public screen contracts.
- `androidctl/`: host CLI package. Source lives in `androidctl/src/androidctl`; tests live in `androidctl/tests`.
- `androidctld/`: host daemon package. Source lives in `androidctld/src/androidctld`; tests live in `androidctld/tests`.
- `android/`: Kotlin Android agent app. Source is under `android/app/src/main`; JVM tests are under `android/app/src/test`.

Release tooling lives in `tools/`; version lockstep is anchored by root `VERSION`.

## Build, Test, and Development Commands

Use the root `Taskfile.yml` when possible:

- `task test`: run Python module tests and Android unit tests.
- `task lint`: run Ruff, Black checks, mypy, ktlint, detekt, and Android lint.
- `task format`: apply Python and Kotlin formatting fixes.
- `task quality`: run the full local quality gate.
- `task test:extended`: run process-level CLI e2e tests.
- `task release:version-check`: verify all package and Android versions match `VERSION`.

Python requires 3.10. If no environment is active, create one with `conda create -p ./.conda python=3.10` and install `contracts/`, `androidctld/`, and `androidctl/` editable.

## Coding Style & Naming Conventions

Python uses Black and Ruff with line length 88 and strict mypy. Prefer typed functions and snake_case identifiers. Wire models expose camelCase aliases; construct them with snake_case fields.

Kotlin uses ktlint, detekt, and Android lint. Keep RPC/action/snapshot tokens aligned with shared conformance fixtures.

## Testing Guidelines

Python tests use pytest and `test_*.py` names. Android tests use JVM classes named `*Test.kt`. Add contract tests when changing shared models, command catalog entries, result shapes, public screen fields, or Android RPC tokens.

Run the narrow module test first, then `task quality` before submitting broad changes.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit style, for example `fix(androidctl): derive Windows owner identity` and `chore(repo): initial public release`. Use `type(scope): imperative summary` with a scope, keep commits focused, and explain contract or behavior changes in the commit body.

Pull requests should include behavior changes, affected modules, test commands, and any device/ADB assumptions. Include screenshots or XML/result samples for CLI output, setup UI, or screen rendering changes.

## Security & Configuration Tips

Daemon state and tokens are workspace-local under `.androidctl/`. Do not commit generated runtime state, screenshots, staged release artifacts, keystores, or device tokens. Android release signing requires environment variables such as `ANDROIDCTL_RELEASE_STORE_FILE`; keep them out of source control.
