# AndroidCtl

AndroidCtl lets people and AI agents operate Android apps through a local CLI
and Shizuku. The host connects to the Android app over the network; daily use
does not require ADB.

The App's Gateway accepts connections, and its shell-UID worker operates the
device.

The App uses these labels for that same worker:

| English App label | Chinese App label |
| --- | --- |
| Executor attached / Executor unavailable | 执行器已连接 / 执行器不可用 |
| Stop idle executor | 停止空闲执行器 |
| Force executor exit | 强制结束执行器 |

For agent workflows, run `androidctl skill --out androidctl-skill` after installing.
It creates a new directory with all top-level Markdown files from the installed
version's skill; start with `SKILL.md`. In this checkout, the corresponding
[SKILL](skills/androidctl/SKILL.md) covers complete tasks and the
[command reference](skills/androidctl/reference.md) defines results and limits.
Each command also has `--help`.

## Requirements

- Python 3.12 or newer on Windows, macOS or Linux.
- Android 11 (API 30) or newer, arm64-v8a or x86_64 userspace, Android user 0 and
  default display. Coordinates require API 34 or newer.
- Shizuku running through ADB or wireless debugging as shell UID 2000, with
  AndroidCtl authorized. Root mode is unsupported.
- A reachable network route to the AndroidCtl app, and matching host/app versions.

AndroidCtl does not restart a killed app or Shizuku. After device reboot, start
Shizuku again, open AndroidCtl and enable **Gateway**.

## Install And Pair

Before replacing the host package, including a same-version reinstall, finish
active work and run `androidctl daemon stop` with the installed version.
Finish device work before upgrading the App: its first executor attachment
replaces the old worker. Before reinstalling the same App version, use
**Stop idle executor** first. See [upgrade and recovery
rules](skills/androidctl/reference.md#connection-and-sessions).

Install the host package and obtain the matching Android APK, or build it from
this checkout using the [development instructions](AGENTS.md#build-test-and-development-commands).
Transfer and open the APK on Android. For an ADB installation, identify the device:

```sh
pip install androidctl
androidctl --version
adb devices -l
adb -s SERIAL install androidctl.apk
```

Open Shizuku and follow its wireless debugging procedure or displayed ADB startup
command. In AndroidCtl, authorize Shizuku and enable **Gateway**. Check **Listening**
and **Executor attached**, then choose **Show new pairing code**.

```sh
androidctl pair 192.168.1.20:18843 000042 --name phone
androidctl device select phone
androidctl --device phone status
```

Replace the address and code with the device's current values; keep leading zeros.
Displayed addresses are candidates, not connectivity tests. The
[connection reference](skills/androidctl/reference.md#connection-and-sessions)
covers pairing recovery, device management and IPv6 addresses.

First pairing trusts the presented certificate and cannot resist an active
intermediary. Later connections pin that complete certificate. Routing, firewall
and any port forwarding are your responsibility.

For an emulator or device without a LAN route, follow the optional
[ADB route setup](skills/androidctl/reference.md#device-setup-and-adb-route).

## Try An Observation

Use the `deviceId` returned by `pair`, or find a paired UUID with `--json devices`,
and choose a name for this task. Global options
precede the command. Open the named session once; if busy, respect its holder.

```sh
androidctl --device DEVICE_UUID -s trial session open
androidctl --device DEVICE_UUID -s trial snapshot
```

To reuse those defaults in this workspace, run
`androidctl --device DEVICE_UUID -s trial use`, then `androidctl snapshot`.
Workspace defaults do not isolate concurrent agents; use explicit
`--device` and `-s` when other agents may share the directory.
See [selection precedence and workspace rules](skills/androidctl/reference.md#connection-and-sessions).

Read the reported absolute `snapshotPath` using your host's file tools.
For visual details, capture and view the actual `imagePath` PNG. The reference
covers [formats, paths and delivery options](skills/androidctl/reference.md#images-and-delivery).
Close your session when finished:

```sh
androidctl --device DEVICE_UUID -s trial screenshot
androidctl --device DEVICE_UUID -s trial session close
```

For actions, verification, jobs and cleanup, follow the installed SKILL's complete
task loop. Preserve `UNKNOWN` results and diagnose before further changes; never
automatically resend the input. See the SKILL's
[recovery guidance](skills/androidctl/SKILL.md#recover-and-finish).

## Development

See [Repository Guidelines](AGENTS.md#build-test-and-development-commands) for
environment setup, Host tests, JVM tests and direct Android builds.
