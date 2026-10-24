from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def cli(*arguments: str, console: bool = False) -> str:
    entry = (
        [
            str(
                Path(sys.executable).with_name(
                    "androidctl.exe" if sys.platform == "win32" else "androidctl"
                )
            )
        ]
        if console
        else [sys.executable, "-m", "androidctl"]
    )
    command = [*entry, *arguments]
    print(f"$ {shlex.join(command)}")
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
    )
    print(result.stdout, end="")
    if result.returncode:
        raise SystemExit(f"Installed CLI failed ({result.returncode}): {command!r}")
    return result.stdout


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Check the installed CLI and its exported package resources."
    )
    parser.add_argument("expected_version")
    parser.add_argument("expected_apk_sha256")
    parser.add_argument("skill_source", type=Path)
    args = parser.parse_args(argv)
    cli("--help", console=True)
    cli("--help")
    if cli("--version").strip() != args.expected_version:
        raise SystemExit(
            f"CLI version must match installed version {args.expected_version}"
        )
    with TemporaryDirectory(prefix="package-export-", dir=Path.cwd()) as temporary:
        apk = Path(temporary) / "agent.apk"
        cli("apk", "--out", str(apk))
        payload = apk.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != args.expected_apk_sha256:
            raise SystemExit(f"Exported APK SHA256 mismatch: {digest}")
        print(f"Exported APK: {len(payload)} bytes, sha256={digest}")
        output = Path(temporary) / "androidctl"
        cli("skill", "--out", str(output))
        expected_files = {
            path.name for path in args.skill_source.glob("*.md") if path.is_file()
        }
        actual_files = {path.name for path in output.iterdir()}
        if actual_files != expected_files:
            raise SystemExit(
                f"Exported skill files mismatch: expected {sorted(expected_files)}, "
                f"got {sorted(actual_files)}"
            )
        for name in sorted(expected_files):
            content = (output / name).read_bytes()
            if content != (args.skill_source / name).read_bytes():
                raise SystemExit(f"Exported {name} differs from the source candidate")
            print(
                f"Exported {name}: {len(content)} bytes, "
                f"sha256={hashlib.sha256(content).hexdigest()}"
            )


if __name__ == "__main__":
    main()
