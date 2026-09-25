# AndroidCtl Command Reference

This page defines externally observable CLI behavior.
Use the accompanying [SKILL](SKILL.md) for an agent's task workflow and
[device setup](#device-setup-and-adb-route) for connection prerequisites.
`androidctl COMMAND --help` gives accepted parameters.

Global `--device`, `-s`/`--session` and `--json` precede the command.

## Command Index

| Purpose | Commands | Details |
| --- | --- | --- |
| Device access | `pair`, `devices`, `device select/address/trust/forget`, `status` | [Connections](#connection-and-sessions) |
| Ownership and host | `session open/close/takeover`, `daemon status/stop` | [Sessions](#connection-and-sessions) |

## Connection And Sessions

Device selection is `--device`, then `ANDROIDCTL_DEVICE`, the user-wide saved
selection, then the sole paired device. Session selection is `-s`/`--session`,
then `ANDROIDCTL_SESSION`, then `default`. Invalid selected devices never fall
back. Use complete UUIDs or unique aliases; UUID prefix matching is not performed.

Pairing uses a six-digit code valid for 600 seconds, five wrong attempts across
connections, or one registration. Leading zeros matter. The user opens each new
pairing window. Close, Back or Escape cancels that window and its unfinished
registrations, preserving existing pairings.
First contact uses TOFU and cannot resist an active intermediary;
later connections pin the complete certificate. Public/private addresses follow
the same trust rules. There is no automatic NAT traversal. The daemon's IPC is
loopback-only.

`pair` returns the device UUID as `data.deviceId` alongside registration
and confirmation facts. Use that UUID with `--device` for later commands.
It persists a new candidate before registration. An existing candidate is first
checked using its saved credential. If it works, the original pairingId is retained,
even with a different code or alias. Only a device authentication rejection allows
registration of that same candidate using the current code, followed by confirmation.
Network, certificate and welcome identity errors do not trigger registration. This
check shares the command's budget, including when the candidate was never registered.
A wrong code preserves the candidate and any old active pairing. A lost registration
reply is UNKNOWN; the next explicit `pair` checks the saved credential without
repeating a successful registration. If registration succeeds but opening the
confirmation connection fails, the result is failed with the registration fact retained.
If saving the candidate fails, registration is not sent (exit 2), including when the
file was saved but final synchronization or cleanup failed. Preserve that
record: the next explicit `pair` checks it first. Provably unsent or explicitly
rejected registration also retains the candidate for that check.
Failed replacement keeps the old active credential and connection.
When `pair` activates a credential or `device address`/`device trust` updates
configuration, failure to close an old connection appears as a string
`data.cleanupError` with exit 3. The completed facts and updated configuration
remain available. A file synchronization error remains in `error` if closing the
old connection also fails.

`devices` lists complete UUIDs, active pairingIds, local records, selection, pending
confirmation and broken-record identifiers. A pending-only record has no pairingId.
Broken records provide their deviceId, path and error; they have no alias or endpoint.
The list and its selection use the same saved records. Selection source is reported
in `data.context.deviceSource`. `data.selectionDevice` retains the requested or
resolved identifier even when unusable; a list item's `selected` flag and
`context.deviceId` require a confirmed active pairing. Selection errors remain
in `data.selectionError`, while the listing itself returns done with exit 0.

Device management requires a positional complete UUID or unique alias:
`device select DEVICE`, `device forget DEVICE`, `device address DEVICE HOST:PORT`
and `device trust DEVICE PATH`. The global `--device` option and `ANDROIDCTL_DEVICE`
do not supply this target. For a broken record, `device forget` also accepts its
exact path reported by `devices`.
`device select DEVICE` returns done when the new user-wide default is saved. If
final file synchronization or cleanup fails, it retains the new selection and reports the
error with exit 3. A failure before replacement is not sent and keeps the previous
selection.
UUIDs can be used directly with `--device`; no prefix
matching is performed. `pair` and `device address` require `HOST:PORT`, with a port
from 1 to 65535; enclose IPv6 hosts in brackets. `device address DEVICE HOST:PORT`
changes the route, not trust. `device forget DEVICE`
works offline, refuses active local work, and removes that local record; it does
not revoke device credentials or stop remote work.
If unlinking the pairing file fails with an OS error, the result is not sent with
exit 2 and a `localConfiguration` Problem at `local.configuration`; local preparation
may already have closed the connection. A failure before removing the pairing
does not report `forgotten`.
If the pairing was removed but its global selection could not be read or cleared,
the result is failed with exit 1. It retains `forgotten` and `selectionCleared`,
with a `data.selectionError` Problem containing `code`,
`message` and any `stage`. A malformed selection file is preserved. Text displays
`Selection: <message>`; JSON retains the complete Problem.
Management text uses lowercase `true`/`false` for boolean fields and does not add
absent fields.

Device-local revocation rejects new requests. Requests already admitted by the
worker may still complete. Revocations made while Gateway is off take effect in
the worker when Gateway is next enabled and the worker reconnects.

Certificate renewal keeps the device key and pairings but invalidates old host
pins. With Gateway enabled, use **Renew certificate**, export through a trusted
channel, then `androidctl device trust UUID certificate.pem` on each host.
Export/renewal remain available when expiration prevents listening. Renewal
restarts the listener; if cleanup blocks it, the App reports no renewal. Finish
cleanup, use **Refresh status**, then explicitly renew again.

One device has one owner; different devices can run concurrently.
Use `session open`, `session close` or `session takeover`; inspect state with `status`.
Bare `session` shows command help and exits with 2 without contacting the daemon.
The default idle limit is ten minutes; `session open --idle DURATION` accepts
whole seconds from `1s` to `24h` (for example `10m`); fractional seconds are rejected.
`busy` identifies the current holder. Explicit `session takeover` can take a
session of the same pairing only when no foreground resources remain. It is not
permission to interrupt another task.

`connectionBusy` reports another session occupying the connection. Same-session
calls can wait within their original budget. `status` separates the
active owner (`session.name` and `session.pairingName`) from the caller state
(`session.callerState`, with values `active` or `none`).

Successful `status` reports worker, actual session holder and Gateway
state without duplicate checks. A failed observation includes local pairing,
actual authentication (new or reused) and worker checks; later checks stay not
checked after an earlier failure. A valid device `UNKNOWN` remains `UNKNOWN`.
These checks do not prove a credential was revoked when
the device is unreachable.
Connection `stage=local.*` names IPC and `device.*` names device transport, including
connect/TLS/WebSocket/authentication phases; remote errors retain their own stage.
Device `protocolMismatch` reports this CLI's release and requires a matching App.
Rejection does not always include the peer's version.

**Gateway off** stops new requests; stopping/failed may still have live work.
After that work ends, **Refresh status** can confirm completion.
**Stop idle executor** requests worker shutdown; Refresh then attaches a new worker.
Wait for stopping to finish; if it fails, use **Refresh status** after the work ends.

Use **Refresh status** to reconnect when the worker is unavailable. Cached
management state may still show the previous worker.

Unused device connections close after 45 seconds; the daemon exits after about
five continuous idle minutes with no local work or connections.

Finish active work and run `androidctl daemon stop` before replacing the host
package, including a same-version reinstall. On a new App version's first executor
attachment, Shizuku replaces the old worker, interrupting unfinished work; finish
that work before upgrading. Before reinstalling the same App version, use
**Stop idle executor** first.

`daemonVersion` rejects a running daemon with another version before connection,
including `daemon stop`. Wait for the idle exit above, or, if a CLI matching that
daemon version is still installed, use it to stop the daemon after active work
finishes.

With no daemon present, `daemon stop` returns done, reason `already stopped`,
exit 0 without starting one. An accepted stop of a running daemon still requires
confirmed shutdown of the same instance. Busy, configuration/version errors and
cleanup failures keep their own results.

### Device Setup And ADB Route

Install the matching APK. Start Shizuku
through ADB or wireless debugging as shell UID 2000, authorize AndroidCtl, and
enable **Gateway**. Check **Listening** and **Executor attached**, then choose
**Show new pairing code**. Pair using its address and current code; displayed
addresses are candidates, not connectivity tests. IPv6 uses `[ADDRESS]:PORT`,
with the host's outgoing interface zone for link-local addresses.

For an explicit ADB route, identify the device and choose an unused host port:

```sh
adb devices -l
adb forward --list
adb -s SERIAL forward --no-rebind tcp:18879 tcp:18843
androidctl pair 127.0.0.1:18879 000042 --name phone
```

Replace the serial, ports and code with the prepared values. For an existing
pairing, use `androidctl device address UUID 127.0.0.1:18879` instead of
pairing again. Restore the same serial/route after disconnect; AndroidCtl neither
maintains mappings nor falls back to LAN. Remove only your mapping when finished:
`adb -s SERIAL forward --remove tcp:18879`. After reboot, the user starts Shizuku,
opens AndroidCtl and enables Gateway again.

### Device Diagnostics

With an existing ADB route, read the running Gateway's management state:

```sh
adb -s SERIAL shell dumpsys activity service com.rainng.androidctl/com.rainng.androidctl.GatewayService
```

This dump does not request fresh worker status or prove an operation completed;
worker state is cached, while the pairing list is read when dumping. Missing
management facts are null, including gateway traffic when no network instance
exists. Do not start a service just to obtain it. The worker lock is
`/data/local/tmp/androidctl/worker.lock`. File existence does not prove a held lock,
and deleting a file does not stop its owner.

## Results And Budgets

| Result | Usual exit | Meaning |
| --- | ---: | --- |
| `done` | 0 | The command's stated condition completed. |
| `failed` | 1 | A known failure; partial effects can remain. |
| `not sent` | 2 | The request was not dispatched. |
| `UNKNOWN` | 3 | Dispatch or its final outcome is uncertain. |
| `still running` | 4 | `daemon stop` has not confirmed shutdown. Recheck the daemon with `daemon status`. |

Text headings use the same fixed command path as JSON `command`.

### JSON Field Guide

Fields depend on the command and outcome; absence does not mean false.
Top-level `command` is the fixed public command path, including on failure and
pre-dispatch rejection. Targets and flags do not become part of that field.
Argument errors preserve an identified command path; without one they use `arguments`.

| Location | Interpretation |
| --- | --- |
| `command`, `result`, optional `reason`, `error`, `exit` | Top-level outcome; `error` supplies code/message and optional stage. An explicit `exit` overrides the usual result exit code. |

Durations use nonnegative integers with `ms`, `s`, `m` or `h`; only `0` may
omit its unit. Each option retains its own range. Requests share one finite
budget across local lookup, connection, authentication and device work.

## Support And Storage

Credentials are private to the current OS user, under `%LOCALAPPDATA%/AndroidCtl`
on Windows or `~/.local/state/androidctl` on macOS/Linux. Sessions live in worker
memory; restarting the worker loses them. There is no durable operation history.
Preserve pairing and recovery materials when outcomes are uncertain.
