from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.release.version_lockstep import (
    derive_android_version_code,
    parse_canonical_version_text,
)

BUILD_LOG_NAME = "build.txt"
INSTALL_LOG_NAME = "local-install.txt"
MANIFEST_NAME = "artifact-manifest.json"
PUBLISH_DRY_RUN_LOG_NAME = "publish-dry-run.txt"
PUBLISH_ORDER_LOG_NAME = "publish-order.txt"
TWINE_CHECK_LOG_NAME = "twine-check.txt"
PACKAGED_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"
PACKAGED_APK_RESOURCE_DIR = Path("androidctl/src/androidctl/resources")
PACKAGED_APK_RESOURCE_GLOB = "androidctl-agent-*-release.apk"


@dataclass(frozen=True)
class PackageSpec:
    project_dir: Path
    distribution_name: str

    @property
    def normalized_distribution_name(self) -> str:
        return self.distribution_name.replace("-", "_")


@dataclass(frozen=True)
class PackageArtifacts:
    spec: PackageSpec
    sdist_path: Path
    wheel_path: Path


@dataclass(frozen=True)
class PackagedApkEvidence:
    resource_name: str
    source_path: Path
    source_sha256: str
    gradle_metadata_path: Path
    gradle_output_path: Path
    gradle_output_sha256: str
    version_name: str
    version_code: int
    sdist_member: str
    sdist_sha256: str
    wheel_member: str
    wheel_sha256: str


@dataclass(frozen=True)
class GradleApkEvidence:
    metadata_path: Path
    output_path: Path
    output_sha256: str
    version_name: str
    version_code: int


@dataclass(frozen=True)
class ReleasePaths:
    repo_root: Path
    version: str
    stage_dir: Path
    wheelhouse_dir: Path
    smoke_env_dir: Path
    build_log_path: Path
    twine_check_log_path: Path
    install_log_path: Path
    publish_order_log_path: Path
    publish_dry_run_log_path: Path
    manifest_path: Path
    packaged_apk_source_path: Path
    gradle_apk_metadata_path: Path
    packaged_apk_resource_dir: Path
    packaged_apk_resource_path: Path


