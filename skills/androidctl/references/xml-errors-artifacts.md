# XML, Errors, And Artifacts

Use this reference when parsing CLI output, routing failures by public code,
collecting artifacts, or deciding whether stdout or stderr is authoritative.

## XML Families

Public CLI output after successful parsing falls into these families:

- Semantic commands (`observe`, `open`, ref actions, global actions, `wait`)
  write `<result>` to stdout.
- Retained commands (`connect`, `screenshot`, `close`) write
  `<retainedResult>` to stdout.
- Successful `list-apps` writes `<listAppsResult>` to stdout.
- Usage, startup, environment, and connection failures write `<errorResult>` to
  stderr and keep stdout empty.

`setup`, `adb-pair`, and `adb-connect` are onboarding helpers. They write
human-readable progress and diagnostics to `stderr`, keep successful stdout
empty, and do not expose a stable XML or JSON result contract.

## Stdout, Stderr, Exit Code

Always pair output parsing with the process exit code:

- `0`: success.
- `1`: command or semantic failure.
- `2`: usage failure.
- `3`: environment failure.

Semantic and retained results can be `ok="false"` on stdout with a nonzero exit
code. Do not reinterpret those as stderr `<errorResult>` failures.

`list-apps` only has a successful stdout XML family. Device, auth, version, or
response-format failures for `list-apps` are stderr `<errorResult>` failures.

If the CLI reports `CLI_RENDER_FAILED` or `CLI_OUTPUT_FAILED`, treat exit code
`3` as authoritative. Output may be partial and should not be assumed to be
well-formed XML.

## Artifacts

Public artifact attributes are workspace-relative:

- Screenshots: `.androidctl/screenshots/...`
- Standalone observed screen XML: `.androidctl/artifacts/screens/...`

Semantic results may include `screenshotPng` and `screenXml`. Retained screenshot
results expose `screenshotPng`. Only consume documented artifact attributes.

## Representative Recovery

- `USAGE_ERROR`: fix local syntax or missing required options, such as
  `gone --ref`, `text-present --text`, `app --app`, duration format, ref format,
  scroll direction, or blank `--screen-id`.
- `DEVICE_NOT_CONNECTED`: run `connect` or `setup`; if the Android app was
  restarted, use the current token.
- `SCREEN_UNAVAILABLE`: run `observe`; if observe cannot recover, reconnect and
  verify device readiness.
- `WORKSPACE_BUSY`: choose a different workspace, close the conflicting runtime,
  or set/check `ANDROIDCTL_OWNER_ID`.
- `ACCESSIBILITY_NOT_READY`: enable the AndroidCtl Accessibility service, then
  retry observe/action.
- `REF_NOT_FOUND` or semantic action-target failure: refresh with `observe` and
  choose a ref from the new screen.
- `WORKSPACE_STATE_UNWRITABLE` or artifact write failure: use a writable
  workspace and inspect `.androidctl/` permissions.
- `DEVICE_AGENT_UNAUTHORIZED` or token/auth public errors: use the current
  token from the app UI and reconnect.
- `DEVICE_AGENT_VERSION_MISMATCH` or host/runtime release mismatch: install a
  matching AndroidCtl release, then reconnect.

Avoid logging tokens, Bearer values, raw device serials, absolute workspace
paths, raw diagnostic payloads, or stack traces.
