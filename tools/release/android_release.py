from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.release.version_lockstep import parse_canonical_version_text

APK_VERIFY_LOG_NAME = "apksigner-verify.txt"
CHECKSUMS_NAME = "SHA256SUMS"
CHECKSUM_VERIFY_LOG_NAME = "sha256sum-check.txt"
PUBLIC_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"
PUBLIC_BUNDLE_TEMPLATE = "androidctl-agent-{version}-release-bundle.zip"
CHECKSUM_LINE_PATTERN = re.compile(r"^([0-9a-fA-F]{64}) ([ *])(.+)$")
STABLE_BUILD_TOOLS_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")
PREVIEW_BUILD_TOOLS_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)(?:[-._].+)$")
APKSIGNER_CANDIDATE_NAMES = ("apksigner", "apksigner.bat")


@dataclass(frozen=True)
class ReleasePaths:
    repo_root: Path
    version: str
    android_dir: Path
    gradle_apk_metadata_path: Path
    stage_dir: Path
    staged_apk_path: Path
    checksums_path: Path
    checksum_verify_log_path: Path
    apk_verify_log_path: Path
    bundle_path: Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and verify Android release APK staging assets."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root path. Defaults to the parent of tools/release/.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("stage", help="Stage the release APK under dist/release.")
    subparsers.add_parser(
        "checksum", help="Write SHA256SUMS for the staged public APK name."
    )
    subparsers.add_parser(
        "verify", help="Verify staged checksums and APK signature into log files."
    )
    subparsers.add_parser(
        "bundle",
        help="Create the optional offline release bundle zip from staged assets.",
    )
    subparsers.add_parser(
        "upload-dry-run",
        help="Print manual GitHub Releases upload commands without executing them.",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    paths = build_release_paths(repo_root)

    if args.command == "stage":
        source_apk_path = stage_release_apk(paths)
        print(f"staged APK: {relative_to_repo(paths.staged_apk_path, repo_root)}")
        print(f"source APK: {relative_to_repo(source_apk_path, repo_root)}")
        return 0

    if args.command == "checksum":
        ensure_staged_apk(paths)
        write_sha256sums(paths)
        print(f"wrote checksum: {relative_to_repo(paths.checksums_path, repo_root)}")
        return 0

    if args.command == "verify":
        ensure_staged_apk(paths)
        ensure_file(
            paths.checksums_path,
            "staged checksum file",
            repo_root=repo_root,
        )
        run_checksum_verification(paths)
        run_apk_signature_verification(paths)
        print(
            f"wrote verify log: {relative_to_repo(paths.apk_verify_log_path, repo_root)}"
        )
        print(
            "wrote checksum check log: "
            f"{relative_to_repo(paths.checksum_verify_log_path, repo_root)}"
        )
        return 0

    if args.command == "bundle":
        ensure_staged_apk(paths)
        ensure_file(
            paths.checksums_path,
            "staged checksum file",
            repo_root=repo_root,
        )
        ensure_file(
            paths.checksum_verify_log_path,
            "checksum verification log",
            repo_root=repo_root,
        )
        ensure_file(
            paths.apk_verify_log_path,
            "APK verification log",
            repo_root=repo_root,
        )
        create_release_bundle(paths)
        print(f"wrote bundle: {relative_to_repo(paths.bundle_path, repo_root)}")
        return 0

    if args.command == "upload-dry-run":
        print(render_upload_dry_run(paths))
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


def build_release_paths(repo_root: Path) -> ReleasePaths:
    version_text = (repo_root / "VERSION").read_text(encoding="utf-8")
    version = parse_canonical_version_text(version_text)
    android_dir = repo_root / "android"
    stage_dir = repo_root / "dist" / "release" / "android" / version
    return ReleasePaths(
        repo_root=repo_root,
        version=version,
        android_dir=android_dir,
        gradle_apk_metadata_path=android_dir
        / "app"
        / "build"
        / "outputs"
        / "apk"
        / "release"
        / "output-metadata.json",
        stage_dir=stage_dir,
        staged_apk_path=stage_dir / PUBLIC_APK_TEMPLATE.format(version=version),
        checksums_path=stage_dir / CHECKSUMS_NAME,
        checksum_verify_log_path=stage_dir / CHECKSUM_VERIFY_LOG_NAME,
        apk_verify_log_path=stage_dir / APK_VERIFY_LOG_NAME,
        bundle_path=stage_dir / PUBLIC_BUNDLE_TEMPLATE.format(version=version),
    )


def stage_release_apk(paths: ReleasePaths) -> Path:
    source_apk_path = resolve_gradle_release_apk(paths)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_apk_path, paths.staged_apk_path)
    return source_apk_path


