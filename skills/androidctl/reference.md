# AndroidCtl Command Reference

This page defines externally observable behavior for the CLI that exports it.
Use the accompanying [SKILL](SKILL.md) for an agent's task workflow and
[device setup](#device-setup-and-adb-route) for connection prerequisites.
`androidctl COMMAND --help` gives accepted parameters.

Global `--device`, `-s`/`--session` and `--json` precede the command. Use
`androidctl --json snapshot` or `androidctl --json find TEXT`; neither command
accepts a local `--json` option.

## Command Index

| Purpose | Commands | Details |
| --- | --- | --- |
| Installed resources | `apk`, `skill` | [Local files](#local-package-files) |
| Device access | `pair`, `devices`, `device select/address/trust/forget`, `status` | [Connections](#connection-and-sessions) |
| Ownership and host | `use`, `session open/close/takeover`, `daemon status/stop` | [Sessions](#connection-and-sessions) |
| Observe and target | `snapshot`, `find`, `locate`, `wait` | [Observations](#observations-and-targets) |
| Semantic input | `click`, `long-click`, `expand`, `collapse`, `dismiss`, `fill`, `check`, `uncheck`, `focus`, `scroll`, `ime`, `key` | [Targets](#observations-and-targets) |
| Navigate | `apps`, `open`, `back`, `home`, `recents`, `notifications`, `wake` | [Navigation](#observations-and-targets) |
| Images and coordinates | `screenshot`, `tap`, `swipe` | [Images](#images-and-delivery) |
| Jobs and transfers | `shell`, `job get/cancel/output`, `push`, `pull` | [Jobs](#jobs-and-files) |

## Local Package Files

`apk --out NEW_PATH` copies the installed package's APK to one new file and reports
`data.path`, `data.sha256` and `data.version`. A source installation without the APK
reports `apkUnavailable`.

`skill --out NEW_DIRECTORY` exports all top-level `.md` files from the installed
skill, sorted by filename. Start with `SKILL.md`, which links to `reference.md`.
JSON reports `data.path`, `data.version` and `data.files`, a list of
`{name, path, sha256}` for the exported files; there is no top-level skill `sha256`.

These commands require an existing parent and refuse an existing target unchanged.
They do not start the daemon or contact a device. Failure before target creation
is `not sent`; a read/write failure after creation is `failed` with partial output
retained. Skill export is not an atomic directory transaction: inspect a failed
target before removing it or choosing a new path.

## Connection And Sessions

Device selection is `--device`, then `ANDROIDCTL_DEVICE`, workspace config,
the user-wide saved selection, then the sole paired device. Session selection is
`-s/--session`, then `ANDROIDCTL_SESSION`, workspace config, then `default`.
Each field is resolved independently. Invalid selected/configured devices never
fall back. A command fixes its device UUID and requested session across subsequent
RPCs, even when an alias or default changes; selection uses the current call's
existing budget. Keep the UUID and pairing record for routing across a task.

The existing root chosen by `--workspace-root`, `ANDROIDCTL_WORKSPACE_ROOT` or cwd
contains `.androidctl/config.json`; no parent directories are searched. Save local
defaults with `androidctl --device DEVICE_UUID -s task use`. Only explicit device
and session arguments update their saved fields; other saved fields remain
unchanged. An explicit device is resolved to its UUID before saving. Updating only
the session does not require a paired device. Bare `use` reads the local config
and shows its path and saved fields without contacting the daemon. Environment
values affect normal command selection but are not saved by `use`. Use `devices`
to inspect the current device selection and any selection error.
`use --clear` removes the config, including malformed JSON, without selecting a
device. A missing config is allowed; malformed, unreadable or invalid config
reports its absolute path. Saving after replacement with a final synchronization
or cleanup error retains the new config and reports exit 3.

`device select DEVICE` changes the user-wide default; `use` changes only the local
workspace. `device forget DEVICE` does not scan or remove workspace configs.
If a config names a forgotten device, clear it or explicitly select another device
when saving `use`. Commands sharing one workspace read its current defaults.
Workspace defaults do not isolate concurrent agents; use explicit `--device`
and `-s` when other agents may share the directory.
Commands that consume device/session defaults read the config at their request
boundary. Their JSON `data.context` reports `workspaceRoot`, requested `session`,
each field's source (`args`, `env`, `config`, `global`, `sole` or `default`) and the
actual `deviceId` when known. Preflight errors have no unconsumed context.
`devices` reports only device context, with a confirmed UUID when available;
device management preserves identity facts returned by the daemon.
Text shows device or session context only when that field came from an implicit
source; it omits the workspace root.
The requested session is separate from the lease holder returned by `status`.

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
Failed replacement keeps the old active credential, connection and original job facts.
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
A UI or job command keeps
its selected device across its requests, including output-retrieval hints.
Device management requires a positional complete UUID or unique alias:
`device select DEVICE`, `device forget DEVICE`, `device address DEVICE HOST:PORT`
and `device trust DEVICE PATH`. The global `--device` option and `ANDROIDCTL_DEVICE`
do not supply this target. For a broken record, `device forget` also accepts its
exact path reported by `devices`.
`device select DEVICE` returns done when the new user-wide default is saved. If
final file synchronization or cleanup fails, it retains the new selection and reports the
error with exit 3. A failure before replacement is not sent and keeps the previous
selection.
A new registration may create a new pairingId, which cannot take over the old
pairing's jobs. UUIDs can be used directly with `--device`; no prefix
matching is performed. `pair` and `device address` require `HOST:PORT`, with a port
from 1 to 65535; enclose IPv6 hosts in brackets. `device address DEVICE HOST:PORT`
changes the route, not trust. `device forget DEVICE`
works offline, refuses active local work, and removes that local record and retained
local job facts; it does not revoke device credentials or stop remote work.
If unlinking the pairing file fails with an OS error, the result is not sent with
exit 2 and a `localConfiguration` Problem at `local.configuration`; local preparation
may already have closed the connection. A failure before removing the pairing
does not report `forgotten`.
If the pairing was removed but its global selection could not be read or cleared,
the result is failed with exit 1. It retains `forgotten`, `selectionCleared` and
`discardedJobFacts`, with a `data.selectionError` Problem containing `code`,
`message` and any `stage`. A malformed selection file is preserved. Text displays
`Selection: <message>`; JSON retains the complete Problem.
Management text uses lowercase `true`/`false` for boolean fields and does not add
absent fields.

Device revocation rejects new requests. Queries or cancellations already admitted
by the worker may still complete. Revocation confirmation does not confirm that
all old work ended; inspect the original jobs and cleanup facts.
Revocations made while Gateway is off take effect in the worker when Gateway is
next enabled and the worker reconnects.

Certificate renewal keeps the device key and pairings but invalidates old host
pins. With Gateway enabled, use **Renew certificate**, export through a trusted
channel, then `androidctl device trust UUID certificate.pem` on each host.
Export/renewal remain available when expiration prevents listening. Renewal
restarts the listener; if cleanup blocks it, the App reports no renewal. Finish
cleanup, use **Refresh status**, then explicitly renew again.

One device has
one owner; different devices can run concurrently. Opening clears old refs/images.
Use `session open`, `session close` or `session takeover`; inspect state with `status`.
Bare `session` shows command help and exits with 2 without contacting the daemon.
The default idle limit is ten minutes; `session open --idle DURATION` accepts
whole seconds from `1s` to `24h` (for example `10m`); fractional seconds are rejected.
`busy` identifies the current holder. Explicit `session takeover` can take a
session of the same pairing only when no foreground resources remain. Unfinished
UI cleanup also blocks takeover. It is not permission to interrupt another task.
Device-side **Release current session** clears refs but does not kill active work;
the released caller must explicitly reopen before new work.

`connectionBusy` reports another session occupying the connection. Same-session
calls can wait within their original budget. Queries and file work may queue;
queuing does not waive the worker's session permission. `status` separates the
active owner (`session.name` and `session.pairingName`) from the caller state
(`session.callerState`, with values `active`, `released` or `none`). Image qualification
describes the last capture; fresh input guards are checked only at input time.

Successful `status` reports worker, actual session holder, resources and Gateway
state without duplicate checks. A failed observation includes local pairing,
actual authentication (new or reused) and worker checks; later checks stay not
checked after an earlier failure. A valid device `UNKNOWN` remains `UNKNOWN`.
These checks do not test action readiness or prove a credential was revoked when
the device is unreachable.
Connection `stage=local.*` names IPC and `device.*` names device transport, including
connect/TLS/WebSocket/authentication phases; remote errors retain their own stage.
Device `protocolMismatch` reports this CLI's release and requires a matching App.
Use `androidctl apk --out NEW_PATH` or obtain that release's APK; source installs
may have no bundled APK. Rejection does not always include the peer's version.

**Gateway off** stops new requests; stopping/failed may still have live work.
After that work ends, **Refresh status** can confirm completion. **Stop idle executor**
refuses live helpers/transfers; Refresh then attaches a new worker.
Wait for stopping to finish; if it fails, use **Refresh status** after the work ends.
**Force executor exit** is a separate device-local confirmation bound to the shown
worker ID, session and jobs. It can also confirm that the captured worker is already
dead. Completion confirms that worker ended; helper descendants and temporary
files may remain. Gateway/pairings survive; calls can become
UNKNOWN. Refresh does not recover old refs/jobs or replay actions. There is no
remote force command.

Use **Refresh status** to reconnect when the worker is unavailable. Cached
management state may still show the previous worker.

Worker restart loses device `j...`
jobs, refs and images; a surviving daemon can retain local `t...` transfer facts.
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

Install the matching APK from `androidctl apk --out NEW_PATH`. Start Shizuku
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
exists. Do not start a service just to obtain it. The shared lock paths are
`/data/local/tmp/androidctl/worker.lock` and
`/data/local/tmp/androidctl/helpers.lock`. File existence does not prove a held
lock, and deleting a file does not stop its owner. UiAutomation may contend with
other tools using the same platform resource; coordinate known owners before
continuing, rather than deleting locks or assuming automatic takeover.

## Results And Budgets

| Result | Usual exit | Meaning |
| --- | ---: | --- |
| `done` | 0 | The command's stated condition completed, possibly only platform dispatch. |
| `failed` | 1 | A known failure, unmet condition or known cancellation; partial effects can remain. |
| `not sent` | 2 | Requested input was not dispatched; transfer admission also requires no execution/publication effects. |
| `UNKNOWN` | 3 | Dispatch, execution receipt or required verification is uncertain. |
| `still running` | 4 | A shell/transfer job has no final result yet, or `daemon stop` has not confirmed shutdown. Query or cancel the original job ID, or recheck the daemon with `daemon status`, respectively. |

Cleanup failure, uncertainty or signal failure overrides exit to 3 while retaining
the known main result. Known shell cancellation with only ordinary pending cleanup
remains exit 1. Ordinary UI has no general job ID. `dispatchStarted=false` alone
does not imply not sent: a URL with no handler is failed. Admitted wait sampling
expiry is also failed, and read-only results have no dispatch facts.
A rejected platform action may have requested focus already. Late callbacks cannot revise a
terminal UNKNOWN already returned.

Follow the [UNKNOWN checklist](SKILL.md#unknown). For its snapshot-based UI
resource release check, obtain each capture from the corresponding JSON
`data.capture` or XML header's `source.capture`, for both the original UNKNOWN
and the new snapshot. Both must share the worker UUID before the colon.
For `--raw`, preserve its JSON result too; see the
[same-sample diagnostics](#xml-defaults-and-gaps).
The new snapshot must succeed on the same device/session without REF or
`--cached`. Check JSON's explicit `snapshotCached=false`, or its equivalent
omitted `source.snapshotCached` in this version's successful snapshot XML under
the [exact default rules](#xml-defaults-and-gaps). Missing fields in other inputs
do not establish false. A screenshot or unrelated old
capture is insufficient. A partial sample can establish UI resource release,
but cannot prove business absence or completion. Without the original capture,
with a changed worker or after a failed snapshot, the check is inconclusive;
waiting out the budget supplies no missing proof. Reliable business success
permits continuing without this capture check. Read-only wait can be queried again.

Text headings use the same fixed command path as JSON `command`.
Ordinary UI text results occupy one line with available action and verification
facts, full `snapshotPath` / `imagePath`, image dimensions and delivery errors or
warnings. String values use JSON quoting; embedded newlines stay escaped on that
line. Done results omit `dispatchStarted=true` and each fill step's `sent=true`;
explicit false and dispatch facts on non-done results remain visible. Missing
dispatch facts on non-done results show unknown.
A different existing ref or selected action expands Requested, Resolved, Selected
and Verification target lines;
refreshed names/bounds and ordinary selector resolution keep one line.
Observed change uses `changed` / `semantic observation unchanged`, or
`change=unknown` with its available reason; JSON retains comparison metadata.
Missing facts are not inferred. `--print` then echoes the saved body. App lists and
raw shell output retain their own lines.

### JSON Field Guide

Fields depend on the command and outcome; absence does not mean false.
Top-level `command` is the fixed public command path, such as `fill`, `key` or
`job get`, including on failure and pre-dispatch rejection. Targets, keys and flags
do not become part of that field. Argument errors preserve an identified command
path; without one they use `arguments`. Nested action and job facts keep their
original meanings.

| Location | Interpretation |
| --- | --- |
| `command`, `result`, optional `reason`, `error`, `exit` | Top-level outcome; `error` supplies code/message and optional stage. An explicit `exit` overrides the usual result exit code. |
| `data.snapshotPath`, `imagePath` | Absolute paths to the saved observation and PNG. [Delivery](#images-and-delivery) also reports `snapshotError`, `imageError`, `historyError` and `deliveryError` when applicable. |
| `data.focus`, `fill`, `submit` | Fill reports separate focus/fill results and submit when requested. Failures before step results are recorded can omit them; text then shows `step results=unknown`. Fill and submit steps can contain `data.verification`, `actualValue`, `verificationError` and `verificationTarget`. Submit fields describe its pre-submit exact-value gate and do not overwrite fill facts. [Targets](#observations-and-targets) |
| `data.requested`, `resolved` | Original target/action and the resolved target. `resolved.method` is original for a published ref, selector for selector discovery, or signature for same-request recovery; resolution does not prove admission or dispatch. |
| `data.selected` | The single action's selected target/action. Selection alone does not prove dispatch. Global actions use target=null; key REF names its focus-admission target. Each fill step keeps its known selected and sent facts in its data. |
| `data.focus.data.sent`, `fill.data.sent`, `submit.data.sent` | Whether that fill step entered dispatch. A later step that was not sent does not negate earlier dispatch. |
| `data.dispatchStarted` | Whether any step entered dispatch. Missing device replies retain only known request facts and do not imply false dispatch. |
| `data.verificationTarget`, `verification` | The object checked by a value operation, and matches/differs/unobservable. Combined steps keep their own fields; naming an object does not prove successful verification. |
| `data.platform` | For semantic input receipts, `accepted` is the synchronous platform call's return value, `callback` is the platform action-completion callback value (null when unavailable), and `acceptedError` is an exception while obtaining acceptance. A positive receipt does not prove App business completion. |
| `data.segments[]`, `cancel` | Coordinate input has no `data.platform`. Each event reports `returned` or `error` in `segments[]`; after UNKNOWN, the CANCEL attempt reports `accepted` or `error` in `cancel`. |
| `data.job`, `stopRequested` | Original job ID; `stopRequested` records a transfer cancellation request, which does not prove completion. [Jobs](#jobs-and-files) |
| `data.helperAlive`, `cleanup`, `cleanupError`, `signalErrors` | Shell helper and cleanup facts, independent of execution outcome. A helper can have ended with cleanup still unknown. [Jobs](#jobs-and-files) |
| `data.resourcesActive`, `cleanupError` | Transfer resources and cleanup for the reported side; host completion does not confirm device completion. [Jobs](#jobs-and-files) |
| `data.queryError`, `outputError` | Failure to query or export a job, separate from its execution result; supplies code/message and optional stage/diagnosticTruncated. [Jobs](#jobs-and-files) |
| `data.outputBytes` | Present in `shell`, `job get` and `job cancel` JSON when this call retrieved retained output; maps stream names to base64-encoded original bytes. [Jobs](#jobs-and-files) |
| `data.path`, `sha256`, `version`, `files` | Local resource paths, hashes and package version. [Package files](#local-package-files) |

`resolved.target` and each non-null `selected.target` contain
`{ref, role, name, windowId, bounds}`. Names keep the first 256 UTF-16 units,
then append `... (N chars omitted)` when longer. Read the original observation
for other node facts.

Durations use nonnegative integers with `ms`, `s`, `m` or `h`; only `0` may
omit its unit. Each option retains its own range. Use `--run` for shell/transfer
execution limits and `--wait` for shell/transfer or `job get` waiting.

| Budget | Default / limit |
| --- | --- |
| Ordinary UI | `5s`; explicit `--timeout` accepts `1ms`–`60s`. |
| Standalone screenshot | `15s`; explicit timeout takes precedence. |
| Action settling | `--settle` accepts `0`–`60s`, default cap `1500ms`; check/uncheck verification keeps the request budget. [Fill rules](#observations-and-targets) |
| Ordinary shell execution / initial wait | 10000 ms each. |
| `shell --background` execution / initial wait | 600000 ms / 0 ms. |
| Transfer execution / initial wait | 600000 ms / 10000 ms. |
| Shell or transfer execution | `--run` accepts `1ms`–`10m`. |
| Shell/transfer and `job get` CLI wait | `--wait` accepts `0`–`60s`; expiry does not cancel execution. `job get` defaults to `0`. |

Connection, queueing, dispatch, callback, verification, observation and attachment
delivery consume one remaining request budget, without restarting it at each step.
An action's screenshot does not receive an extra 15 seconds. Requests return when
complete. This budget is not a hard wall-clock CLI limit: local validation,
decoding and file saving continue after complete attachments arrive, without a
separate clock cutoff. Resources remain held until actual cleanup ends.
Network requests and waiting for an occupied history lock keep the original
deadline.

The device checks the request's cutoff before entering the worker. This does not
cancel an already started shell or file job; a later cancel request has its own
budget.

Measured JSON fields are `localConnectMs`, `deviceConnectMs`, `uiConnectMs`,
`preWaitMs`, `settleMs`, `deliveryMs`. Unmeasured/reused connections omit their
fields; cached reads do not replay old timing. `deliveryMs` covers fetching and
local processing through file saving, before output encoding. Stages can overlap;
they exclude subsequent reading/model work and cannot be added as total task time.

## Observations And Targets

A published observation is the capture available for delivery in the device's
current session. Publication does not mean the client has received or downloaded
it; completeness and collection policy remain separate facts.

`snapshot` without REF or `--cached` collects a new sample, including when scope,
boxes or global `--json` are specified. `snapshot --cached [REF]`, `snapshot REF`
and `find` reuse the current sample.
They save a new file, retaining sampling identity/sequence/age. Here, visibility
means the Android accessibility report. A node reported visible may have no
visible pixels, and one reported invisible may still be visible in the screen
image. `--all` includes nodes reported invisible, marked `offscreen="true"`;
the label does not establish location or occlusion. It changes the XML
projection, not collection. `snapshot/find --raw` saves the device's collected,
processed tree as JSON instead of projected XML. It changes representation,
not permissions or completeness. Global `--json` independently
selects Result JSON on stdout; it does not change the saved body.

| File | Contents |
| --- | --- |
| XML | Projected screen/window/control tags; `<?androidctl ...?>` holds invocation, source, snapshotFacts and optional image metadata. |
| `snapshot/find --raw` | The entire current device-processed tree, including on REF/find; match counts stay in the result. Works with text or JSON stdout. |

Invocation metadata keeps non-default query/all/boxes and the existing renderRef,
but omits fill argument text; visible screen values still appear.
`source` preserves capture and age, with cache and before-action flags subject to
the defaults below. `snapshotFacts` retains connection, sequence, sampling times,
scope and unavailable fields, with non-body gaps summarized below. The complete
window inventory is in the XML body. JSON stdout retains observation and
completeness summaries plus successful file paths; the original gap list remains
in raw. Text stdout gives result and paths,
with `observationWarning` for partial sampling or completeness gaps.
`timing=before-action` remains meaningful even if saving fails.
Reported unavailable fields remain in raw JSON. An empty `actions=""` means no
exposed actions.

Raw `checked` uses `"false"`, `"true"` or `"mixed"`; check/uncheck verification
reports the same strings in `actualValue` when readable. Unknown raw checked
integers remain decimal strings.
Raw `checkedModel` identifies the node's binary or tri-state platform reading;
XML omits the checkable node's reported `triState` default below.
Window `type` uses application, inputMethod, system, accessibilityOverlay,
splitScreenDivider or magnificationOverlay, with unknown integers as decimal strings.

### XML Defaults And Gaps

XML omits only the following explicitly reported exact defaults and duplicate
facts. Missing input fields are not filled in; nulls, errors and non-default
facts remain. Raw JSON retains its existing facts.

| Location | XML rule |
| --- | --- |
| Invocation `args` | Omit `timeoutMs=5000`, `settleMs=1500`, `screenshot=false` and `scope={}`. Preserve query/renderRef/all/boxes/cached and non-default requests. Do not add an absent args object. |
| `source` | Omit reported `snapshotCached=false` and `snapshotBeforeAction=false`. Preserve capture and age. A successful snapshot in this version reports both flags before XML rendering. |
| `snapshotFacts` | Omit the windows copy, keyboard when equal to screen.keyboard, `selectionScope={}`, `partial=false` and empty missing/nodeUnavailable lists. Preserve startedMs/finishedMs, sequence, connection, scope and other facts. |
| `snapshotFacts.display` | Omit `interactive=true`, `deepIdle=false`, `userId=0`, `displayId=0`, `displayState=2` and `keyguardShowing/keyguardSecure/deviceLocked=false`. With displayError, retain displayId/displayState. Omit sampleMs only when it equals an actually reported, non-null finishedMs. Keep width/height/rotation and errors. |
| Controls and windows | Omit a checkable node's reported `checkedModel=triState`; keep binary and other reported models. Omit window `active=false` and `focused=false`. Keep rootAvailable true/false; its absence means unreported, not true. Actions, including empty editable actions, and named coordinates keep their existing meaning. |
| Collections | Render all reported non-null integer dimensions, indexes, spans and selectionMode, including negative values. rows/columns=-1 are unknown platform counts; other negative values are preserved as reported. Omit `selectionMode=0` only with rows or columns, and `rowSpan/columnSpan=1` only with rowIndex or columnIndex on that element. Keep zero, multi-cell and negative spans; absent collection fields add no defaults. These rules use fields, including on textbox/button, rather than list/item tags. |
| `image.shape` | Use the image's own shape and time. Omit reported displayId/userId=0, interactive=true and keyguardShowing/keyguardSecure/deviceLocked=false. Keep dimensions, rotation and complete top-level magnification, including mode/activated/scale. Shape windows omit active/focused=false; omit their transformation only when the complete numeric object is matrix=[1,0,0,0,1,0,0,0,1] and magnification=[1,0,0]. Keep non-unit or incomplete transformations, shape=null and errors. mappingKnown=false can still carry a valid after shape. |
| Selected title | Keep screen.title. After XML character cleaning and titleIncomplete checks, omit only the nonempty duplicate window title whose id equals scope.window. Keep explicit empty window titles, both titleIncomplete flags, other window titles, packageName and rootAvailable. Null or absent window titles remain absent. Illegal XML characters use the existing replacement; the header no longer duplicates their original window values. |

The original capture's missing entries whose refs appear in the final XML body
remain in `snapshotFacts.missing`, in original order, omitting only reported
unreadChildren/unavailableChildren=0. All other entries appear only in
`missingSummary`: `entries` is their count; optional unreadChildren and
unavailableChildren sum only reported counts and are omitted when the sum is
zero; optional `reasons` counts each reported reason. An absent count does not
prove zero missing children, and these sums are not a total of all missing
descendants. Empty missing or missingSummary are omitted.

JSON `snapshotCompleteness.missingCount` remains the original entry count:
the retained missing length plus missingSummary.entries. REF/find change which
refs remain individual entries; the summary still covers the whole capture.
A parent find match retains its subtree and match counts keep their existing
meaning. `--all` may expose more refs, but ref-unavailable and ref-less entries
can still be summarized. It does not restore the original missing list.
Missing/empty missing alone does not establish completeness. Even partial=false
does not guarantee visual text, provider data or business content is complete.

To locate summarized gaps or diagnose image/tree differences, preserve the same
sample before another fresh snapshot or action replaces the cache. On the same
device/session, run:

```sh
androidctl --device DEVICE -s SESSION --json snapshot --cached --raw
```

Save the JSON result. Check its `data.capture` against the original XML's
`source.capture`, and the raw tree's sequence against XML `snapshotFacts.sequence`.
Read raw missing and the relevant nodes/ancestors with host file tools. Keep the
existing PNG; a later image cannot establish the old visual state. Raw has no XML
source header, so the raw file alone cannot complete this capture check. Once the
cache has changed, fresh raw cannot recover an old XML's summarized distribution.

Fresh partial samples replace previous ones. Reliable local refs and value facts
remain usable; gaps cannot prove selector uniqueness, whole-page stability or
absence. When repeated source expansion is stopped, the observation records the
gap and provides no usable refs because uniqueness can no longer be established.
`observedChange` compares the existing observation signatures of complete before/final
endpoints in the same connection and actual scope. Missing or partial endpoints
report unknown. Unchanged endpoints do not prove that no effects occurred between them. If an explicitly
selected window was present before dispatch and is absent from the same
connection's current default-display inventory, `absentWindow` identifies it and
observation falls back to the default active scope. If that capture fails, the
absence fact and error remain, and the old tree and attachments are unavailable.
This does not prove the window
closed or was destroyed. It neither repeats input nor redirects verification/IME.

Refs resolve in the latest collected tree while node identity remains. For explicit
REF actions, the App retains the target's current collected facts at action entry,
before fresh action admission. Every successful fresh tree collection, including
a partial sample, replaces the device's current facts even when the body omits
that ref or delivery fails. These facts can differ from the last body received
by the caller. Reading a node for admission does not itself replace the collected
tree. `snapshot --cached`, `snapshot REF` and `find` do not collect new facts.
If the ref is absent or unusable at entry, the action rejects without repair.
If the entire first attempt is definitely not dispatched and rejects with staleRef,
selectionChanged or identityUnknown, the App may make one repair attempt within
the same request budget. Repair requires retained entry facts and a complete new
observation, then selects a clear best match using name/ID and supporting facts.
Conflicting nonempty names or row labels reject. This is a bounded selection aid,
not a guarantee of business identity. UNKNOWN or any known dispatch never triggers
repair. On a failed repair, error/reason can describe the repair failure while
resolved/selected/verificationTarget still describe the first unsent attempt;
these facts do not establish dispatch.
`ref-unavailable` nodes have no usable ref. Filters (`--role`, `--name`, `--text`, `--id`) combine on one original
node, with exact values and one XML-entity decode. Numeric entities decode only
legal XML characters; literal candidates remain available. IDs accept full names or the
suffix after `:id/`. A label maps to a parent only if it supplies that parent's
name. Disabled/unavailable matches still count toward ambiguity; known invisible
nodes do not. An earlier `--window` snapshot does not authorize a later action:
repeat the matching explicit window for non-active targets.

REF actions and selectors after discovery recheck the target's full identity
and readiness through its ancestors. Admission does not repeat the whole-page
uniqueness check. `observedChange` compares the published observation with the
observation returned after the action.

`find` counts matching projected subtrees, not unique actionable targets.
Read the original observation when row or ancestor context is needed.

Actions wait for their target, with the current scroll-direction exception below,
then dispatch once and normally return an observation.
`locate` waits for the target's direct action readiness; check/uncheck can also use
a qualified suggested row.
A `notReady` reason explains the blocking condition checked for the target, or
distinguishes no selector match from incomplete uniqueness. Before any completed
check, it remains neutral.

`scroll` accepts `forward`, `backward`, `up`, `down`, `left` or `right`; omitted
direction defaults to `forward`.

Scroll selects the advertised requested action first, then the corresponding
`page-up`, `page-down`, `page-left` or `page-right` action. If no physical direction
or PAGE action is advertised, supported scrolling containers may map physical
directions to advertised backward/forward. Forward/backward keep their provider
meaning. The chosen action is reported in `selected.action`;
compare it with `requested.action` to see a substitution. Dispatch does not try
another route afterward.
The observation's `scroll` attribute retains the actual `page-*` names.
After the original identity and window are confirmed fresh, a visible, enabled
scroll target with none of the routes above for the requested direction immediately returns
`notReady`; it does not wait for that direction to appear later in the request.
This applies to REF actions and selectors after discovery. Observe again for
current directions, or use a selector with `locate --action scroll-<dir>` to wait
explicitly, then act on the ref returned at that time.

`expand`, `collapse` and `dismiss` request the corresponding exposed platform
action. Their names appear in `actions` and make the node a target, including when
it is also clickable. Use the same name with `locate --action`; `done` confirms
the platform receipt, so read the resulting observation to verify the App effect.
These actions do not fall back to coordinates or expose custom actions.
Noneditable, long-clickable nodes show `long-click` only when not clickable;
editable nodes retain their full reported `actions` field.
System actions without a before observation can stop sampling after two
consecutive complete, identical, non-loading samples. At that stop, `settle` is
`unsettled` and `observedChange` is `unknown`.
`samplingStopped` means sampling stopped before the observation deadline because
another sample was not expected to fit; the delivered fact is not a later recheck.

Fill/check/uncheck require the original ref and full identity before dispatch.
Check/uncheck also retain the verified control's ref and identity. After an ordinary
fill is dispatched, verification permits a name change on the same unique source while
window, role, full row identity and identity completeness must still match, and
the original field must remain reported visible in the post-write sample; if
reported invisible, ordinary fill is UNKNOWN/unobservable.
This write-time exception does not revive expired refs for later requests.
Losing the required identity ends UNKNOWN/unobservable even if another target
shows the wanted value. Temporary collection failure can use the remaining
ordinary observation window for fill; check/uncheck verification can use the
remaining request budget. Fill can verify a retained successful post-write sample
even when a later collection fails: matches/done or differs/failed can coexist
with the later top-level `snapshotError`.

Dispatched check/uncheck verification reads the verification target's retained ref in each
sample until that identity is lost. Its `actualValue`, `checkedModel` and
`stateDescription`, when reported, belong to the sample used for verification.
A matching no-op still uses the admission fresh read. Later collection failure
can make verification unobservable and remove an earlier actualValue.
If identity loss leaves a `rootUnavailable` tree in the default scope and ordinary
settle and request time remain, one final observation is attempted. The original
UNKNOWN and `verificationTarget` stay fixed; the delivered tree can show a later
window.

Already matching readable values require no input, even on disabled controls.
Password masks prove neither equality nor a no-op; affirmative writes can be done
with unobservable verification. Preserve the App's checked model/state description;
platform binary values do not imply a business tri-state. If the control has no
direct click action, check/uncheck can click its qualified collection row once and
verify the row's unique checkable. A row label can be the requested target; resolved
keeps that target, selected identifies the row click, and verificationTarget names
the control. The row and control need usable refs and the row membership must be
complete. A matching fresh control sends no input. The click may open details
without changing the value; failed/UNKNOWN cannot undo that effect, and no second
click or replacement target is attempted. An unchecked radio already
meets uncheck; change a selected radio by selecting another option.
For a complete unique target known to be a radio, `locate --action uncheck`
rejects selected, mixed or unknown checked state before waiting for readiness.
It reports `radioUncheck`/`not sent`/exit 2; locate does not make a later action atomic.

`fill` reports separate focus/fill results and submit when requested. Failures
before step results are recorded can omit them; text then shows `step results=unknown`.
An exact readable match without submit completes both steps without input.
Otherwise fill obtains focus when needed, then checks the original identity and
current value again before writing. Focus does not authorize rebinding to a
replacement. If focus was dispatched, later rejection cannot trigger a whole-command
repair/retry. An already-focused no-op is still not dispatched.

After an accepted ordinary non-password write, fill completes the finite ordinary
observation, then determines final verification from the last successful post-write
sample.
With a complete pre-action observation, ordinary observation can end early after
two consecutive complete samples share a signature that differs from the baseline
and the last sample is non-loading. Ordinary fill defers that early stop while
the original field has a readable differing value, within the same settle cap
and sampling budget. A larger `--settle` can allow a later exact value; stable
differences can take longer to report.
A differing final value is failed/exit 1 even if the App reaches the requested
value later. The required first post-write sample can exceed the settle cap
within the remaining request budget. `--settle 0` only attempts that first sample
when the remaining request budget permits; that sample may still differ.
Without a usable sample, fill is UNKNOWN/exit 3. No-op and password writes have no
required first-sample rule.

With `--submit`, an ordinary value must match or a password write must have a
positive receipt before submit preparation. After an ordinary dispatched fill
matches, submit checks its confirmed post-fill identity; already-matching and
password fields keep the original full-identity requirement. A business
reassignment expressed only by the field name can pass the write-time identity
check. After fresh original-identity and focus admission, a non-password field
must still be an exact match. A differs/unobservable value leaves submit not sent,
with its verification facts and no `error`, so it does not trigger ref repair.
Any earlier dispatch, including focus alone, makes the overall result failed/exit 1;
with no step dispatched it is not sent/exit 2.
Submit prefers the original editable, focused field's advertised IME action.
Only complete actions explicitly lacking IME and `isMultiLine=false` allow one
Enter down/up. Multiline or unknown line mode does not allow Enter, and an IME
attempt never falls through to Enter. No other field or button is selected.
Standalone `ime REF` requires the original field already focused and
requests its exposed IME action without an automatic focus step. A multiline IME
action may insert a newline; one Enter can cause two App submissions or none. Verify
business effects independently of the platform receipt.

Read each step's selected/sent and the fill and submit verification/actualValue. A later
not-sent step does not negate earlier dispatch. If submit was not sent, choose
recovery from the current value and reason rather than defaulting to refill or a
longer timeout. The command's dispatchStarted remains the authority for any input.
On normal completion, if a fill observation exists and submit was not sent,
the command preserves that tree, settle and observedChange. If submit's exact
value gate ran, its value comes from the later fresh read and need not appear in
that tree; fill keeps its earlier value. Other dispatched paths perform ordinary
observation, including after an actually dispatched submit; fill retains its
earlier facts. With no dispatch, the command keeps the existing tree.
Once fill has dispatched input and its overall result is recorded, a later
post-processing error preserves that result and its step facts. `actualValue` is the accessible text used
for exact comparison, with hint text treated as empty. It can include App formatting
or labels and does not expose the App's private model or saved value. Fill does not
strip labels, normalize formatting or automatically refill.
`key enter|tab|del|escape [REF]` requires a supplied ref already focused in the
input-focus window. Without a ref it is global and rejects scope. Platform input
and animation waits can exceed the budget, and effects can arrive after UNKNOWN;
they do not revise that result or authorize repetition. Key completion does not
verify animation completion or the App's business result.

`wait` accepts exactly one of `--contains`, `--app`, `--gone`, `--idle` or
`--screen-change`.
The positive text condition uses visible
text/descriptions/state descriptions, excluding hints and unknown fields, with
NFKC/whitespace/case/hyphen normalization also used by `find`. A reliable partial
match can succeed with that sample attached; only failed collection can attach
explicitly cached facts. An admitted wait whose sampling budget expires is failed,
even without a first observation.

`--gone` requires at least one exact selector field (`--role`, `--name`, `--id`,
`--text`). It succeeds only when a complete tree has zero true or unknown selector
matches; invisible nodes do not match. A missing explicit window is a collection
failure, not an empty tree. Selector fields are accepted only with `--gone`;
`--contains` is a normalized substring condition, while selector `--text` is exact.

`--idle` requires two consecutive complete trees with the same signature and no
recognized loading indicator. A partial tree, collection failure or loading sample
breaks that sequence. This describes tree stability; silent work or Canvas animation
may continue. `data.predicate` retains the last sampled judgment on expiry: gone
reports matches/unknown/complete, and idle reports sequence/complete/loading/stable.

`--screen-change` compares complete new samples with the same session's current
complete published capture. Downloading the capture is not required. Missing or
partial baselines and a different collection policy are rejected as not sent before
sampling; after reopening a session, take a new snapshot first. Collection policy
comes from the original sampling input: default active scope can cross Activity or
app, `--in PACKAGE` permits another window of that package, and `--window ID` fixes
the window. Use the same policy for the baseline and wait. An admitted wait that
only sees partial or unchanged samples, or collection failures, ends failed.
Its predicate keeps `baseline=published`, `baselineCapture`, `baselineSequence`,
`selectionScope`, the baseline's actual `scope` and the last sample's `sequence`,
`complete` and `currentScope`.
Without a new sample, only the admitted baseline facts are known. A changed tree
can precede business readiness; verify the result needed for the next action.

`apps [QUERY]` filters launchable names/packages by case-insensitive substring.
`open PACKAGE` or `open --url URL` navigates. Notifications reports dispatch,
recents toggles, and wake checks interactive state without unlocking credentials.
Already interactive wake sends no input. Without a current pre-action observation,
system actions do not infer changed/unchanged from an old cached page.

## Images And Delivery

Observation bodies, including action/rejection and cached/find/REF results,
default to files under the artifact root's `.androidctl/snapshots/`.
The root is the global `--workspace-root PATH`, then `ANDROIDCTL_WORKSPACE_ROOT`,
then the CLI working directory. It must be an existing directory; AndroidCtl does
not search parent directories. This root does not change user-level credentials
or create a separate daemon. All reported host file paths are absolute, including
`job output`, `apk` and `skill` exports.
`snapshot/find --out PATH` writes the body only to that new file in an existing directory;
relative explicit paths resolve against the CLI cwd. Automatic PNGs use
`.androidctl/shots/`. Matching filename stems identify a delivery, not an atomic
tree/image frame. Fresh `snapshot --screenshot` adds a current PNG.
`find`, `snapshot --cached` and `snapshot REF` cannot request images; cached bodies
keep their original sample identity and age. JSON stdout's image is a summary;
XML metadata retains delivered image facts under the
[shape default rules](#xml-defaults-and-gaps).
Text output includes `imagePath="..."` and `imageSize=W x H px` on the result line;
JSON uses `data.imagePath` and `data.image.width` / `data.image.height`.
Complete files contain all selected collected content, not necessarily a complete
page. Check missing/partial facts and any host-tool truncation separately.

UI observation and action commands accept `--print` to echo the saved body after
the result and paths.
It cannot be combined with global `--json`. The body is generated once and echoed
only after saving succeeds; print is neither a body-only stdout format nor a
fallback for a failed save. `--out` is available only on snapshot/find; actions,
wait, locate and screenshot still save their bodies automatically.

For a cache-path or permission error, preserve the existing object and choose
an existing `--workspace-root` where AndroidCtl can create a new cache. To repair
the reported path on Windows, use **Properties → Security → Advanced** and the
reported permission error. On POSIX, `chmod 700` fixes access bits only when the
reported owner is correct. Fix delivery before obtaining the missing observation;
do not repeat a known or UNKNOWN action to repair its output.

New observations save bodies and requested PNGs. A received PNG can be saved
even when tree delivery fails. AndroidCtl does not create or read workspace
`.source` files for recovery.

Automatic bodies, PNGs and legacy source files share a 150-file/20-MiB quota.
Later saves can reclaim old files matching AndroidCtl's complete filename
format, including edited or replaced files. Matching stems are reclaimed together
and current file sizes count toward the quota. Copy files elsewhere for lasting retention;
nonmatching files are left alone and failed deletions still occupy quota.
This is not a physical directory-size guarantee. Explicit outputs outside these
cache directories are not rotated. An explicit output inside them with a matching
filename is reclaimable too. `--image-out` requires a new file and an existing
directory and implies `--screenshot`. Giving both options requests one image,
without repeating the main action. Invalid output paths and print/json conflicts
reject before the action RPC. With an explicit body output, automatic PNGs still
use the shared history quota; the body is not duplicated in history.

`snapshot`/`find` require a saved body, and `screenshot` requires a saved PNG;
missing that artifact fails an otherwise successful observation. An attachment or
stdout failure preserves a known action result. Read the reported errors and
saved files, then obtain only the missing observation; do not resend the action.
Actual write or close failures are reported and removal of that write's file is
attempted.

Read actual saved content before relying on it; expand the same file when the
host's display is truncated. If a decision depends on a new PNG, wait for the image
tool's result to reach the model before choosing the action. Independent operations
may be batched. Automatic files can contain private screen values.

Coordinates require API 34+ and the session's latest screenshot with verified
coordinate mapping (`data.image.mappingKnown=true`). `--image ID` requires that
ID to match the current image; it does not select an older image. A mismatch or
image older than 120 seconds is rejected as `staleImage`. Read `data.image.id`
from JSON output when using the guard. Text output reports the path and dimensions.

Input checks the current default display, window, lock and magnification state,
and normally requires an unlocked screen. An explicit `swipe` may pass a non-secure
swipe lock when keyguard is showing, keyguard is not secure and the device is not
credential-locked; `tap` cannot bypass that lock. Input, observed changes or uncertain
mapping require a new screenshot. A saved PNG is not a fresh input guard. Use
original pixels or convert the preview's actual scale. After disabling
magnification, finish active work before closing/reopening the session and taking
a fresh screenshot when needed.

Semantic click requests an App action, not a finger touch. XML `covered` is an
overlap hint. Separately, dispatch outside the active task window is rejected
with error code `covered` unless the action repeats that window's explicit
`--window` scope. An empty WebView may need an image. Swipes keep scheduled points
while the session/budget permits; slow platform injection can alter timing.
Attempted touch failure tries one CANCEL and remains UNKNOWN, because cancellation
does not erase earlier effects. Equal endpoints and `--duration` provide long press.
Swipe duration accepts `50ms`–`5s` and defaults to `400ms`.

## Jobs And Files

Shell receives one complete non-interactive script, from either SCRIPT after `--`
or `--script-file PATH`, never both. The file reader checks valid UTF-8, no NUL and
at most 64 KiB before any RPC. Any CRLF in either source rejects locally as not
sent/2 with an LF conversion hint. BOM and standalone CR are sent unchanged;
no input conversion occurs. Prefer UTF-8 without BOM and LF line endings.
The local CLI-to-daemon message is limited to 256 KiB. The complete encoded JSON
request sent to the device also has a 96-KiB budget. It may reject a file within
64 KiB as not sent/2 after daemon connection, device selection or device connection,
but before business dispatch. The device keeps its existing script limits for argv input.
If the encoded request is too large, shorten the script or push it to a task-owned
device path and run it there after the transfer succeeds.

Both modes supervise descendants, including `&`, `nohup` and `setsid`; work
delegated to apps or system services is outside that supervision. Background
execution releases only this shell's session foreground resource after permission
to execute. The device's four helper slots are separate: releasing this foreground
resource does not release a live helper's capacity, which remains occupied until
that helper exits.
Same-session read-only snapshots remain available during ordinary shell. No
screen observation is attached to shell output.

For a completed script, a nonzero exit or signal termination gives `failed` and
CLI exit 1. Nonzero exits have reason `exit=N` and preserve N in
`data.execution.exitCode`; signal termination has reason `signal=N` and preserves
N in `data.execution.signal`. `exitCode` supplies the normal exit status only after
the root process has been reaped with a normal exit; otherwise it is -1, including
signal termination, an unconfirmed root termination or a root that was never
created. `signal` supplies a signal number only after confirmed signal termination.
The value -1 is neither a CLI exit code nor by itself an UNKNOWN result; later
helper cleanup reports do not replace these execution values. Cleanup uncertainty
or signal failure can make the CLI exit 3; inspect execution and cleanup separately.

Cancellation attempts TERM then KILL. Cleanup can finish after the first result.
Query the original shell job and read `helperAlive`, `cleanup`, `cleanupError`
and `signalErrors`. `helperAlive=false` does not resolve `cleanup=unknown`;
processes may still change data, and cleanup uncertainty can produce exit 3 while
retaining the known execution result. The CLI can return a known result with
available output while helper cleanup is still pending.

Shell output retains at most 128 KiB per stream, up to eight cached
outputs for 120 seconds. Displayed/exported size and SHA-256 cover retained bytes,
not discarded data. Preview controls are escaped; `job output` exports original
bytes. Truncation, binary data and missing final newlines are identified. Output
expiry does not change execution results; an export result is separate from them.
`data.queryError` and `data.outputError` use the Problem shape: `code`, `message`,
and optional `stage`/`diagnosticTruncated`. Text shows only the short message.
They preserve known execution results and cleanup exits. An export's own `error`
describes querying, reading or saving, never the original script failure.
Before the query supplies original job facts, a definite rejection returns
`not sent`/2; a query that may have been dispatched without a trusted result
returns `UNKNOWN`/3. Once the original job is known, unavailable output or a
metadata, read or save failure returns `failed`/1. Successful export returns
`done`/0 even if the original script failed or is UNKNOWN; its execution and
cleanup exit do not determine the export's exit.
Existing specific error codes are preserved; other local output failures use
code `output`. In `shell`, `job get` and `job cancel` JSON, `data.outputBytes` maps
stream names to base64-encoded retained original bytes retrieved by this call.
`job output` writes the `--out` file and returns its successful path in `reason`,
without `outputBytes`. It requires a new file with an existing parent directory;
invalid paths reject before querying.

Push/pull transfer one ordinary file in 32-KiB chunks, so route round-trip latency
can limit throughput. Publication refuses overwrite unless `--replace` is explicit.
The source must end at its declared size. Extra bytes fail with `sourceChanged`
before publication, including files that report zero size while exposing content.
Unsupported publication primitives report failed/publishUnsupported; choose a
supported destination or explicitly allow replacement. See
[publication boundaries](#support-and-storage) before relying on no-overwrite.

On the device, transfer permission precedes opening files; the host may already
hold a source or temporary destination. A begun transfer may finish after
session release by its original ID; queued work may not begin after release.
Without active I/O, a device transfer expires after about 120 seconds without an
owner query/step; blocked kernel I/O is not forcibly interrupted. Local `t...` queries
return host facts for the original pairing/session without contacting the device.
Cancelling an active transfer records `stopRequested`, then the transfer attempts
device cleanup with its own budget. Cleanup failures appear in `cleanupError`.
Unexpected host open/copy/finish defects retain the original job ID and publication
facts. Confirmed publication remains done with internal diagnostics and exit 3;
uncertain publication remains UNKNOWN, and known unpublished work fails.
Each cleanup step records its failure and continues the other cleanup steps.
If a local close cannot be confirmed, the terminal result and cleanupError/exit 3
remain, resourcesActive stays true and the destination stays occupied until this
daemon exits. This also rejects device forget as busy. Job cancel does not retry
cleanup; with other work finished, daemon stop can end the host process. Stopping
loses in-memory job facts and does not confirm remote cleanup or permit replay.

Not sent requires no data transferred, no device object created, an unchanged
target, and a removed or absent host temporary file. A lost begin reply is failed and can leave
temporary device work; it is not resent. A finish known unsent is failed. A lost
finish or a device UNKNOWN preserves its original reason and Problem. A subsequent
query returning not sent or UNKNOWN without the original transfer ID adds that it
did not confirm finish; it does not establish the device's publication outcome.
Push remains UNKNOWN unless an existing cleanup receipt confirms the original ID,
size and hash, without repeating publication.
Its reason then states `Original push confirmed during cleanup`. Before host pull
publication, UNKNOWN becomes failed and adds `target not published` to the original
reason, or uses that fact alone when no reason was supplied. A device publication
preparation failure after opening the temporary file is failed; failure after
publication starts can be UNKNOWN. Cleanup uncertainty can still override the exit
code of a known done result.

`stopRequested` is not completion. `resourcesActive` stays true until local I/O and
cleanup end; `cleanupError` can identify failed or unconfirmed device cleanup.
Device cleanup that was not confirmed remains reported with the original job ID;
UNKNOWN or not sent alone does not mean resources ended. Local completion does
not imply remote completion. There are no structured install/uninstall/recovery
commands.

### Shell Without An Initial Job ID

For a shell UNKNOWN with no initial job ID, inspect `status` on the original
device/session: `jobs=` is JSON `data.helpers.active`. It lists unfinished jobs,
which may already be cleaning up. A sole ID is not proof of association. Recover
that ID only with independent facts establishing the same worker, no prior jobs
or outstanding starts, and that only this request could have created it. These
are sufficient evidence examples, not a required preflight for every shell call.
Multiple candidates, an already-ended job or uncertain association leaves UNKNOWN;
do not resend the script. Querying an identified job does not rewrite the lost reply.

## Support And Storage

Targets are Python 3.12+ on Windows/macOS/Linux, Android API 30+ on arm64-v8a/x86_64,
shell-UID Shizuku, Android user 0 and default display. Root Shizuku, 32-bit userspace,
other users/work profiles and secondary displays are unsupported. Local/network
filesystems must provide the required publication operations. Windows host state
storage must support replacing open records.

Publication checks ordinary concurrent destination conflicts and reports the
object's hash at publication time, not after later edits. Android shared-storage
concurrent creation of the same destination is excluded from no-overwrite.
Parent directories follow ordinary access rules; temporary files must remain
private. Active interference with temporary objects by the same user, privileged
processes, directory-authorized principals, shared-storage Apps or an app-specific
directory's owner is outside the guarantee.

Credentials are private to the current OS user, under `%LOCALAPPDATA%/AndroidCtl`
on Windows or `~/.local/state/androidctl` on macOS/Linux. Sessions, refs, images and job
facts are in memory; there is no durable operation history. Preserve pairing and
recovery materials when outcomes are uncertain, and preserve verified deliverables
outside automatic history reclamation when needed.
