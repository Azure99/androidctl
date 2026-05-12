# Setup And Workspace

Use this reference when setup, connect, ADB device selection, wireless debugging,
manual Accessibility fallback, workspace ownership, release mismatch, or workspace
state affects an `androidctl` task.

## Setup Paths

Preferred first path for an already authorized USB ADB device:

```bash
androidctl setup --adb
androidctl setup --adb --serial <device-serial>
```

`setup --adb` installs or opens the Android Device Agent, provisions a token,
attempts Accessibility enablement, connects the host CLI to the device, and
verifies readiness. It is best effort and still depends on the user accepting
USB ADB authorization and any Android/OEM prompts.

Useful setup options:

- `--dry-run`: print the onboarding plan without running ADB or mutating a
  device.
- `--skip-install`: skip APK install only; app launch, token provisioning, and
  Accessibility handling still run.
- `--manual-accessibility`: skip ADB Accessibility writes and print the manual
  enablement path.
- `--apk <path>`: use a specific Android Device Agent APK.

Manual connection when the Android app is already ready:

```bash
androidctl connect --adb --token <device-token>
androidctl connect --adb --serial <device-serial> --token <device-token>
androidctl connect --host <device-host> --port <device-port> --token <device-token>
```

Use `--serial` whenever more than one authorized ADB device or emulator is
eligible. If exactly one ADB target is in `device` state, ADB setup/connect can
select it automatically.

## Wireless ADB

Wireless debugging still requires the user to read the device-side pairing
endpoint, connect endpoint, and pairing code. Host-side helper flow:

```bash
androidctl adb-pair --pair <host:pair-port> --code <code>
androidctl adb-connect <host:connect-port>
androidctl setup --adb --serial <host:connect-port>
```

`adb-pair` and `adb-connect` wrap ADB only. They write human-readable progress or
diagnostics to `stderr`, keep successful `stdout` empty, and have no stable XML
or JSON result family. `adb-pair` must not echo the pairing code. Their
`--workspace-root` option is accepted for CLI consistency but does not select or
write AndroidCtl runtime state.

## Workspace Rules

Workspace root resolution order:

1. `--workspace-root`
2. `ANDROIDCTL_WORKSPACE_ROOT`
3. current directory

The CLI does not auto-promote to the git repository root. Runtime state and
artifacts live under `<workspaceRoot>/.androidctl/`. Use the same workspace for
`setup`, `connect`, `observe`, actions, screenshots, and `close` when they are
part of the same device task.

Treat `.androidctl/` as sensitive runtime state. Do not commit it, expose tokens,
or rely on local absolute paths from the host.

## Workspace Ownership And Version

A workspace can be owned by one active AndroidCtl runtime at a time. Use the same
workspace for commands that belong to one device task. `close` shuts down the
runtime for the selected workspace; it does not create a new runtime only to
close it.

If another session controls the workspace, the CLI reports `WORKSPACE_BUSY`. Use
a different workspace, close the other session if appropriate, or set a stable
`ANDROIDCTL_OWNER_ID` for repeated agent/CI steps. This owner id is not an auth
token.

If the CLI reports a release or version mismatch, install a matching published
AndroidCtl release for the host and Android app, then reconnect.

## Failure Routing

- No eligible ADB device: confirm `adb devices` shows the target in `device`
  state and that the user accepted authorization.
- Multiple ADB devices: rerun with `--serial`.
- Accessibility not ready: follow setup's manual instructions or rerun with
  `--manual-accessibility`.
- Token/auth failure: use the current token shown in the Android app UI.
- LAN failure: confirm device host, port, network path, Android app readiness,
  and token.
- Workspace/artifact write failure: choose a writable workspace and check
  `.androidctl/` permissions.
