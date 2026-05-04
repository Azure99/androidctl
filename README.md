# AndroidCtl

AndroidCtl is a local Android automation toolkit for developer and agent
workflows. It provides a host CLI, a host daemon, shared Python contracts, and an
Android device agent that can observe the current UI, expose stable element refs,
run actions, wait for screen changes, and collect artifacts such as screenshots.

The usual loop is:

```sh
androidctl observe
androidctl tap n3
androidctl wait --until idle
androidctl observe
```

## Repository Layout

- `contracts/`: shared Python wire models and command catalog.
- `androidctl/`: public CLI, command parsing, daemon discovery, and XML output.
- `androidctld/`: host daemon, runtime state, command execution, and device RPC.
- `android/`: Kotlin Android device agent with a foreground RPC service and
  Accessibility service.

## Requirements

- Python 3.10.
- Android SDK platform tools, especially `adb`.
- An Android 11+ device or emulator with USB debugging enabled.

## Install From PyPI

Install the released host tools with the single public distribution:

```sh
pip install androidctl
androidctl --help
```

The `androidctl` distribution includes the CLI, host daemon, and shared Python
contracts. Do not install separate `androidctld` or `androidctl-contracts`
distributions.

## Install From Source

Create a local Python environment and install AndroidCtl in editable mode:

```sh
conda create -p ./.conda python=3.10
./.conda/bin/python -m pip install -U pip
./.conda/bin/python -m pip install -e ".[dev]"
export PATH="$PWD/.conda/bin:$PATH"
```

Check the CLI:

```sh
androidctl --version
androidctl --help
```

## Build The Android Agent

Released Python packages include the Android agent APK used by setup. When
working from source and testing a locally built debug agent, build a debug APK
and pass it to setup with `--apk`:

```sh
(cd android && ./gradlew :app:assembleDebug)
androidctl setup --adb --apk android/app/build/outputs/apk/debug/app-debug.apk
```

The debug APK is created at:

```sh
android/app/build/outputs/apk/debug/app-debug.apk
```

## Prepare A Device

Connect a device with USB debugging enabled and confirm that ADB can see it:

```sh
adb devices
```

Run the onboarding helper:

```sh
androidctl setup --adb
```

If more than one device is connected, pass the ADB serial:

```sh
androidctl setup --adb --serial <adb-serial>
```

Setup installs the Android agent, starts its foreground RPC server, provisions a
device token, attempts to enable the AndroidCtl Accessibility service, and checks
that the daemon can talk to the device. If Android blocks automatic
Accessibility enablement, follow the manual instructions printed by the command
and rerun setup.

## Optional Wireless ADB

Pair and connect from the CLI if you prefer Android wireless debugging:

```sh
androidctl adb-pair --pair <host:pair-port> --code <pairing-code>
androidctl adb-connect <host:connect-port>
androidctl setup --adb --serial <host:connect-port>
```

## Basic Usage

Observe the current screen:

```sh
androidctl observe
```

The output is XML intended for scripts and agents. Interactive elements receive
refs such as `n1`, `n2`, and `n3`; use those refs in later commands.

Common commands:

```sh
androidctl list-apps
androidctl open app:com.android.settings
androidctl open https://example.com
androidctl tap n3
androidctl long-tap n3
androidctl focus n4
androidctl type n4 "hello from androidctl"
androidctl submit n4
androidctl scroll n8 down
androidctl back
androidctl home
androidctl recents
androidctl notifications
androidctl wait --until screen-change
androidctl wait --until gone --ref n3
androidctl wait --until text-present --text "Done"
androidctl wait --until app --app com.android.settings
androidctl screenshot
androidctl close
```

Commands use the current workspace by default. For a dedicated runtime state
directory, pass `--workspace-root <path>` or set `ANDROIDCTL_WORKSPACE_ROOT`.

## Development

Run the default verification suite from the repository root:

```sh
task test
task lint
task quality
```

Useful focused commands:

```sh
task androidctl:test
task androidctld:test
task contracts:test
task android:test
task format
task --list
```

Source development additionally requires Conda, JDK 17 or newer for Android
Gradle builds, and `task` for repository shortcuts.

Process-level CLI checks are available through:

```sh
task test:extended
```