def resolve_gradle_release_apk(paths: ReleasePaths) -> Path:
    ensure_file(
        paths.gradle_apk_metadata_path,
        "Gradle release APK metadata",
        repo_root=paths.repo_root,
    )
    metadata = json.loads(paths.gradle_apk_metadata_path.read_text(encoding="utf-8"))
    elements = metadata.get("elements")
    if not isinstance(elements, list) or len(elements) != 1:
        raise SystemExit(
            "expected exactly one release APK output in "
            f"{relative_to_repo(paths.gradle_apk_metadata_path, paths.repo_root)}"
        )
    version_name = elements[0].get("versionName")
    if version_name != paths.version:
        raise SystemExit(
            f"release APK metadata versionName mismatch: expected {paths.version}, "
            f"got {version_name!r}"
        )
    output_file = elements[0].get("outputFile")
    if not isinstance(output_file, str) or not output_file:
        raise SystemExit(
            "release APK metadata did not expose a usable outputFile field"
        )
    source_apk_path = paths.gradle_apk_metadata_path.parent / output_file
    ensure_file(source_apk_path, "Gradle release APK", repo_root=paths.repo_root)
    return source_apk_path


def write_sha256sums(paths: ReleasePaths) -> None:
    digest = sha256_file(paths.staged_apk_path)
    content = f"{digest}  {paths.staged_apk_path.name}\n"
    paths.checksums_path.write_text(content, encoding="utf-8")


def run_checksum_verification(paths: ReleasePaths) -> None:
    verification_lines, failed_files = verify_checksums_file(paths)
    write_command_output(
        paths.checksum_verify_log_path,
        "".join(f"{line}\n" for line in verification_lines),
    )
    if failed_files:
        raise SystemExit(
            "sha256sum verification failed; see "
            f"{relative_to_repo(paths.checksum_verify_log_path, paths.repo_root)}"
        )