PACKAGE_SPECS = (
    PackageSpec(Path("contracts"), "androidctl-contracts"),
    PackageSpec(Path("androidctld"), "androidctld"),
    PackageSpec(Path("androidctl"), "androidctl"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build, validate, and stage local PyPI release artifacts."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root path. Defaults to the parent of tools/release/.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build", help="Clean package dist/ directories and build.")
    subparsers.add_parser(
        "check",
        help="Run twine check for all package artifacts and copy them into wheelhouse.",
    )
    subparsers.add_parser(
        "install",
        help="Install the staged androidctl entry wheel into a throwaway clean venv.",
    )
    subparsers.add_parser(
        "publish-dry-run",
        help="Validate and record the intended publish order without uploading.",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    paths = build_release_paths(repo_root)

    if args.command == "build":
        build_distributions(paths)
        print(
            f"built distributions under: {relative_to_repo(paths.stage_dir, repo_root)}"
        )
        return 0

    if args.command == "check":
        check_distributions(paths)
        print(
            f"wrote twine check log: {relative_to_repo(paths.twine_check_log_path, repo_root)}"
        )
        print(
            f"populated wheelhouse: {relative_to_repo(paths.wheelhouse_dir, repo_root)}"
        )
        return 0

    if args.command == "install":
        install_from_wheelhouse(paths)
        print(
            f"wrote local install log: {relative_to_repo(paths.install_log_path, repo_root)}"
        )
        return 0

    if args.command == "publish-dry-run":
        render_publish_evidence(paths)
        print(
            f"wrote publish order evidence: {relative_to_repo(paths.publish_order_log_path, repo_root)}"
        )
        print(
            "wrote publish dry-run evidence: "
            f"{relative_to_repo(paths.publish_dry_run_log_path, repo_root)}"
        )
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


def build_release_paths(repo_root: Path) -> ReleasePaths:
    version_text = (repo_root / "VERSION").read_text(encoding="utf-8")
    version = parse_canonical_version_text(version_text)
    stage_dir = repo_root / "dist" / "release" / "pypi" / version
    return ReleasePaths(
        repo_root=repo_root,
        version=version,
        stage_dir=stage_dir,
        wheelhouse_dir=stage_dir / "wheelhouse",
        smoke_env_dir=stage_dir / ".venv-install-smoke",
        build_log_path=stage_dir / BUILD_LOG_NAME,
        twine_check_log_path=stage_dir / TWINE_CHECK_LOG_NAME,
        install_log_path=stage_dir / INSTALL_LOG_NAME,
        publish_order_log_path=stage_dir / PUBLISH_ORDER_LOG_NAME,
        publish_dry_run_log_path=stage_dir / PUBLISH_DRY_RUN_LOG_NAME,
        manifest_path=stage_dir / MANIFEST_NAME,
        packaged_apk_source_path=repo_root
        / "dist"
        / "release"
        / "android"
        / version
        / PACKAGED_APK_TEMPLATE.format(version=version),
        gradle_apk_metadata_path=repo_root
        / "android"
        / "app"
        / "build"
        / "outputs"
        / "apk"
        / "release"
        / "output-metadata.json",
        packaged_apk_resource_dir=repo_root / PACKAGED_APK_RESOURCE_DIR,
        packaged_apk_resource_path=repo_root
        / PACKAGED_APK_RESOURCE_DIR
        / PACKAGED_APK_TEMPLATE.format(version=version),
    )


def build_distributions(paths: ReleasePaths) -> None:
    shutil.rmtree(paths.stage_dir, ignore_errors=True)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    clean_packaged_agent_apks(paths)
    try:
        for spec in PACKAGE_SPECS:
            shutil.rmtree(
                paths.repo_root / spec.project_dir / "dist", ignore_errors=True
            )
            if spec.distribution_name == "androidctl":
                stage_packaged_agent_apk(paths)
            run_logged(
                [sys.executable, "-m", "build"],
                cwd=paths.repo_root / spec.project_dir,
                log_path=paths.build_log_path,
                title=f"build {spec.distribution_name}",
                repo_root=paths.repo_root,
            )
    finally:
        clean_packaged_agent_apks(paths)
    artifacts = collect_project_artifacts(paths)
    write_manifest(
        paths,
        artifacts,
        packaged_apk=inspect_packaged_agent_apk(paths, artifacts),
    )


def check_distributions(paths: ReleasePaths) -> None:
    clear_manifest(paths)
    artifacts = collect_project_artifacts(paths)
    if paths.twine_check_log_path.exists():
        paths.twine_check_log_path.unlink()
    shutil.rmtree(paths.wheelhouse_dir, ignore_errors=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    for artifact in artifacts:
        run_logged(
            [
                sys.executable,
                "-m",
                "twine",
                "check",
                artifact.sdist_path.name,
                artifact.wheel_path.name,
            ],
            cwd=artifact.sdist_path.parent,
            log_path=paths.twine_check_log_path,
            title=f"twine check {artifact.spec.distribution_name}",
            repo_root=paths.repo_root,
        )
        shutil.copy2(
            artifact.sdist_path, paths.wheelhouse_dir / artifact.sdist_path.name
        )
        shutil.copy2(
            artifact.wheel_path, paths.wheelhouse_dir / artifact.wheel_path.name
        )

    write_manifest(
        paths,
        artifacts,
        packaged_apk=inspect_packaged_agent_apk(paths, artifacts),
    )


def install_from_wheelhouse(paths: ReleasePaths) -> None:
    clear_manifest(paths)
    artifacts = collect_wheelhouse_artifacts(paths)
    packaged_apk = inspect_packaged_agent_apk(paths, artifacts)
    write_manifest(paths, artifacts, packaged_apk=packaged_apk)
    if paths.install_log_path.exists():
        paths.install_log_path.unlink()

    smoke_env_prefix = relative_to_repo(paths.smoke_env_dir, paths.repo_root)
    wheelhouse_dir = relative_to_repo(paths.wheelhouse_dir, paths.repo_root)
    androidctl_wheel = next(
        artifact.wheel_path
        for artifact in artifacts
        if artifact.spec.distribution_name == "androidctl"
    )
    wheel_args = [relative_to_repo(androidctl_wheel, paths.repo_root)]

    shutil.rmtree(paths.smoke_env_dir, ignore_errors=True)
    run_logged(
        [
            "python",
            "-m",
            "venv",
            smoke_env_prefix,
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="create throwaway venv",
        repo_root=paths.repo_root,
    )

    smoke_env_python = smoke_env_executable(paths, "python")
    run_logged(
        [
            smoke_env_python,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--retries",
            "5",
            "--timeout",
            "60",
            "--find-links",
            wheelhouse_dir,
            *wheel_args,
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="install androidctl entry wheel from local wheelhouse",
        repo_root=paths.repo_root,
    )

    run_logged(
        [
            smoke_env_python,
            "-c",
            build_installed_version_assertion_script(paths.version),
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="verify installed project versions",
        repo_root=paths.repo_root,
    )

    run_logged(
        [
            smoke_env_python,
            "-c",
            build_packaged_apk_smoke_script(paths.version),
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="verify packaged Android APK resource",
        repo_root=paths.repo_root,
    )

    run_logged(
        [
            smoke_env_executable(paths, "androidctl"),
            "--help",
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="smoke androidctl console script",
        repo_root=paths.repo_root,
    )

    run_logged(
        [
            smoke_env_executable(paths, "androidctld"),
            "--help",
        ],
        cwd=paths.repo_root,
        log_path=paths.install_log_path,
        title="smoke androidctld console script",
        repo_root=paths.repo_root,
    )

    append_text(
        paths.install_log_path,
        "\nthrowaway venv cleanup: removed after successful install check\n",
    )
    shutil.rmtree(paths.smoke_env_dir, ignore_errors=True)


def smoke_env_executable(paths: ReleasePaths, name: str) -> str:
    if sys.platform == "win32":
        executable_name = "python.exe" if name == "python" else f"{name}.exe"
        executable_path = paths.smoke_env_dir / "Scripts" / executable_name
    else:
        executable_path = paths.smoke_env_dir / "bin" / name
    return lexical_relative_to_repo(executable_path, paths.repo_root)


def render_publish_evidence(paths: ReleasePaths) -> None:
    clear_manifest(paths)
    artifacts = collect_wheelhouse_artifacts(paths)
    packaged_apk = inspect_packaged_agent_apk(paths, artifacts)
    order_lines = [
        f"{index}. {artifact.spec.distribution_name}"
        for index, artifact in enumerate(artifacts, start=1)
    ]
    paths.publish_order_log_path.write_text(
        "\n".join(order_lines) + "\n",
        encoding="utf-8",
    )

    lines = [
        "PyPI publish dry-run only. No upload API was called.",
        "Preferred future normal path: GitHub Actions trusted publishing.",
        "Break-glass token publishing remains documented fallback only and was not used.",
        "APK policy: Android Device Agent APK is embedded in the androidctl "
        "sdist/wheel; standalone Android release assets are not uploaded to PyPI.",
        "Packaged APK evidence: "
        f"{packaged_apk.resource_name}; versionName={packaged_apk.version_name}; "
        f"versionCode={packaged_apk.version_code}; "
        f"sha256={packaged_apk.source_sha256}.",
        "",
        "Intended publish order:",
    ]
    for index, artifact in enumerate(artifacts, start=1):
        lines.extend(
            [
                f"{index}. {artifact.spec.distribution_name}",
                f"   sdist: {relative_to_repo(artifact.sdist_path, paths.repo_root)}",
                f"   wheel: {relative_to_repo(artifact.wheel_path, paths.repo_root)}",
            ]
        )
    paths.publish_dry_run_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_manifest(
        paths,
        artifacts,
        packaged_apk=packaged_apk,
    )


def build_installed_version_assertion_script(expected_version: str) -> str:
    distributions = [spec.distribution_name for spec in PACKAGE_SPECS]
    lines = [
        "from importlib.metadata import PackageNotFoundError, version",
        f"expected = {expected_version!r}",
        f"distributions = {distributions!r}",
        "mismatches = []",
        "missing = []",
        "for distribution in distributions:",
        "    try:",
        "        installed = version(distribution)",
        "    except PackageNotFoundError:",
        "        installed = '<missing>'",
        "        status = 'MISSING'",
        "        missing.append(distribution)",
        "        print(",
        "            f'{distribution}: expected={expected} installed={installed} status={status}',",
        "            flush=True,",
        "        )",
        "        continue",
        "    except Exception:",
        "        installed = '<missing>'",
        "        status = 'MISSING'",
        "        missing.append(distribution)",
        "        print(",
        "            f'{distribution}: expected={expected} installed={installed} status={status}',",
        "            flush=True,",
        "        )",
        "        continue",
        "    status = 'OK' if installed == expected else 'MISMATCH'",
        "    print(",
        "        f'{distribution}: expected={expected} installed={installed} status={status}',",
        "        flush=True,",
        "    )",
        "    if installed != expected:",
        "        mismatches.append(distribution)",
        "failures = []",
        "if mismatches:",
        "    failures.append('mismatch: ' + ', '.join(mismatches))",
        "if missing:",
        "    failures.append('missing: ' + ', '.join(missing))",
        "if failures:",
        "    raise SystemExit('installed version check failed; ' + '; '.join(failures))",
    ]
    return "\n".join(lines) + "\n"


def build_packaged_apk_smoke_script(expected_version: str) -> str:
    lines = [
        "from androidctl import __version__ as androidctl_version",
        (
            "from androidctl.setup.apk_resource import "
            "packaged_agent_apk_name, packaged_agent_apk_path"
        ),
        f"expected = {expected_version!r}",
        "if androidctl_version != expected:",
        "    raise SystemExit(",
        "        f'androidctl version mismatch: expected={expected} installed={androidctl_version}'",
        "    )",
        "expected_name = packaged_agent_apk_name(expected)",
        "with packaged_agent_apk_path(expected) as apk_path:",
        "    if apk_path.name != expected_name:",
        "        raise SystemExit(",
        "            f'packaged APK name mismatch: expected={expected_name} got={apk_path.name}'",
        "        )",
        "    if not apk_path.is_file():",
        "        raise SystemExit(f'packaged APK is not a file: {apk_path}')",
        "    size = apk_path.stat().st_size",
        "    if size <= 0:",
        "        raise SystemExit(f'packaged APK is empty: {apk_path}')",
        "    print(",
        "        f'packaged apk: name={apk_path.name} size={size} status=OK',",
        "        flush=True,",
        "    )",
    ]
    return "\n".join(lines) + "\n"


def collect_project_artifacts(paths: ReleasePaths) -> list[PackageArtifacts]:
    artifacts: list[PackageArtifacts] = []
    for spec in PACKAGE_SPECS:
        dist_dir = paths.repo_root / spec.project_dir / "dist"
        artifacts.append(resolve_artifacts_for_directory(paths, spec, dist_dir))
    return artifacts


def collect_wheelhouse_artifacts(paths: ReleasePaths) -> list[PackageArtifacts]:
    ensure_dir(paths.wheelhouse_dir, "wheelhouse", repo_root=paths.repo_root)
    artifacts: list[PackageArtifacts] = []
    for spec in PACKAGE_SPECS:
        artifacts.append(
            resolve_artifacts_for_directory(paths, spec, paths.wheelhouse_dir)
        )
    return artifacts


def resolve_artifacts_for_directory(
    paths: ReleasePaths,
    spec: PackageSpec,
    directory: Path,
) -> PackageArtifacts:
    ensure_dir(directory, "distribution directory", repo_root=paths.repo_root)
    prefix = f"{spec.normalized_distribution_name}-{paths.version}"
    sdist_matches = sorted(directory.glob(f"{prefix}.tar.gz"))
    wheel_matches = sorted(directory.glob(f"{prefix}-*.whl"))
    if len(sdist_matches) != 1:
        raise SystemExit(
            f"expected exactly one sdist for {spec.distribution_name} in "
            f"{relative_to_repo(directory, paths.repo_root)}, found {len(sdist_matches)}"
        )
    if len(wheel_matches) != 1:
        raise SystemExit(
            f"expected exactly one wheel for {spec.distribution_name} in "
            f"{relative_to_repo(directory, paths.repo_root)}, found {len(wheel_matches)}"
        )
    return PackageArtifacts(
        spec=spec,
        sdist_path=sdist_matches[0],
        wheel_path=wheel_matches[0],
    )


def write_manifest(
    paths: ReleasePaths,
    artifacts: list[PackageArtifacts],
    *,
    packaged_apk: PackagedApkEvidence | None = None,
) -> None:
    manifest = {
        "version": paths.version,
        "publish_order": [artifact.spec.distribution_name for artifact in artifacts],
        "packages": [
            {
                "distribution_name": artifact.spec.distribution_name,
                "project_dir": str(artifact.spec.project_dir),
                "sdist_path": relative_to_repo(artifact.sdist_path, paths.repo_root),
                "wheel_path": relative_to_repo(artifact.wheel_path, paths.repo_root),
                "wheelhouse_sdist_path": relative_if_exists(
                    paths.wheelhouse_dir / artifact.sdist_path.name,
                    paths.repo_root,
                ),
                "wheelhouse_wheel_path": relative_if_exists(
                    paths.wheelhouse_dir / artifact.wheel_path.name,
                    paths.repo_root,
                ),
            }
            for artifact in artifacts
        ],
    }
    if packaged_apk is not None:
        manifest["packaged_apk"] = {
            "resource_name": packaged_apk.resource_name,
            "source_path": relative_to_repo(packaged_apk.source_path, paths.repo_root),
            "source_sha256": packaged_apk.source_sha256,
            "gradle_metadata_path": relative_to_repo(
                packaged_apk.gradle_metadata_path,
                paths.repo_root,
            ),
            "gradle_output_path": relative_to_repo(
                packaged_apk.gradle_output_path,
                paths.repo_root,
            ),
            "gradle_output_sha256": packaged_apk.gradle_output_sha256,
            "version_name": packaged_apk.version_name,
            "version_code": packaged_apk.version_code,
            "sdist_member": packaged_apk.sdist_member,
            "sdist_sha256": packaged_apk.sdist_sha256,
            "wheel_member": packaged_apk.wheel_member,
            "wheel_sha256": packaged_apk.wheel_sha256,
        }
    paths.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clear_manifest(paths: ReleasePaths) -> None:
    if paths.manifest_path.exists():
        paths.manifest_path.unlink()


def stage_packaged_agent_apk(paths: ReleasePaths) -> Path:
    ensure_file(
        paths.packaged_apk_source_path,
        "staged Android release APK for Python package data",
        repo_root=paths.repo_root,
    )
    clean_packaged_agent_apks(paths)
    paths.packaged_apk_resource_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.packaged_apk_source_path, paths.packaged_apk_resource_path)
    return paths.packaged_apk_resource_path


def clean_packaged_agent_apks(paths: ReleasePaths) -> None:
    if not paths.packaged_apk_resource_dir.exists():
        return
    for apk_path in paths.packaged_apk_resource_dir.glob(PACKAGED_APK_RESOURCE_GLOB):
        apk_path.unlink()


def inspect_packaged_agent_apk(
    paths: ReleasePaths,
    artifacts: list[PackageArtifacts],
) -> PackagedApkEvidence:
    ensure_file(
        paths.packaged_apk_source_path,
        "staged Android release APK for Python package data",
        repo_root=paths.repo_root,
    )
    androidctl_artifact = next(
        artifact
        for artifact in artifacts
        if artifact.spec.distribution_name == "androidctl"
    )
    resource_name = PACKAGED_APK_TEMPLATE.format(version=paths.version)
    source_sha256 = sha256_file(paths.packaged_apk_source_path)
    gradle_evidence = inspect_gradle_apk_evidence(paths, source_sha256)
    try:
        sdist_member, sdist_bytes = read_packaged_apk_from_sdist(
            androidctl_artifact.sdist_path,
            resource_name,
        )
        wheel_member, wheel_bytes = read_packaged_apk_from_wheel(
            androidctl_artifact.wheel_path,
            resource_name,
        )
    except (BadZipFile, OSError, tarfile.TarError) as exc:
        raise SystemExit(f"failed to inspect packaged APK artifacts: {exc}") from exc
    sdist_sha256 = sha256_bytes(sdist_bytes)
    wheel_sha256 = sha256_bytes(wheel_bytes)
    if sdist_sha256 != source_sha256:
        raise SystemExit(
            "androidctl sdist packaged APK checksum mismatch: "
            f"expected {source_sha256}, got {sdist_sha256}"
        )
    if wheel_sha256 != source_sha256:
        raise SystemExit(
            "androidctl wheel packaged APK checksum mismatch: "
            f"expected {source_sha256}, got {wheel_sha256}"
        )
    return PackagedApkEvidence(
        resource_name=resource_name,
        source_path=paths.packaged_apk_source_path,
        source_sha256=source_sha256,
        gradle_metadata_path=gradle_evidence.metadata_path,
        gradle_output_path=gradle_evidence.output_path,
        gradle_output_sha256=gradle_evidence.output_sha256,
        version_name=gradle_evidence.version_name,
        version_code=gradle_evidence.version_code,
        sdist_member=sdist_member,
        sdist_sha256=sdist_sha256,
        wheel_member=wheel_member,
        wheel_sha256=wheel_sha256,
    )


def inspect_gradle_apk_evidence(
    paths: ReleasePaths,
    expected_sha256: str,
) -> GradleApkEvidence:
    ensure_file(
        paths.gradle_apk_metadata_path,
        "Gradle release APK metadata",
        repo_root=paths.repo_root,
    )
    try:
        metadata = json.loads(
            paths.gradle_apk_metadata_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise SystemExit(
            "failed to parse Gradle release APK metadata: "
            f"{relative_to_repo(paths.gradle_apk_metadata_path, paths.repo_root)}"
        ) from exc
    elements = metadata.get("elements")
    if not isinstance(elements, list) or len(elements) != 1:
        raise SystemExit(
            "expected exactly one release APK output in "
            f"{relative_to_repo(paths.gradle_apk_metadata_path, paths.repo_root)}"
        )
    element = elements[0]
    if not isinstance(element, dict):
        raise SystemExit("release APK metadata element must be an object")

    version_name = element.get("versionName")
    if version_name != paths.version:
        raise SystemExit(
            f"release APK metadata versionName mismatch: expected {paths.version}, "
            f"got {version_name!r}"
        )
    version_code = element.get("versionCode")
    expected_version_code = derive_android_version_code(paths.version)
    if type(version_code) is not int or version_code != expected_version_code:
        raise SystemExit(
            "release APK metadata versionCode mismatch: "
            f"expected {expected_version_code}, got {version_code!r}"
        )
    output_file = element.get("outputFile")
    if not isinstance(output_file, str) or not output_file:
        raise SystemExit(
            "release APK metadata did not expose a usable outputFile field"
        )
    output_file_path = Path(output_file)
    if output_file_path.is_absolute() or ".." in output_file_path.parts:
        raise SystemExit(
            f"release APK metadata outputFile must be relative: {output_file!r}"
        )
    output_path = paths.gradle_apk_metadata_path.parent / output_file_path
    ensure_file(output_path, "Gradle release APK", repo_root=paths.repo_root)
    output_sha256 = sha256_file(output_path)
    if output_sha256 != expected_sha256:
        raise SystemExit(
            "staged Android release APK checksum does not match Gradle output: "
            f"expected {output_sha256}, got {expected_sha256}"
        )
    return GradleApkEvidence(
        metadata_path=paths.gradle_apk_metadata_path,
        output_path=output_path,
        output_sha256=output_sha256,
        version_name=version_name,
        version_code=version_code,
    )


def read_packaged_apk_from_wheel(
    wheel_path: Path,
    resource_name: str,
) -> tuple[str, bytes]:
    expected_member = f"androidctl/resources/{resource_name}"
    with ZipFile(wheel_path) as wheel:
        matches = [name for name in wheel.namelist() if name == expected_member]
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one packaged APK {expected_member} in "
                f"{wheel_path.name}, found {len(matches)}"
            )
        member = matches[0]
        return member, wheel.read(member)


def read_packaged_apk_from_sdist(
    sdist_path: Path,
    resource_name: str,
) -> tuple[str, bytes]:
    expected_suffix = f"/src/androidctl/resources/{resource_name}"
    with tarfile.open(sdist_path, "r:gz") as sdist:
        matches = [
            member
            for member in sdist.getmembers()
            if member.isfile() and member.name.endswith(expected_suffix)
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one packaged APK ending with {expected_suffix} in "
                f"{sdist_path.name}, found {len(matches)}"
            )
        member = matches[0]
        extracted = sdist.extractfile(member)
        if extracted is None:
            raise SystemExit(f"unable to read packaged APK {member.name}")
        return member.name, extracted.read()


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    title: str,
    repo_root: Path,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    rendered_command = " ".join(
        shlex.quote(redact_repo_root(part, repo_root)) for part in command
    )
    section = [
        f"$ {rendered_command}",
        f"cwd: {relative_to_repo(cwd, repo_root)}",
        f"title: {title}",
        f"exit code: {result.returncode}",
        "",
        redact_repo_root(result.stdout, repo_root).rstrip(),
        "",
    ]
    append_text(log_path, "\n".join(section) + "\n")
    if result.returncode != 0:
        raise SystemExit(f"{title} failed; see {relative_to_repo(log_path, repo_root)}")
    return result.stdout


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_if_exists(path: Path, repo_root: Path) -> str | None:
    if not path.exists():
        return None
    return relative_to_repo(path, repo_root)


def redact_repo_root(text: str, repo_root: Path) -> str:
    return text.replace(str(repo_root.resolve()), "<repo-root>")


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


def ensure_dir(path: Path, description: str, *, repo_root: Path) -> None:
    if not path.is_dir():
        raise SystemExit(f"missing {description}: {relative_to_repo(path, repo_root)}")


def ensure_file(path: Path, description: str, *, repo_root: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"missing {description}: {relative_to_repo(path, repo_root)}")


def relative_to_repo(path: Path, repo_root: Path) -> str:
    return str(path.resolve().relative_to(repo_root.resolve()))


def lexical_relative_to_repo(path: Path, repo_root: Path) -> str:
    absolute_path = path if path.is_absolute() else repo_root / path
    return str(absolute_path.relative_to(repo_root))


if __name__ == "__main__":
    raise SystemExit(main())
