from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PUBLIC_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"
APK_VERIFY_LOG_NAME = "apksigner-verify.txt"
CHECKSUMS_NAME = "SHA256SUMS"


def read_version(repo_root: Path) -> str:
    version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise SystemExit(
            "VERSION must contain MAJOR.MINOR.PATCH "
            "after trimming surrounding whitespace"
        )
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleasePaths:
    repo_root: Path
    version: str
    gradle_apk_path: Path
    stage_dir: Path
    staged_apk_path: Path
    checksums_path: Path
    apk_verify_log_path: Path


def build_release_paths(repo_root: Path) -> ReleasePaths:
    version = read_version(repo_root)
    stage = repo_root / "dist/release/android" / version
    return ReleasePaths(
        repo_root=repo_root,
        version=version,
        gradle_apk_path=repo_root
        / "android/app/build/outputs/apk/release/app-release.apk",
        stage_dir=stage,
        staged_apk_path=stage / PUBLIC_APK_TEMPLATE.format(version=version),
        checksums_path=stage / CHECKSUMS_NAME,
        apk_verify_log_path=stage / APK_VERIFY_LOG_NAME,
    )


def ensure_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{description} not found at {path}")


def build_tools_rank(path: Path) -> tuple[int, tuple[int, ...], str]:
    name = path.parent.name
    match = re.fullmatch(r"(\d+(?:\.\d+)*)([-._].+)?", name)
    if match is None:
        return (0, (), name)
    return (1 if match[2] else 2, tuple(map(int, match[1].split("."))), name)


def resolve_build_tool(name: str) -> Path:
    filenames = (name, f"{name}.exe", f"{name}.bat")
    for filename in filenames:
        found = shutil.which(filename)
        if found:
            return Path(found)
    candidates: list[Path] = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(variable)
        if not root:
            continue
        for version_dir in (Path(root) / "build-tools").glob("*"):
            for filename in filenames:
                candidate = version_dir / filename
                if candidate.is_file():
                    candidates.append(candidate)
                    break
    if candidates:
        return max(candidates, key=build_tools_rank)
    raise SystemExit(
        f"{name} not found on PATH or in SDK build-tools; "
        "set ANDROID_HOME or ANDROID_SDK_ROOT"
    )


def inspect_apk(apk: Path, version: str, log_path: Path) -> None:
    manifest = subprocess.run(
        [str(resolve_build_tool("aapt2")), "dump", "badging", str(apk)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if manifest.returncode:
        raise SystemExit(f"aapt2 could not read {apk}:\n{manifest.stdout}")
    package_line = next(
        (line for line in manifest.stdout.splitlines() if line.startswith("package: ")),
        None,
    )
    if package_line is None:
        raise SystemExit(f"aapt2 did not report package metadata for {apk}")
    fields = dict(item.split("=", 1) for item in shlex.split(package_line[9:]))
    expected = {
        "name": "com.rainng.androidctl",
        "versionName": version,
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            raise SystemExit(
                f"APK {key} mismatch: expected {value}, got {fields.get(key)!r}"
            )
    signature = subprocess.run(
        [
            str(resolve_build_tool("apksigner")),
            "verify",
            "--verbose",
            "--print-certs",
            str(apk),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
    )
    log_path.write_text(signature.stdout, encoding="utf-8")
    if signature.returncode:
        raise SystemExit(f"apksigner verification failed; see {log_path}")
    print(
        f"APK versionName={fields['versionName']} versionCode={fields['versionCode']}"
    )


def assert_source_clean(repo_root: Path, phase: str) -> None:
    if not (repo_root / ".git").exists():
        return
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode or result.stdout.strip():
        raise SystemExit(f"{phase}: source tree must be clean\n{result.stdout}")


def stage_release_apk(paths: ReleasePaths) -> Path:
    assert_source_clean(paths.repo_root, "pre-stage")
    apk = paths.gradle_apk_path
    ensure_file(apk, "Gradle release APK")
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk, paths.staged_apk_path)
    return apk


def write_sha256sums(paths: ReleasePaths) -> None:
    ensure_file(paths.staged_apk_path, "Staged release APK")
    paths.checksums_path.write_text(
        f"{sha256_file(paths.staged_apk_path)}  {paths.staged_apk_path.name}\n",
        encoding="utf-8",
    )


def verify_release(paths: ReleasePaths) -> str:
    paths.apk_verify_log_path.unlink(missing_ok=True)
    ensure_file(paths.staged_apk_path, "Staged release APK")
    digest = sha256_file(paths.staged_apk_path)
    ensure_file(paths.checksums_path, "Android SHA256SUMS")
    entries = [
        line.split()
        for line in paths.checksums_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if entries != [[digest, paths.staged_apk_path.name]]:
        raise SystemExit(
            "SHA256SUMS must contain this APK's actual digest and filename"
        )
    inspect_apk(
        paths.staged_apk_path,
        paths.version,
        paths.apk_verify_log_path,
    )
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and verify Android release assets."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "verify",
        help="Check actual APK metadata, bytes and signature without rebuilding.",
    )
    commands.add_parser(
        "prepare", help="Stage, checksum and verify the Gradle release APK."
    )
    args = parser.parse_args(argv)
    paths = build_release_paths(args.repo_root.resolve())
    if args.command == "prepare":
        print(f"staged APK: {stage_release_apk(paths)} -> {paths.staged_apk_path}")
        write_sha256sums(paths)
        print(f"wrote checksum: {paths.checksums_path}")
    print(f"verified APK: sha256={verify_release(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
