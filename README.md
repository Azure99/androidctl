# AndroidCtl

[![PyPI](https://img.shields.io/pypi/v/androidctl.svg)](https://pypi.org/project/androidctl/)
[![CI](https://github.com/Azure99/androidctl/actions/workflows/quality.yml/badge.svg)](https://github.com/Azure99/androidctl/actions/workflows/quality.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

**A Playwright-like CLI for Android automation, built for AI agents.**

AndroidCtl lets your agent operate apps on an Android phone or emulator. The agent
reads the screen as compact XML, acts on its elements, waits for results and takes
screenshots; it can also run shell scripts and transfer files.

Works with Claude Code, Codex, OpenCode and any other agent that can run shell
commands.

- **No root, no ADB for daily use.** The Android app runs through Shizuku and
  accepts connections over your network after a one-time pairing.
- **Built for agents.** Snapshots carry refs such as `e6`, and every command
  reports `done`, `failed`, `not sent` or `UNKNOWN`. Uncertain actions are never
  resent automatically.
- **Light on context.** Snapshots and screenshots are saved as files and each
  command prints a short result, so the agent reads only what it needs.
- **Everything in one package.** `pip install androidctl` includes the CLI, the
  matching Android app and the agent skill.

## Requirements

- Python 3.12 or newer on Windows, macOS or Linux.
- Android 11 or newer on arm64-v8a or x86_64, main user and default display only.
- [Shizuku](https://shizuku.rikka.app/) started through ADB or wireless debugging.
  Root mode is not supported.
- A network route from the computer to the phone.

## Setup

### 1. Install

```sh
pip install androidctl
androidctl apk --out androidctl.apk
```

Install `androidctl.apk` on the phone, for example with `adb install androidctl.apk`.
It always matches the installed CLI; the two versions must be the same.

### 2. Install The Skill

Export the skill into the directory your agent loads skills from:

```sh
mkdir -p ~/.claude/skills
androidctl skill --out ~/.claude/skills/androidctl
```

That is the location for Claude Code. For Codex, use `~/.agents/skills` instead;
OpenCode reads both. The export contains `SKILL.md`, the agent's workflow, and
`reference.md`, the full command reference.

### 3. Pair

Start Shizuku, open AndroidCtl, tap **Authorize Shizuku** and turn on **Gateway**.
When it shows **Listening** and **Executor attached**, tap **Show new pairing code**.
Give your agent the address and code, or pair yourself:

```sh
androidctl pair 192.168.1.20:18843 000042 --name phone
```

The first pairing trusts the certificate the phone presents, so pair on a network
you trust. For an emulator or a phone without a network route, use an
[ADB route](skills/androidctl/reference.md#device-setup-and-adb-route). After the
phone restarts, start Shizuku and turn on **Gateway** again.

## Ask Your Agent

With the skill installed, ask for the result you want:

- "Open Settings on my phone and turn on dark mode."
- "Go through the sign-up flow in the Example app and take a screenshot of each
  screen."
- "Copy the newest file in /sdcard/Download on my phone to this folder."

If your agent does not support skills, start with: "Use androidctl to control my
phone. First run `androidctl skill --out androidctl-skill` and read its SKILL.md."

To run several agents, start each one with its own session and device:

```sh
ANDROIDCTL_DEVICE=phone ANDROIDCTL_SESSION=checkout claude
```

A phone serves one session at a time, so agents on different phones run in
parallel and agents sharing a phone take turns.

## What Your Agent Runs

```sh
androidctl session open
androidctl snapshot
androidctl fill e3 "Groceries" --submit
androidctl click e6
androidctl wait --contains "Saved"
androidctl screenshot
androidctl session close
```

Run `androidctl COMMAND --help` for options, or read the skill for the complete
workflow.

## Upgrading

- Finish active work, then run `androidctl daemon stop` before replacing the
  Python package, even with the same version.
- Finish device work before installing a new app version. Before reinstalling the
  same version, tap **Stop idle executor**.
- Delete the exported skill directory and run `androidctl skill --out` again so
  the instructions match the new version.

## Development

See [Repository Guidelines](AGENTS.md#build-test-and-development-commands) for
environment setup and quality checks, and [Release Operations](tools/release/README.md)
for signing, local candidates and manual publishing.
