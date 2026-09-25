# AndroidCtl

AndroidCtl connects a local CLI to an Android app through Shizuku.
Daily use does not require ADB.

The App's Gateway accepts connections, and its shell-UID worker operates the
device.

The App uses these labels for that same worker:

| English App label | Chinese App label |
| --- | --- |
| Executor attached / Executor unavailable | 执行器已连接 / 执行器不可用 |
| Stop idle executor | 停止空闲执行器 |

The [SKILL](skills/androidctl/SKILL.md) covers a session workflow, and the
[command reference](skills/androidctl/reference.md) defines pairing, results and
recovery. Each command also has `--help`.

## Requirements

- Python 3.12 or newer on Windows, macOS or Linux.
- Android 11 (API 30) or newer, arm64-v8a or x86_64 userspace and Android user 0.
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

## Use A Session

Use the `deviceId` returned by `pair`, or find a paired UUID with `--json devices`.
Global options precede the command. Keep a distinct session name for each task.

```sh
androidctl --json devices
androidctl --device DEVICE_UUID -s trial session open
androidctl --device DEVICE_UUID -s trial status
androidctl --device DEVICE_UUID -s trial session close
```

`status` observes the actual owner without creating or refreshing a lease. Respect
`busy`; explicit takeover is limited to another session of the same pairing.

## Development

See [Repository Guidelines](AGENTS.md#build-test-and-development-commands) for
environment setup, Host tests, JVM tests and direct Android builds.
