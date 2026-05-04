from __future__ import annotations

import argparse
import hashlib
import json
import os
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
BUILD_OUTPUT_DIR_NAME = "build-output"
INSTALL_LOG_NAME = "local-install.txt"
SDIST_SMOKE_ENV_DIR_NAME = ".venv-sdist-install-smoke"
TWINE_CHECK_LOG_NAME = "twine-check.txt"
PACKAGED_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"
PACKAGED_APK_RESOURCE_DIR = Path("androidctl/src/androidctl/resources")
PACKAGED_APK_RESOURCE_GLOB = "androidctl-agent-*-release.apk"
ANDROID_CHECKSUMS_NAME = "SHA256SUMS"
ANDROID_CHECKSUM_VERIFY_LOG_NAME = "sha256sum-check.txt"
ANDROID_APK_VERIFY_LOG_NAME = "apksigner-verify.txt"
FORBIDDEN_ARTIFACT_PREFIXES = ("androidctld", "androidctl_contracts")
REQUIRED_IMPORT_PACKAGE_PREFIXES = (
    "androidctl/",
    "androidctld/",
    "androidctl_contracts/",
)
LEGACY_STAGE_DIR_NAMES = ("wheelhouse", "sdist-rebuild")
LEGACY_STAGE_FILE_NAMES = (
    "artifact-manifest.json",
    "publish-order.txt",
    "publish-dry-run.txt",
    "pypi-availability.txt",
)


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
class AndroidReleaseEvidence:
    checksums_path: Path
    checksum_verify_log_path: Path
    apk_verify_log_path: Path


@dataclass(frozen=True)
class PackagedApkEvidence:
    resource_name: str
    source_path: Path
    source_sha256: str
    version_name: str
    version_code: int
    sdist_member: str
    wheel_member: str
    android_release: AndroidReleaseEvidence


@dataclass(frozen=True)
class GradleApkEvidence:
    metadata_path: Path
    output_path: Path
    version_name: str
    version_code: int


@dataclass(frozen=True)
class ReleasePaths:
    repo_root: Path
    version: str
    stage_dir: Path
    build_output_dir: Path
    smoke_env_dir: Path
    sdist_smoke_env_dir: Path
    build_log_path: Path
    twine_check_log_path: Path
    install_log_path: Path
    packaged_apk_source_path: Path
    android_checksums_path: Path
    android_checksum_verify_log_path: Path
    android_apk_verify_log_path: Path
    gradle_apk_metadata_path: Path
    packaged_apk_resource_dir: Path
    packaged_apk_resource_path: Path


