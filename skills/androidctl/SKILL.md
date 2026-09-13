---
name: androidctl
description: Use when pairing with an Android device and managing its AndroidCtl session through the CLI.
---

# AndroidCtl

Use the installed `androidctl` CLI. The user prepares shell-UID Shizuku, enables
Gateway and supplies a reachable address and current pairing code. Host and App
versions must match. Follow the [upgrade guidance](reference.md#connection-and-sessions)
before replacing either package.

Wait for each original host process, retain all output chunks and the CLI exit
code. In PowerShell capture `$LASTEXITCODE` immediately after AndroidCtl returns
and propagate it with `exit` when wrapping the command. An outer wrapper status
does not replace the CLI status.

## Start A Task

Use `devices` to find an existing pairing, or pair with the supplied address/code.
Keep the returned device ID and one named session throughout the task. Global
options precede the command.

```sh
androidctl pair 192.168.1.20:18843 000042 --name phone
androidctl --device DEVICE -s task session open
```

Read the result before continuing.
The unnamed `default` session is shared. Respect `busy`/`connectionBusy`; use
takeover only on explicit user instruction.

## Results

| Result | Next action |
| --- | --- |
| `done` | Read the completed facts. |
| `failed` | Read the reason and retained registration or confirmation facts. |
| `not sent` | Correct the reported problem before a new request. |
| `UNKNOWN` | Preserve the result and diagnose before further changes. |

## Recover And Finish

`status` reports worker/session and failed connection checks.
Use the reference's [connection
recovery](reference.md#connection-and-sessions), [ADB route setup](reference.md#device-setup-and-adb-route)
or [device diagnostics](reference.md#device-diagnostics) for those specific failures.

Close the session when finished:

```sh
androidctl --device DEVICE_UUID -s task session close
```