def run_apk_signature_verification(paths: ReleasePaths) -> None:
    apksigner_path = resolve_apksigner()
    result = subprocess.run(
        [
            str(apksigner_path),
            "verify",
            "--verbose",
            "--print-certs",
            paths.staged_apk_path.name,
        ],
        cwd=paths.stage_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    write_command_output(paths.apk_verify_log_path, result.stdout)
    if result.returncode != 0:
        raise SystemExit(
            "apksigner verification failed; see "
            f"{relative_to_repo(paths.apk_verify_log_path, paths.repo_root)}"
        )


def create_release_bundle(paths: ReleasePaths) -> None:
    entries = [
        paths.staged_apk_path,
        paths.checksums_path,
        paths.checksum_verify_log_path,
        paths.apk_verify_log_path,
    ]
    with ZipFile(paths.bundle_path, "w", compression=ZIP_DEFLATED) as bundle_zip:
        for entry in entries:
            bundle_zip.write(entry, arcname=entry.name)


def render_upload_dry_run(paths: ReleasePaths) -> str:
    tag_name = f"v{paths.version}"
    required_assets = [
        relative_to_repo(paths.staged_apk_path, paths.repo_root),
        relative_to_repo(paths.checksums_path, paths.repo_root),
    ]
    required_command = " ".join(
        [
            "gh",
            "release",
            "upload",
            shlex.quote(tag_name),
            *(shlex.quote(path) for path in required_assets),
            "--clobber",
        ]
    )
    bundle_command = " ".join(
        [
            "gh",
            "release",
            "upload",
            shlex.quote(tag_name),
            shlex.quote(relative_to_repo(paths.bundle_path, paths.repo_root)),
            "--clobber",
        ]
    )
    required_state = "ready" if required_stage_files_exist(paths) else "missing"
    bundle_state = "ready" if paths.bundle_path.is_file() else "missing"

    return "\n".join(
        [
            "GitHub Releases upload is disabled in this phase.",
            "Manual commands only; nothing was executed.",
            "",
            f"required assets [{required_state}]:",
            required_command,
            "",
            f"optional bundle [{bundle_state}]:",
            bundle_command,
            "",
            "staged verification evidence:",
            f"- {relative_to_repo(paths.checksum_verify_log_path, paths.repo_root)}",
            f"- {relative_to_repo(paths.apk_verify_log_path, paths.repo_root)}",
        ]
    )


def required_stage_files_exist(paths: ReleasePaths) -> bool:
    return paths.staged_apk_path.is_file() and paths.checksums_path.is_file()


def resolve_apksigner() -> Path:
    path_candidate = shutil.which("apksigner")
    if path_candidate:
        return Path(path_candidate)
    path_candidate = shutil.which("apksigner.bat")
    if path_candidate:
        return Path(path_candidate)

    sdk_roots = []
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(name)
        if value:
            sdk_roots.append(Path(value))

    stable_candidates: list[tuple[tuple[int, ...], Path]] = []
    preview_candidates: list[tuple[tuple[int, ...], str, Path]] = []
    fallback_candidates: list[Path] = []
    for sdk_root in sdk_roots:
        build_tools_dir = sdk_root / "build-tools"
        if not build_tools_dir.is_dir():
            continue
        for version_dir in build_tools_dir.iterdir():
            candidate = resolve_apksigner_in_build_tools_dir(version_dir)
            if candidate is None:
                continue
            fallback_candidates.append(candidate)
            stable_version = parse_stable_build_tools_version(version_dir.name)
            if stable_version is not None:
                stable_candidates.append((stable_version, candidate))
                continue
            preview_version = parse_preview_build_tools_version(version_dir.name)
            if preview_version is not None:
                preview_candidates.append(
                    (preview_version, version_dir.name, candidate)
                )

    if stable_candidates:
        return max(stable_candidates, key=lambda item: item[0])[1]
    if preview_candidates:
        return max(preview_candidates, key=lambda item: (item[0], item[1]))[2]
    if fallback_candidates:
        return max(fallback_candidates, key=lambda path: path.parent.name)

    raise SystemExit(
        "apksigner not found on PATH and no Android SDK build-tools apksigner was "
        "discovered from ANDROID_HOME or ANDROID_SDK_ROOT"
    )


def parse_version_tuple(raw_value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw_value.split("."))


def parse_stable_build_tools_version(raw_value: str) -> tuple[int, ...] | None:
    if not STABLE_BUILD_TOOLS_PATTERN.fullmatch(raw_value):
        return None
    return parse_version_tuple(raw_value)


def parse_preview_build_tools_version(raw_value: str) -> tuple[int, ...] | None:
    match = PREVIEW_BUILD_TOOLS_PATTERN.fullmatch(raw_value)
    if match is None:
        return None
    return parse_version_tuple(match.group(1))


def resolve_apksigner_in_build_tools_dir(version_dir: Path) -> Path | None:
    for file_name in APKSIGNER_CANDIDATE_NAMES:
        candidate = version_dir / file_name
        if candidate.is_file():
            return candidate
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def verify_checksums_file(paths: ReleasePaths) -> tuple[list[str], list[str]]:
    checksum_lines = paths.checksums_path.read_text(encoding="utf-8").splitlines()
    verification_lines: list[str] = []
    failed_files: list[str] = []

    if not checksum_lines:
        raise SystemExit(
            f"checksum file {relative_to_repo(paths.checksums_path, paths.repo_root)} is empty"
        )

    for line in checksum_lines:
        if not line.strip():
            continue
        expected_digest, file_name = parse_checksum_line(line, paths)
        target_path = paths.stage_dir / file_name
        ensure_file(
            target_path,
            f"checksummed file {file_name}",
            repo_root=paths.repo_root,
        )
        actual_digest = sha256_file(target_path)
        if actual_digest == expected_digest:
            verification_lines.append(f"{file_name}: OK")
        else:
            verification_lines.append(f"{file_name}: FAILED")
            failed_files.append(file_name)

    if not verification_lines:
        raise SystemExit(
            f"checksum file {relative_to_repo(paths.checksums_path, paths.repo_root)} has no usable entries"
        )

    if failed_files:
        verification_lines.append(
            "sha256sum: WARNING: "
            f"{len(failed_files)} computed checksum"
            f"{'' if len(failed_files) == 1 else 's'} did NOT match"
        )

    return verification_lines, failed_files


def parse_checksum_line(line: str, paths: ReleasePaths) -> tuple[str, str]:
    match = CHECKSUM_LINE_PATTERN.fullmatch(line)
    if match is None:
        raise SystemExit(
            "unsupported checksum line format in "
            f"{relative_to_repo(paths.checksums_path, paths.repo_root)}: {line!r}"
        )
    return match.group(1).lower(), match.group(3)


def ensure_staged_apk(paths: ReleasePaths) -> None:
    ensure_file(
        paths.staged_apk_path,
        "staged public release APK",
        repo_root=paths.repo_root,
    )


def ensure_file(path: Path, description: str, repo_root: Path | None = None) -> None:
    if not path.is_file():
        location = str(path)
        if repo_root is not None:
            location = relative_to_repo_or_abs(path, repo_root)
        raise SystemExit(f"{description} not found at {location}")


def write_command_output(path: Path, output: str) -> None:
    path.write_text(output, encoding="utf-8")


def relative_to_repo(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def relative_to_repo_or_abs(path: Path, repo_root: Path) -> str:
    try:
        return relative_to_repo(path, repo_root)
    except Exception:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
