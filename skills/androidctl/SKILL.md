---
name: androidctl
description: "Use when a task requires operating an Android device/emulator or Android app through the androidctl CLI."
---

# AndroidCtl CLI

Use this skill to drive an existing Android device or emulator through documented
`androidctl` commands and public CLI outputs. Prefer documented AndroidCtl
commands for setup, observation, actions, waits, and artifact collection.

## Environment Check

Before running device commands, verify the CLI is available:

```bash
command -v androidctl
```

If `androidctl` is unavailable, install the published release into the active
Python environment before continuing:

```bash
python -m pip install androidctl
```

## Quick Start

```bash
# First connection path for an already authorized ADB device.
androidctl setup --adb

# Manual connection path when the Android app is already running and ready.
androidctl connect --adb --token <device-token>

# Operate from observed refs.
androidctl observe
androidctl tap n1
androidctl wait --until idle
androidctl observe

# Collect evidence and close the runtime when finished.
androidctl screenshot
androidctl close
```

Use `list-apps` before opening an installed launcher app:

```bash
androidctl list-apps
androidctl open app:<package-name>
androidctl open https://example.com
androidctl open url:<target>
```

## Workspace Discipline

Commands use this workspace resolution order:

1. `--workspace-root`
2. `ANDROIDCTL_WORKSPACE_ROOT`
3. the current directory

Use an explicit workspace when operating from scripts, a different directory, or
multiple projects. Treat `.androidctl/` as sensitive runtime state and artifact
storage. Do not commit it, expose tokens, or paste local absolute artifact paths.

## Command Families

- Setup and connection: `setup`, `connect`, `adb-pair`, `adb-connect`.
- Screen state and app discovery: `observe`, `list-apps`, `open`.
- Ref actions: `tap`, `long-tap`, `focus`, `type`, `submit`, `scroll`.
- Global actions: `back`, `home`, `recents`, `notifications`.
- Synchronization and evidence: `wait`, `screenshot`, `close`.

`setup`, `adb-pair`, and `adb-connect` are onboarding auxiliary commands. They
write human-readable progress and diagnostics to `stderr`, use exit codes for
success or failure, and do not provide a stable XML or JSON result contract.

## Ref And Screen Discipline

Use refs only from the latest `androidctl observe` output. Do not invent refs or
reuse old refs after navigation, app changes, `SCREEN_UNAVAILABLE`,
`POST_ACTION_OBSERVATION_LOST`, or a global action that may leave a
no-fresh-current window. Run a fresh `observe` before the next ref action or
relative wait.

Use explicit `--screen-id` only when replaying against a known observed screen.
Otherwise omit it and let the CLI bind the live current screen.

Ref actions:

```bash
androidctl tap n3
androidctl long-tap n3
androidctl focus n4
androidctl type n4 "text"
androidctl submit n4
androidctl scroll n8 down
```

Scroll directions are `up`, `down`, `left`, `right`, and `backward`.

Wait after actions, then observe again:

```bash
androidctl wait --until idle
androidctl wait --until screen-change
androidctl wait --until gone --ref n3
androidctl wait --until text-present --text "Done"
androidctl wait --until app --app com.android.settings
```

`--timeout` defaults to `2000ms`; durations are integer values ending in `ms` or
`s`.

## Output Discipline

Parse stdout, stderr, and exit code together:

- Semantic commands write `<result>` on stdout.
- `connect`, `screenshot`, and `close` write `<retainedResult>` on stdout.
- Successful `list-apps` writes `<listAppsResult>` on stdout.
- Usage, startup, environment, and connection failures write `<errorResult>` on
  stderr.
- Semantic or retained command results with `ok="false"` still appear on stdout
  and use a nonzero exit code.

Exit codes are `0` for success, `1` for command or semantic failure, `2` for
usage failure, and `3` for environment failure.

Public artifact paths are workspace-relative, such as
`.androidctl/screenshots/...` and `.androidctl/artifacts/screens/...`. Only
consume documented artifact attributes.

## ADB Escape Hatch

Prefer AndroidCtl for normal operation. If AndroidCtl is blocked and an
authorized ADB target is available, use raw `adb` only as a temporary escape
hatch to restore device state or complete the immediate blocking step. After any
raw ADB action, re-establish AndroidCtl truth with `androidctl observe` or
reconnect/setup before using refs, waits, artifacts, or XML results again.

## Recovery Routing

- `USAGE_ERROR`: fix CLI syntax, required predicate options, ref format, scroll
  direction, duration format, or blank `--screen-id`.
- `WORKSPACE_BUSY`: use a different workspace, close the conflicting runtime, or
  check `ANDROIDCTL_OWNER_ID`.
- `DEVICE_NOT_CONNECTED`: run `setup` or `connect` again and confirm the runtime
  was not closed.
- `SCREEN_UNAVAILABLE`: run `observe`; if the runtime is broken, reconnect.
- `ACCESSIBILITY_NOT_READY`: enable the AndroidCtl Accessibility service or use
  `setup --manual-accessibility`.
- Multiple ADB devices: pass `--serial` to `setup` or `connect`.
- Token/auth errors: use the current token shown by the Android app and reconnect.
- LAN failures: confirm reachable host, port, network path, and current token.
- Workspace or artifact write failures: choose a writable workspace and inspect
  `.androidctl/` permissions.

## References

- Read [references/setup-and-workspace.md](references/setup-and-workspace.md)
  for setup/connect failures, ADB serial selection, wireless helpers, manual
  Accessibility fallback, workspace owner/version mismatch, and `.androidctl/`
  state boundaries.
- Read [references/observe-action-wait.md](references/observe-action-wait.md)
  for screen-driven automation, ref reuse, wait predicate selection, app
  opening, and `--screen-id` decisions.
- Read [references/xml-errors-artifacts.md](references/xml-errors-artifacts.md)
  for XML family parsing, stdout/stderr/exit code decisions, artifact
  collection, and public-code recovery.
