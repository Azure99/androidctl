---
name: androidctl
description: Use when pairing with an Android device and managing its AndroidCtl session and running supervised device shell scripts through the CLI.
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
takeover only on explicit user instruction. After device-side release, explicitly
open again only when continuing is intended.

## Results

| Result | Next action |
| --- | --- |
| `done` | Read the completed facts. |
| `failed` | Read the reason and retained registration or confirmation facts. |
| `not sent` | Correct the reported problem before a new request. |
| `UNKNOWN` | Preserve the result and diagnose before further changes. |
| `still running` | For a shell job, query or cancel its original ID. For `daemon stop`, recheck with `daemon status`. |

## Run Jobs

```sh
androidctl --device DEVICE -s task shell --script-file task.sh
androidctl --device DEVICE -s task job get JOB_ID
```

Supply one complete script after `--` or through `--script-file`. Prefer UTF-8
without BOM and LF line endings, and quote it against host-shell expansion.
Read [script limits and job rules](reference.md#jobs-and-files) before large scripts
or background work. `--wait 0` ends only this call's wait; `--background` leaves
the job supervised. Delegated App/system-service work needs its own business check.

Read both execution and cleanup facts; a known script result can accompany
cleanup failures. The reference explains [shell exits and cleanup](reference.md#jobs-and-files).

Keep the original job ID; query or cancel that ID and preserve cleanup failures.

For shell, read `helperAlive`, `cleanup`, `cleanupError` and `signalErrors`;
`helperAlive=false` can coexist with
`cleanup=unknown`. Signal errors mean processes may still modify data. For shell UNKNOWN without
a job ID, use the reference's
[correlation procedure](reference.md#shell-without-an-initial-job-id).

## Recover And Finish

`status` reports worker/session/resources and failed connection checks.
Use the reference's [connection
recovery](reference.md#connection-and-sessions), [ADB route setup](reference.md#device-setup-and-adb-route)
or [device diagnostics](reference.md#device-diagnostics) for those specific failures.
Certificate changes require a trusted export. Forgetting a pairing does not stop
remote work. Force executor exit can leave descendants and temporary files.

Verify the business result and restore settings changed temporarily to carry out the task.
Remove only your remote temporary objects, confirm original jobs/helpers ended,
then `session close` unless the supervising workflow owns closure. Preserve local
deliverables and recovery records. Report the result, remaining uncertainties and
links using the reported absolute paths.