PACKAGE_SPECS = (PackageSpec(Path("."), "androidctl"),)
ANDROIDCTL_SPEC = PACKAGE_SPECS[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate local PyPI release artifacts."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root path. Defaults to the parent of tools/release/.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="Build the single androidctl distribution.")
    subparsers.add_parser("check", help="Run twine check for the built artifacts.")
    subparsers.add_parser(
        "install",
        help="Install the built androidctl wheel into a throwaway clean venv.",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    paths = build_release_paths(repo_root)

    if args.command == "build":
        build_distributions(paths)
        print(
            f"built androidctl distribution under: "
            f"{relative_to_repo(paths.build_output_dir, repo_root)}"
        )
        return 0

    if args.command == "check":
        check_distributions(paths)
        print(
            f"wrote twine check log: "
            f"{relative_to_repo(paths.twine_check_log_path, repo_root)}"
        )
        return 0

    if args.command == "install":
        install_from_build_output(paths)
        print(
            f"wrote local install log: "
            f"{relative_to_repo(paths.install_log_path, repo_root)}"
        )
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


def build_release_paths(repo_root: Path) -> ReleasePaths:
    version_text = (repo_root / "VERSION").read_text(encoding="utf-8")
    version = parse_canonical_version_text(version_text)
    stage_dir = repo_root / "dist" / "release" / "pypi" / version
    android_stage_dir = repo_root / "dist" / "release" / "android" / version
    return ReleasePaths(
        repo_root=repo_root,
        version=version,
        stage_dir=stage_dir,
        build_output_dir=stage_dir / BUILD_OUTPUT_DIR_NAME,
        smoke_env_dir=stage_dir / ".venv-install-smoke",
        sdist_smoke_env_dir=stage_dir / SDIST_SMOKE_ENV_DIR_NAME,
        build_log_path=stage_dir / BUILD_LOG_NAME,
        twine_check_log_path=stage_dir / TWINE_CHECK_LOG_NAME,
        install_log_path=stage_dir / INSTALL_LOG_NAME,
        packaged_apk_source_path=android_stage_dir
        / PACKAGED_APK_TEMPLATE.format(version=version),
        android_checksums_path=android_stage_dir / ANDROID_CHECKSUMS_NAME,
        android_checksum_verify_log_path=android_stage_dir
        / ANDROID_CHECKSUM_VERIFY_LOG_NAME,
        android_apk_verify_log_path=android_stage_dir / ANDROID_APK_VERIFY_LOG_NAME,
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
    clean_pypi_build_stage(paths)
    clean_packaged_agent_apks(paths)
    assert_source_clean(paths, "pre-build source clean check")
    paths.build_output_dir.mkdir(parents=True, exist_ok=True)
    try:
        stage_packaged_agent_apk(paths)
        run_logged(
            [
                sys.executable,
                "-m",
                "build",
                "--outdir",
                lexical_relative_to_repo(paths.build_output_dir, paths.repo_root),
                ".",
            ],
            cwd=paths.repo_root,
            log_path=paths.build_log_path,
            title="build androidctl",
            repo_root=paths.repo_root,
        )
    finally:
        clean_packaged_agent_apks(paths)
        cleanup_root_build_metadata(paths)

    assert_source_clean(paths, "post-build source clean check")
    artifacts = collect_project_artifacts(paths)
    inspect_python_artifact_members(paths, artifacts)
    inspect_packaged_agent_apk(paths, artifacts)


def check_distributions(paths: ReleasePaths) -> None:
    artifacts = collect_project_artifacts(paths)
    inspect_python_artifact_members(paths, artifacts)
    inspect_packaged_agent_apk(paths, artifacts)
    if paths.twine_check_log_path.exists():
        paths.twine_check_log_path.unlink()

    artifact = artifacts[0]
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
        title="twine check androidctl",
        repo_root=paths.repo_root,
    )


def install_from_build_output(paths: ReleasePaths) -> None:
    artifacts = collect_project_artifacts(paths)
    inspect_python_artifact_members(paths, artifacts)
    packaged_apk = inspect_packaged_agent_apk(paths, artifacts)
    artifact = artifacts[0]
    if paths.install_log_path.exists():
        paths.install_log_path.unlink()

    run_install_smoke(
        paths,
        wheel_path=artifact.wheel_path,
        find_links_dir=paths.build_output_dir,
        expected_apk_sha256=packaged_apk.source_sha256,
    )
    run_sdist_install_smoke(
        paths,
        sdist_path=artifact.sdist_path,
        expected_apk_sha256=packaged_apk.source_sha256,
    )


def run_install_smoke(
    paths: ReleasePaths,
    *,
    wheel_path: Path,
    find_links_dir: Path,
    expected_apk_sha256: str,
) -> None:
    clean_env = minimal_subprocess_env()
    smoke_cwd = paths.stage_dir
    smoke_cwd.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(paths.smoke_env_dir, ignore_errors=True)
    run_logged(
        [
            sys.executable,
            "-m",
            "venv",
            absolute_path_text(paths.smoke_env_dir),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: create throwaway venv",
        repo_root=paths.repo_root,
        env=clean_env,
    )

    smoke_env_python = smoke_env_executable_absolute(paths, "python")
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
            find_links_dir.resolve().as_posix(),
            wheel_path.resolve().as_posix(),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: install androidctl wheel",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [
            smoke_env_python,
            "-c",
            build_installed_version_assertion_script(paths.version),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: verify installed metadata and runtime versions",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [
            smoke_env_python,
            "-c",
            build_packaged_apk_smoke_script(paths.version, expected_apk_sha256),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: verify packaged Android APK resource",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [smoke_env_executable_absolute(paths, "androidctl"), "--help"],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: smoke androidctl console script",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [smoke_env_executable_absolute(paths, "androidctld"), "--help"],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: smoke androidctld console script",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [smoke_env_python, "-m", "androidctld", "--help"],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="install smoke: smoke python -m androidctld",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    append_text(paths.install_log_path, "\ninstall smoke: cleanup after success\n")
    shutil.rmtree(paths.smoke_env_dir, ignore_errors=True)


def run_sdist_install_smoke(
    paths: ReleasePaths,
    *,
    sdist_path: Path,
    expected_apk_sha256: str,
) -> None:
    clean_env = minimal_subprocess_env()
    smoke_cwd = paths.stage_dir
    smoke_cwd.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(paths.sdist_smoke_env_dir, ignore_errors=True)
    run_logged(
        [
            sys.executable,
            "-m",
            "venv",
            absolute_path_text(paths.sdist_smoke_env_dir),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="sdist smoke: create throwaway venv",
        repo_root=paths.repo_root,
        env=clean_env,
    )

    sdist_smoke_python = sdist_smoke_env_executable_absolute(paths, "python")
    run_logged(
        [
            sdist_smoke_python,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--retries",
            "5",
            "--timeout",
            "60",
            sdist_path.resolve().as_posix(),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="sdist smoke: install androidctl sdist",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [
            sdist_smoke_python,
            "-c",
            build_installed_version_assertion_script(paths.version),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="sdist smoke: verify installed metadata and runtime versions",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    run_logged(
        [
            sdist_smoke_python,
            "-c",
            build_packaged_apk_smoke_script(paths.version, expected_apk_sha256),
        ],
        cwd=smoke_cwd,
        log_path=paths.install_log_path,
        title="sdist smoke: verify packaged Android APK resource",
        repo_root=paths.repo_root,
        env=clean_env,
    )
    append_text(paths.install_log_path, "\nsdist smoke: cleanup after success\n")
    shutil.rmtree(paths.sdist_smoke_env_dir, ignore_errors=True)


def smoke_env_executable(paths: ReleasePaths, name: str) -> str:
    return env_executable(paths.smoke_env_dir, name, paths.repo_root)


def smoke_env_executable_absolute(paths: ReleasePaths, name: str) -> str:
    return absolute_path_text(env_executable_path(paths.smoke_env_dir, name))


def sdist_smoke_env_executable_absolute(paths: ReleasePaths, name: str) -> str:
    return absolute_path_text(env_executable_path(paths.sdist_smoke_env_dir, name))


def env_executable(env_dir: Path, name: str, repo_root: Path) -> str:
    executable_path = env_executable_path(env_dir, name)
    return lexical_relative_to_repo(executable_path, repo_root)


def env_executable_path(env_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        executable_name = "python.exe" if name == "python" else f"{name}.exe"
        return env_dir / "Scripts" / executable_name
    return env_dir / "bin" / name


def absolute_path_text(path: Path) -> str:
    return Path(os.path.abspath(path)).as_posix()


def build_installed_version_assertion_script(expected_version: str) -> str:
    lines = [
        "from importlib import import_module",
        "from importlib.metadata import PackageNotFoundError, version",
        f"expected = {expected_version!r}",
        "failures = []",
        "",
        "def record(label, expected_value, actual_value, ok):",
        "    status = 'OK' if ok else 'FAIL'",
        "    print(",
        (
            "        f'{label}: expected={expected_value} "
            "actual={actual_value} status={status}',"
        ),
        "        flush=True,",
        "    )",
        "    if not ok:",
        "        failures.append(label)",
        "",
        "try:",
        "    androidctl_dist = version('androidctl')",
        "except PackageNotFoundError:",
        "    androidctl_dist = '<missing>'",
        "record(",
        "    'distribution androidctl',",
        "    expected,",
        "    androidctl_dist,",
        "    androidctl_dist == expected,",
        ")",
        "for distribution in ('androidctld', 'androidctl-contracts'):",
        "    try:",
        "        installed = version(distribution)",
        "    except PackageNotFoundError:",
        "        installed = '<missing>'",
        "    record(",
        "        f'distribution {distribution}',",
        "        '<missing>',",
        "        installed,",
        "        installed == '<missing>',",
        "    )",
        "runtime_modules = {",
        "    'androidctl': 'androidctl',",
        "    'androidctld': 'androidctld',",
        "    'androidctl_contracts': 'androidctl_contracts',",
        "}",
        "for label, module_name in runtime_modules.items():",
        "    module = import_module(module_name)",
        "    installed = getattr(module, '__version__', '<missing>')",
        "    record(f'runtime {label}', expected, installed, installed == expected)",
        "if failures:",
        (
            "    raise SystemExit('installed metadata/runtime check failed: ' "
            "+ ', '.join(failures))"
        ),
    ]
    return "\n".join(lines) + "\n"


def build_packaged_apk_smoke_script(
    expected_version: str,
    expected_sha256: str,
) -> str:
    lines = [
        "import hashlib",
        "from androidctl.setup.apk_resource import (",
        "    packaged_agent_apk_name,",
        "    packaged_agent_apk_path,",
        ")",
        f"expected = {expected_version!r}",
        f"expected_sha256 = {expected_sha256!r}",
        "expected_name = packaged_agent_apk_name(expected)",
        "with packaged_agent_apk_path(expected) as apk_path:",
        "    if apk_path.name != expected_name:",
        "        raise SystemExit(",
        (
            "            f'packaged APK name mismatch: expected={expected_name} "
            "got={apk_path.name}'"
        ),
        "        )",
        "    if not apk_path.is_file():",
        "        raise SystemExit(f'packaged APK is not a file: {apk_path}')",
        "    payload = apk_path.read_bytes()",
        "    if not payload:",
        "        raise SystemExit(f'packaged APK is empty: {apk_path}')",
        "    actual_sha256 = hashlib.sha256(payload).hexdigest()",
        "    if actual_sha256 != expected_sha256:",
        "        raise SystemExit(",
        (
            "            f'packaged APK sha256 mismatch: "
            "expected={expected_sha256} got={actual_sha256}'"
        ),
        "        )",
        "    print(",
        "        f'packaged apk: name={apk_path.name} size={len(payload)} '",
        "        f'sha256={actual_sha256} status=OK',",
        "        flush=True,",
        "    )",
    ]
    return "\n".join(lines) + "\n"


def collect_project_artifacts(paths: ReleasePaths) -> list[PackageArtifacts]:
    return [
        resolve_artifacts_for_directory(paths, ANDROIDCTL_SPEC, paths.build_output_dir)
    ]


def inspect_python_artifact_members(
    paths: ReleasePaths,
    artifacts: list[PackageArtifacts],
) -> None:
    for artifact in artifacts:
        inspect_wheel_members(paths, artifact.wheel_path)
        inspect_sdist_members(paths, artifact.sdist_path)


def inspect_wheel_members(paths: ReleasePaths, wheel_path: Path) -> None:
    try:
        with ZipFile(wheel_path) as wheel:
            names = wheel.namelist()
    except (BadZipFile, OSError) as exc:
        raise SystemExit(f"failed to inspect wheel members: {exc}") from exc
    expected_apk_member = (
        f"androidctl/resources/{PACKAGED_APK_TEMPLATE.format(version=paths.version)}"
    )
    apk_members = sorted(
        name
        for name in names
        if name.startswith("androidctl/resources/") and name.endswith(".apk")
    )
    if apk_members != [expected_apk_member]:
        raise SystemExit(
            f"wheel {wheel_path.name} APK resource mismatch: "
            f"expected {[expected_apk_member]!r}, got {apk_members!r}"
        )
    for package_prefix in REQUIRED_IMPORT_PACKAGE_PREFIXES:
        if not any(name.startswith(package_prefix) for name in names):
            raise SystemExit(
                f"wheel {wheel_path.name} is missing import package {package_prefix}"
            )
    top_level_dirs = {
        name.split("/", 1)[0]
        for name in names
        if "/" in name and not name.startswith("/")
    }
    expected_dist_info = f"androidctl-{paths.version}.dist-info"
    dist_info_dirs = sorted(
        name for name in top_level_dirs if name.endswith(".dist-info")
    )
    if dist_info_dirs != [expected_dist_info]:
        raise SystemExit(
            f"wheel {wheel_path.name} dist-info mismatch: "
            f"expected {[expected_dist_info]!r}, got {dist_info_dirs!r}"
        )
    egg_info_dirs = sorted(
        name for name in top_level_dirs if name.endswith(".egg-info")
    )
    if egg_info_dirs:
        raise SystemExit(
            f"wheel {wheel_path.name} must not contain egg-info: {egg_info_dirs!r}"
        )


def inspect_sdist_members(paths: ReleasePaths, sdist_path: Path) -> None:
    try:
        with tarfile.open(sdist_path, "r:gz") as sdist:
            names = sdist.getnames()
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit(f"failed to inspect sdist members: {exc}") from exc
    sdist_root = f"androidctl-{paths.version}/"
    expected_apk_member = (
        f"{sdist_root}androidctl/src/androidctl/resources/"
        f"{PACKAGED_APK_TEMPLATE.format(version=paths.version)}"
    )
    apk_members = sorted(
        name
        for name in names
        if name.startswith(f"{sdist_root}androidctl/src/androidctl/resources/")
        and name.endswith(".apk")
    )
    if apk_members != [expected_apk_member]:
        raise SystemExit(
            f"sdist {sdist_path.name} APK resource mismatch: "
            f"expected {[expected_apk_member]!r}, got {apk_members!r}"
        )
    required_source_prefixes = (
        "androidctl/src/androidctl/",
        "androidctld/src/androidctld/",
        "contracts/src/androidctl_contracts/",
    )
    for source_prefix in required_source_prefixes:
        expected_prefix = sdist_root + source_prefix
        if not any(name.startswith(expected_prefix) for name in names):
            raise SystemExit(
                f"sdist {sdist_path.name} is missing source package {source_prefix}"
            )

    metadata_dirs: set[str] = set()
    for name in names:
        if not name.startswith(sdist_root):
            continue
        relative_name = name.removeprefix(sdist_root)
        parts = relative_name.split("/")
        for index, part in enumerate(parts):
            if part.endswith((".egg-info", ".dist-info")):
                metadata_dirs.add("/".join(parts[: index + 1]))
    allowed_metadata_dirs = {"androidctl.egg-info"}
    unexpected_metadata_dirs = sorted(metadata_dirs - allowed_metadata_dirs)
    if unexpected_metadata_dirs:
        raise SystemExit(
            f"sdist {sdist_path.name} contains child metadata: "
            f"{unexpected_metadata_dirs!r}"
        )


def resolve_artifacts_for_directory(
    paths: ReleasePaths,
    spec: PackageSpec,
    directory: Path,
) -> PackageArtifacts:
    ensure_dir(directory, "distribution directory", repo_root=paths.repo_root)
    artifact_files = [
        path
        for path in directory.iterdir()
        if path.is_file() and (path.name.endswith(".tar.gz") or path.suffix == ".whl")
    ]
    forbidden = [
        path.name for path in artifact_files if artifact_name_is_forbidden(path.name)
    ]
    if forbidden:
        raise SystemExit(
            "forbidden split-package artifact(s) in "
            f"{relative_to_repo(directory, paths.repo_root)}: {', '.join(forbidden)}"
        )

    prefix = f"{spec.normalized_distribution_name}-{paths.version}"
    sdist_matches = sorted(directory.glob(f"{prefix}.tar.gz"))
    wheel_matches = sorted(directory.glob(f"{prefix}-*.whl"))
    directory_label = relative_to_repo(directory, paths.repo_root)
    if len(sdist_matches) != 1:
        raise SystemExit(
            f"expected exactly one sdist for {spec.distribution_name} in "
            f"{directory_label}, found {len(sdist_matches)}"
        )
    if len(wheel_matches) != 1:
        raise SystemExit(
            f"expected exactly one wheel for {spec.distribution_name} in "
            f"{directory_label}, found {len(wheel_matches)}"
        )
    expected_names = {sdist_matches[0].name, wheel_matches[0].name}
    extra_artifacts = [
        path.name for path in artifact_files if path.name not in expected_names
    ]
    if extra_artifacts:
        raise SystemExit(
            "unexpected artifact(s) in "
            f"{relative_to_repo(directory, paths.repo_root)}: "
            f"{', '.join(sorted(extra_artifacts))}"
        )
    return PackageArtifacts(
        spec=spec,
        sdist_path=sdist_matches[0],
        wheel_path=wheel_matches[0],
    )


def artifact_name_is_forbidden(name: str) -> bool:
    normalized = name.replace("-", "_")
    return any(
        normalized.startswith(f"{prefix}_") or normalized.startswith(f"{prefix}-")
        for prefix in FORBIDDEN_ARTIFACT_PREFIXES
    )


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


def cleanup_root_build_metadata(paths: ReleasePaths) -> None:
    shutil.rmtree(paths.repo_root / "build", ignore_errors=True)
    for egg_info_path in paths.repo_root.glob("*.egg-info"):
        shutil.rmtree(egg_info_path, ignore_errors=True)


def clean_pypi_build_stage(paths: ReleasePaths) -> None:
    shutil.rmtree(paths.build_output_dir, ignore_errors=True)
    shutil.rmtree(paths.smoke_env_dir, ignore_errors=True)
    shutil.rmtree(paths.sdist_smoke_env_dir, ignore_errors=True)
    for legacy_dir_name in LEGACY_STAGE_DIR_NAMES:
        shutil.rmtree(paths.stage_dir / legacy_dir_name, ignore_errors=True)
    for log_path in (
        paths.build_log_path,
        paths.twine_check_log_path,
        paths.install_log_path,
    ):
        if log_path.exists():
            log_path.unlink()
    for legacy_file_name in LEGACY_STAGE_FILE_NAMES:
        legacy_file_path = paths.stage_dir / legacy_file_name
        if legacy_file_path.exists():
            legacy_file_path.unlink()
    paths.stage_dir.mkdir(parents=True, exist_ok=True)


def inspect_packaged_agent_apk(
    paths: ReleasePaths,
    artifacts: list[PackageArtifacts],
) -> PackagedApkEvidence:
    ensure_file(
        paths.packaged_apk_source_path,
        "staged Android release APK for Python package data",
        repo_root=paths.repo_root,
    )
    androidctl_artifact = artifacts[0]
    resource_name = PACKAGED_APK_TEMPLATE.format(version=paths.version)
    source_sha256 = sha256_file(paths.packaged_apk_source_path)
    gradle_evidence = inspect_gradle_apk_evidence(paths, source_sha256)
    android_release = inspect_android_release_evidence(paths, source_sha256)
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
        version_name=gradle_evidence.version_name,
        version_code=gradle_evidence.version_code,
        sdist_member=sdist_member,
        wheel_member=wheel_member,
        android_release=android_release,
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
        version_name=version_name,
        version_code=version_code,
    )


def inspect_android_release_evidence(
    paths: ReleasePaths,
    expected_sha256: str,
) -> AndroidReleaseEvidence:
    ensure_file(
        paths.android_checksums_path, "Android SHA256SUMS", repo_root=paths.repo_root
    )
    ensure_file(
        paths.android_checksum_verify_log_path,
        "Android checksum verification log",
        repo_root=paths.repo_root,
    )
    ensure_file(
        paths.android_apk_verify_log_path,
        "Android APK signature verification log",
        repo_root=paths.repo_root,
    )
    checksum_lines = [
        line.strip()
        for line in paths.android_checksums_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    if len(checksum_lines) != 1:
        raise SystemExit(
            "expected exactly one Android checksum line in "
            f"{relative_to_repo(paths.android_checksums_path, paths.repo_root)}"
        )
    checksum_parts = checksum_lines[0].split()
    if len(checksum_parts) != 2:
        raise SystemExit("Android SHA256SUMS line must contain digest and filename")
    checksum_sha256, checksum_name = checksum_parts
    expected_name = PACKAGED_APK_TEMPLATE.format(version=paths.version)
    if checksum_name != expected_name:
        raise SystemExit(
            f"Android SHA256SUMS filename mismatch: expected {expected_name}, "
            f"got {checksum_name}"
        )
    if checksum_sha256 != expected_sha256:
        raise SystemExit(
            "Android SHA256SUMS digest mismatch: "
            f"expected {expected_sha256}, got {checksum_sha256}"
        )
    checksum_log_text = paths.android_checksum_verify_log_path.read_text(
        encoding="utf-8"
    )
    if expected_name not in checksum_log_text:
        raise SystemExit(
            "Android checksum verification log does not mention " f"{expected_name}"
        )
    if expected_sha256 not in checksum_log_text:
        raise SystemExit(
            "Android checksum verification log does not mention "
            f"APK sha256 {expected_sha256}"
        )
    expected_checksum_ok_line = f"{expected_name}: OK"
    if expected_checksum_ok_line not in checksum_log_text.splitlines():
        raise SystemExit(
            "Android checksum verification log does not confirm checksum success "
            f"for {expected_name}"
        )
    apk_log_text = paths.android_apk_verify_log_path.read_text(encoding="utf-8")
    if expected_name not in apk_log_text:
        raise SystemExit(
            "Android APK signature verification log does not mention "
            f"{expected_name}"
        )
    if expected_sha256 not in apk_log_text:
        raise SystemExit(
            "Android APK signature verification log does not mention "
            f"APK sha256 {expected_sha256}"
        )
    return AndroidReleaseEvidence(
        checksums_path=paths.android_checksums_path,
        checksum_verify_log_path=paths.android_checksum_verify_log_path,
        apk_verify_log_path=paths.android_apk_verify_log_path,
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
    expected_suffix = f"/androidctl/src/androidctl/resources/{resource_name}"
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
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
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


def minimal_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def assert_source_clean(paths: ReleasePaths, title: str) -> dict[str, str]:
    git_dir = paths.repo_root / ".git"
    if not git_dir.exists():
        return {"status": "not-git", "title": title}
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=paths.repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{title} failed: git status exited {result.returncode}")
    if result.stdout.strip():
        raise SystemExit(
            f"{title} failed; source tree is not clean:\n{result.stdout.rstrip()}"
        )
    return {"status": "clean", "title": title}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
