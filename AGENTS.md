# Repository Guidelines

## Project Structure & Module Organization

This repository contains a host CLI, host daemon, shared contracts, and Android device agent.

- `contracts/`: shared Python wire models and command catalog in `src/androidctl_contracts/`; tests in `contracts/tests/`.
- `androidctl/`: Typer CLI in `src/androidctl/`; tests in `androidctl/tests/`.
- `androidctld/`: host daemon, runtime, commands, snapshots, waits, and device RPC in `src/androidctld/`; tests in `androidctld/tests/`.
- `android/`: Kotlin app, foreground RPC service, accessibility service, resources, and JVM tests.
- `docs/`: architecture, RPC, and verification notes.

## Build, Test, and Development Commands

Use `task` from the repository root.

- `task test`: runs standard tests for Android unit tests, daemon, CLI, and contracts.
- `task lint`: runs Python Ruff/Black/mypy and Android ktlint/detekt/lint.
- `task format`: applies Ruff/Black fixes and Kotlin ktlint formatting.
- `task quality`: runs each module quality gate.
- `task test:extended`: runs extended `androidctl` process checks.

For focused work, run module tasks such as `task androidctld:test` or `task contracts:lint`.

## Coding Style & Naming Conventions

Python targets 3.10, uses 4-space indentation, Black line length 88, Ruff import sorting, and strict mypy. Keep public wire fields aligned with `contracts/src/androidctl_contracts/command_catalog.py` and daemon API models. Kotlin follows ktlint and detekt; keep Android package paths under `com.rainng.androidctl`.

## Testing Guidelines

Python tests use pytest. Name files `test_*.py` and keep unit and integration scope separated. Android JVM tests run via `testDebugUnitTest`. Add or update contract tests whenever command names, result shapes, RPC tokens, or public screen models change.

## Documentation Updates

When changing code behavior, update matching documentation in the same change. This includes CLI commands or XML output, daemon API/result shapes, Android RPC methods or tokens, runtime state behavior, and verification procedures under `docs/`.

## Commit & Pull Request Guidelines

Use `<type>(<scope>): <subject>` for commits; `scope` is required. Example: `fix(androidctld): handle stale runtime state`. Keep commits focused and explain contract or behavior changes in the body. PRs should include a summary, tests run, issue or motivation, and screenshots/log snippets for CLI XML or Android UI-visible changes.

## Security & Configuration Tips

Do not commit device tokens, daemon secrets, `.androidctl/` runtime state, or generated screenshots unless they are intentional sanitized fixtures. Treat Android RPC auth, owner IDs, and workspace paths as compatibility-sensitive boundaries.
