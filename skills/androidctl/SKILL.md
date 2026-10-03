---
name: androidctl
description: Use when operating or inspecting apps on an Android phone, tablet, or emulator through the AndroidCtl CLI, including UI actions, screenshots, device shell scripts, and file transfers.
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
androidctl --device DEVICE -s task snapshot
```

Read the result before continuing. Open once: reopening clears refs and images.
The unnamed `default` session is shared. Respect `busy`/`connectionBusy`; use
takeover only on explicit user instruction. After device-side release, explicitly
open again only when continuing is intended.

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
Inspect requested/resolved/selected targets. See [ref and action rules](reference.md#observations-and-targets).

Standalone `ime REF` requests the exposed IME action on the already-focused
original field; it does not focus the field.

`expand`, `collapse` and `dismiss` request those exposed actions. There is no
automatic coordinate fallback.

Use `apps` to discover launchable applications.

## Results

| Result | Next action |
| --- | --- |
| `done` | Verify the business outcome. |
| `failed` | Read the reason and retained registration or confirmation facts. |
| `not sent` | Correct the reported problem before a new request. |
| `UNKNOWN` | Preserve the result and diagnose before further changes. |
| `still running` | For a shell/transfer job, query or cancel its original ID. For `daemon stop`, recheck with `daemon status`. |

`selected` describes the chosen action. `dispatchStarted` says whether the command
entered dispatch. Missing facts do not mean false.

Use `--json` for the [complete result fields](reference.md#json-field-guide).

`changed`, `unchanged`, `loading` and `unsettled` describe observations, not
business success. `--timeout` sets the request budget; `--settle` limits ordinary
observation waiting. A returned sample may precede later App effects.
See [budgets and duration formats](reference.md#results-and-budgets).

### UNKNOWN

1. Preserve UNKNOWN; after the original CLI ends, inspect effects read-only. Continue on reliable success.
2. Check UI resource release using the [capture rules](reference.md#results-and-budgets); elapsed time is insufficient.
3. Proved non-occurrence and an ended operation permit one new action on a current target; verify it.
4. Stop after another UNKNOWN; switching input methods does not resolve uncertainty.
5. For payment, sending or deletion, require authoritative records or clear object state; otherwise report uncertainty.

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
