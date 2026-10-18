---
name: androidctl
description: Use when operating or inspecting apps on an Android phone, tablet, or emulator through the AndroidCtl CLI, including UI actions, screenshots, device shell scripts, and file transfers.
---

# AndroidCtl

Use the installed `androidctl` CLI. The user prepares shell-UID Shizuku, enables
Gateway and supplies a reachable address and current pairing code. Host and App
versions must match. `androidctl apk --out NEW_PATH` extracts the matching APK;
the user installs it. Export these instructions and their [reference](reference.md)
with `androidctl skill --out NEW_DIRECTORY`. Follow the [upgrade guidance](reference.md#connection-and-sessions)
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
androidctl --device DEVICE -s task snapshot
```

Read the result before continuing. Open once: reopening clears refs and images.
The unnamed `default` session is shared. Respect `busy`/`connectionBusy`; use
takeover only on explicit user instruction. After device-side release, explicitly
open again only when continuing is intended.

For repeated commands, `androidctl --device DEVICE -s task use` saves the
explicitly supplied workspace defaults. Bare `use` shows the local config path
and saved fields; `devices` shows the current device selection. Shared workspaces
need explicit device/session arguments. See [selection precedence and config
recovery](reference.md#connection-and-sessions).

## Read Observations

Read the absolute `snapshotPath` with host file tools.
Paths in text output are JSON-quoted; decode them as JSON strings before
opening the files. XML is the default;
`--raw` saves the tree as JSON. Global `--json` independently selects result JSON
on stdout. See [XML defaults and gaps](reference.md#xml-defaults-and-gaps) and
[delivery options](reference.md#images-and-delivery).

Real App JSON result excerpts follow. Paths and image IDs use symbolic values;
omitted fields remain in the original results.

```json
{
  "command": "snapshot",
  "result": "done",
  "data": {
    "snapshotPath": "SNAPSHOT_PATH",
    "snapshotCached": false,
    "snapshotCompleteness": {
      "partial": true,
      "missingCount": 1,
      "unavailableNodeCount": 0
    }
  }
}
```

```json
{
  "command": "screenshot",
  "result": "done",
  "data": {
    "imagePath": "IMAGE_PATH",
    "image": {"id": "IMAGE_ID", "width": 1080, "height": 2400, "mappingKnown": true},
    "snapshotCompleteness": {
      "partial": true,
      "missingCount": 1,
      "unavailableNodeCount": 0
    }
  }
}
```

Keep the App/window, sampling time, completeness, full values, relevant ancestors
and all candidate matches. When host output is truncated, expand the same file
before collecting again. A failed file read needs diagnosis, not another action.

Reuse the returned observation when it answers the next decision. Collect again
for transitions, changed targets or missing facts. Partial observations can
establish reliable positive facts; gaps cannot prove absence or selector
uniqueness. A complete sample does not establish business readiness.

`snapshot` collects a new sample. `snapshot --cached [REF]`, `snapshot REF` and
`find TEXT` reuse the previous sample; check sequence and age. These cached reads
cannot request a new screenshot. Use a fresh snapshot with screenshot when both
are needed. Scroll off-screen nodes into view and observe again before acting.

For same-sample raw diagnostics of summarized gaps or image/tree differences,
see [XML defaults and gaps](reference.md#xml-defaults-and-gaps).

Automatic observations are temporary workspace files. Read them while available
and copy lasting deliverables elsewhere; the cache can reclaim even edited files.
See [artifact paths and retention](reference.md#images-and-delivery).

## Select And Act

Use current refs or exact `--role`, `--name`, `--text`, `--id` filters. Filters
combine on one original node; these are not Playwright locator strings. Scope
with `--in PACKAGE` or `--window ID`. Repeat a non-active window's explicit
`--window` on every targeted action. Disabled/unavailable candidates still count
toward ambiguity. `ref-unavailable` is not actionable.

Replace symbolic refs with observed targets and check their business identity.
The CLI may repair an old ref once only after the entire action was definitely
not dispatched, within the same budget. Inspect changed requested/resolved/selected
targets. Repair does not prove business identity, and dispatched or UNKNOWN
actions are never automatically retried. See [ref and action rules](reference.md#observations-and-targets).

`fill` reports focus, fill and optional submit separately. A readable exact match
without submit needs no input. Otherwise it focuses the original field when
needed. After an accepted ordinary write, it completes a finite observation and
checks the last successful post-write sample of the original visible field.
While the original field remains readable and differs, fill keeps observing as
time permits within `--settle`. A larger `--settle` may allow a later exact match.
`--settle 0` only attempts the first post-write sample within the remaining budget.
Losing that field stops later steps. Password masks cannot establish equality;
an accepted password write can have unobservable verification.

Submit requires the current original field to remain an exact match after fresh
focus admission; accepted password writes are the exception.
Submit prefers the field's exposed IME action. Only a known single-line field
without an IME action uses one Enter; it never tries Enter after an IME attempt.
An App may treat Enter as two submissions, and multiline IME may insert a newline.
Verify the business effect. Preserve earlier sent steps when a later step fails.
Standalone `ime REF` requests the exposed IME action on the already-focused
original field; it does not focus the field. `key ... REF` also requires that
field already focused; a key without REF is global.

`check`/`uncheck` verify the exposed value and send no input when it already
matches. They may click a suggested collection row when the control lacks a
direct click; verify the resulting control value. Change a selected radio by
choosing another option. Interpret `checked` with `checkedModel` and
`stateDescription`, keeping missing facts unknown.

Use `scroll REF [DIR]` or selector flags; DIR defaults to `forward`. Match the
advertised directions, using `down` for `page-down`, for example.

`expand`, `collapse` and `dismiss` request those exposed actions. There is no
automatic coordinate fallback. For navigation,
use `apps`, `open`, `back`, `home`, `recents`, `notifications` and `wake`.
Verify the destination; Recents can toggle and wake does not unlock credentials.

## Verify And Wait

| Result | Next action |
| --- | --- |
| `done` | Verify the business outcome. |
| `failed` | Read the reason, actual value and partial steps. |
| `not sent` | Correct the reported problem before a new request. |
| `UNKNOWN` | Preserve the result and diagnose before further changes. |
| `still running` | For a shell/transfer job, query or cancel its original ID. For `daemon stop`, recheck with `daemon status`. |

`selected` describes the chosen action. `dispatchStarted` says whether the command
entered dispatch; each fill step keeps its own `selected` and `sent` facts.
Missing facts do not mean false.
Read focus/fill/submit independently, including verification and `actualValue`.
Use `--json` for the [complete result fields](reference.md#json-field-guide).

Prefer positive business conditions, such as `wait --contains "Order confirmed"`
or `wait --app PACKAGE`. Read the result area: an input echo alone is insufficient.
`wait --gone --text "Saving"` requires complete evidence of absence. `wait --idle`
requires complete stable samples, which can still precede business readiness.
`wait --screen-change` compares against the session's preceding complete published
observation; retain its collection policy. Missing/partial baselines or changed
scope are rejected. Use exactly one wait condition.

`changed`, `unchanged`, `loading` and `unsettled` describe observations, not
business success. `--timeout` sets the request budget; `--settle` limits ordinary
observation waiting. Ordinary fill verifies after observation settling.
Check/uncheck can continue verifying beyond `--settle`, within the remaining
request budget. A returned sample may precede later App effects.
See [budgets and duration formats](reference.md#results-and-budgets).

After a `done` semantic `click`, check the intended result and any pending App
work before sending more input. Do not repeat the same click without new evidence
that another attempt is appropriate; an unchanged semantic observation or gaps
in a partial observation are insufficient. If current evidence confirms that the
intended result did not occur, view a current screenshot: the corresponding touch
target may be activated with `tap` under [Use Images](#use-images). Verify its
business outcome.

### UNKNOWN

1. Preserve UNKNOWN; after the original CLI ends, inspect effects read-only. Continue on reliable success.
2. Check UI resource release using the [capture rules](reference.md#results-and-budgets); elapsed time is insufficient.
3. Proved non-occurrence and an ended operation permit one new action on a current target; verify it.
4. Stop after another UNKNOWN; switching input methods does not resolve uncertainty.
5. For payment, sending or deletion, require authoritative records or clear object state; otherwise report uncertainty.

## Complete A Form Task

With the named session open, collect and read the form. Replace the placeholders
with the actual package, current field ref, value and business-ready text.

```sh
androidctl --device DEVICE -s task snapshot --in PACKAGE --screenshot
androidctl --device DEVICE -s task fill FIELD_REF "VALUE" --submit --in PACKAGE
androidctl --device DEVICE -s task wait --contains "BUSINESS_READY_TEXT" --in PACKAGE
```

Run each command only after reading the preceding result. View the PNG when the
decision needs it. After fill, check each step and the business result; handle
non-done results before continuing. After wait, read the ready state and choose
the current next target. Click it, verify the next business state and close the session.

Example result prefix:

```text
click: done; settle=unsettled; changed
```

The click was accepted and the tree changed, but settling did not finish. Read
the observation at the reported path to decide whether the business state is ready.

## Use Images

When semantics lack a needed fact, run `screenshot` and view its actual `imagePath`
PNG with the host image tool before choosing an image-dependent action.
`--image-out PATH` implies screenshot; action screenshots share the action budget.
A known action with a failed image needs the missing screenshot, not another dispatch.

Coordinates require API 34+ and a current screenshot with verified mapping.
`--image ID` guards the session's current image; it does not select an older one.
Read the ID from JSON `data.image.id`.
Follow the [image ID, expiry and coordinate rules](reference.md#images-and-delivery),
using original pixels or the preview's actual scale. A coordinate long press uses
equal swipe endpoints and explicit duration; semantic long press uses `long-click`.
An attempted touch failure remains UNKNOWN even if cancellation succeeds. On
API 30–33, ask the user when semantics cannot complete the task; do not bypass
this with shell touch input.

## Run Jobs And Transfer Files

```sh
androidctl --device DEVICE -s task shell --script-file task.sh
androidctl --device DEVICE -s task job get JOB_ID
androidctl --device DEVICE -s task job output JOB_ID --stream stdout --out output.bin
androidctl --device DEVICE -s task push local.txt /sdcard/Download/remote.txt
androidctl --device DEVICE -s task pull /sdcard/Download/remote.txt local-copy.txt
```

Supply one complete script after `--` or through `--script-file`. Prefer UTF-8
without BOM and LF line endings, and quote it against host-shell expansion.
Read [script limits and job rules](reference.md#jobs-and-files) before large scripts
or background work. `--wait 0` ends only this call's wait; `--background` leaves
the job supervised. Delegated App/system-service work needs its own business check.

Read both execution and cleanup facts; a known script result can accompany
cleanup failures. The reference explains [shell exits and cleanup](reference.md#jobs-and-files).

Keep the original job ID. `job output` exports retained bytes without rerunning
the script; truncated/expired output is incomplete. Follow its recovery command
with a new output path. `push`/`pull` refuse overwrite unless `--replace` is intended.
Inspect the original job and destination before another transfer; never resend
an unknown finish because publication may already have happened.

For shell, read `helperAlive`, `cleanup`, `cleanupError` and `signalErrors`;
`helperAlive=false` can coexist with
`cleanup=unknown`. Signal errors mean processes may still modify data. For transfers,
`stopRequested` does not mean ended; read `resourcesActive` and `cleanupError` for
the reported side; local completion does not prove device completion. Query or
cancel the original ID and preserve cleanup failures. For shell UNKNOWN without
a job ID, use the reference's
[correlation procedure](reference.md#shell-without-an-initial-job-id).

## Recover And Finish

For attachment/save/read failures, preserve the action result and obtain only the
missing observation. For stale targets, collect fresh facts and select the current
target. `status` reports worker/session/resources and failed connection checks;
it does not prove action readiness. Use the reference's [connection
recovery](reference.md#connection-and-sessions), [ADB route setup](reference.md#device-setup-and-adb-route)
or [device diagnostics](reference.md#device-diagnostics) for those specific failures.
Certificate changes require a trusted export. Forgetting a pairing does not stop
remote work. Force executor exit can leave descendants and temporary files.

Verify the business result and restore settings changed temporarily to carry out the task.
Remove only your remote temporary objects, confirm original jobs/helpers ended,
then `session close` unless the supervising workflow owns closure. Preserve local
deliverables and recovery records. Report the result, remaining uncertainties and
links using the reported absolute paths.
