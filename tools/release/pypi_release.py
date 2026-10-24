from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from tools.release import android_release


def relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return path.absolute().relative_to(repo_root.absolute()).as_posix()
    except ValueError:
        return str(path)


def remove_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


@dataclass(frozen=True)
class ReleasePaths:
    android: android_release.ReleasePaths
    stage_dir: Path
    build_output_dir: Path
    build_log_path: Path
    twine_check_log_path: Path
    install_log_path: Path


@dataclass(frozen=True)
class PackageArtifacts:
    wheel_path: Path
    sdist_path: Path


def build_release_paths(repo_root: Path) -> ReleasePaths:
    android = android_release.build_release_paths(repo_root)
    stage = repo_root / "dist/release/pypi" / android.version
    return ReleasePaths(
        android=android,
        stage_dir=stage,
        build_output_dir=stage / "build-output",
        build_log_path=stage / "build.txt",
        twine_check_log_path=stage / "twine-check.txt",
        install_log_path=stage / "local-install.txt",
    )


def build_distributions(paths: ReleasePaths) -> tuple[PackageArtifacts, str]:
    repo_root = paths.android.repo_root
    android_release.assert_source_clean(repo_root, "pre-build")
    digest = android_release.verify_release(paths.android)
    remove_directory(paths.build_output_dir)
    paths.build_output_dir.mkdir(parents=True)
    for path in (
        paths.build_log_path,
        paths.twine_check_log_path,
        paths.install_log_path,
    ):
        path.unlink(missing_ok=True)
    with TemporaryDirectory(prefix="source-", dir=paths.stage_dir) as temporary:
        candidate_source = Path(temporary)
        for name in ("pyproject.toml", "VERSION", "README.md", "LICENSE"):
            shutil.copy2(repo_root / name, candidate_source / name)
        for name in ("androidctl/src/androidctl", "skills/androidctl"):
            shutil.copytree(
                repo_root / name,
                candidate_source / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        candidate_resources = candidate_source / "androidctl/src/androidctl/resources"
        for path in candidate_resources.glob("androidctl-agent-*-release.apk"):
            path.unlink()
        shutil.copy2(paths.android.staged_apk_path, candidate_resources)
        # With no format flags, build creates the sdist and builds its wheel.
        run_logged(
            [
                sys.executable,
                "-m",
                "build",
                "--outdir",
                str(paths.build_output_dir),
                ".",
            ],
            cwd=candidate_source,
            log_path=paths.build_log_path,
            title="build androidctl",
        )
    artifacts = collect_artifacts(paths)
    inspect_artifacts(paths, artifacts, digest)
    return artifacts, digest


def collect_artifacts(paths: ReleasePaths) -> PackageArtifacts:
    files = [
        path
        for path in paths.build_output_dir.iterdir()
        if path.is_file() and path.name.endswith((".whl", ".tar.gz"))
    ]
    prefix = f"androidctl-{paths.android.version}"
    wheels = [
        path
        for path in files
        if path.name.startswith(prefix + "-") and path.suffix == ".whl"
    ]
    sdists = [path for path in files if path.name == prefix + ".tar.gz"]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            "Expected exactly one androidctl wheel and sdist, "
            "with no extra distributions"
        )
    return PackageArtifacts(wheels[0], sdists[0])


def check_metadata(payload: bytes, version: str, source: str) -> None:
    metadata = BytesParser().parsebytes(payload)
    if metadata.get("Name") != "androidctl" or metadata.get("Version") != version:
        raise SystemExit(
            f"{source} metadata mismatch: expected androidctl {version}, "
            f"got {metadata.get('Name')!r} {metadata.get('Version')!r}"
        )


def check_apk_members(names: list[str], expected: str) -> None:
    apks = [name for name in names if name.endswith(".apk")]
    if apks != [expected]:
        raise SystemExit(f"Expected sole APK resource {expected}, got {apks!r}")


def inspect_artifacts(
    paths: ReleasePaths, artifacts: PackageArtifacts, digest: str
) -> None:
    version = paths.android.version
    resource = paths.android.staged_apk_path.name
    with ZipFile(artifacts.wheel_path) as wheel:
        names = wheel.namelist()
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise SystemExit("Wheel must contain metadata for exactly one distribution")
        check_metadata(wheel.read(metadata[0]), version, "wheel")
        member = f"androidctl/resources/{resource}"
        check_apk_members(names, member)
        if hashlib.sha256(wheel.read(member)).hexdigest() != digest:
            raise SystemExit("Wheel APK bytes differ from the verified staged APK")
    with tarfile.open(artifacts.sdist_path, "r:gz") as sdist:
        names = sdist.getnames()
        root = f"androidctl-{version}"
        root_metadata = f"{root}/PKG-INFO"
        if names.count(root_metadata) != 1:
            raise SystemExit("Sdist must contain one project PKG-INFO")
        project_metadata = [
            name
            for name in names
            if name.endswith((".egg-info/PKG-INFO", ".dist-info/METADATA"))
        ]
        if len(project_metadata) > 1:
            raise SystemExit("Sdist contains metadata for multiple distributions")
        for name in [root_metadata, *project_metadata]:
            check_metadata(read_sdist_member(sdist, name), version, "sdist")
        member = f"{root}/androidctl/src/androidctl/resources/{resource}"
        check_apk_members(names, member)
        if hashlib.sha256(read_sdist_member(sdist, member)).hexdigest() != digest:
            raise SystemExit("Sdist APK bytes differ from the verified staged APK")


def read_sdist_member(sdist: tarfile.TarFile, name: str) -> bytes:
    content = sdist.extractfile(name)
    if content is None:
        raise SystemExit(f"Sdist member is not a file: {name}")
    with content:
        return content.read()


def verified_artifacts(paths: ReleasePaths) -> tuple[PackageArtifacts, str]:
    digest = android_release.verify_release(paths.android)
    artifacts = collect_artifacts(paths)
    inspect_artifacts(paths, artifacts, digest)
    return artifacts, digest


def check_distributions(paths: ReleasePaths, artifacts: PackageArtifacts) -> None:
    paths.twine_check_log_path.unlink(missing_ok=True)
    run_logged(
        [
            sys.executable,
            "-m",
            "twine",
            "check",
            str(artifacts.wheel_path),
            str(artifacts.sdist_path),
        ],
        cwd=paths.stage_dir,
        log_path=paths.twine_check_log_path,
        title="twine check",
    )


def env_executable(env_dir: Path, name: str) -> str:
    # Resolving the POSIX Python symlink would bypass this venv.
    path = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
    return str((path / (name + (".exe" if sys.platform == "win32" else ""))).absolute())


def minimal_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def run_install_smoke(
    paths: ReleasePaths, wheel_path: Path, expected_apk_sha256: str
) -> None:
    paths.install_log_path.unlink(missing_ok=True)
    env_dir = paths.stage_dir / ".venv-wheel-install-smoke"
    remove_directory(env_dir)
    env = minimal_subprocess_env()
    python = env_executable(env_dir, "python")
    expected_version = paths.android.version
    skill_source = (paths.android.repo_root / "skills/androidctl").absolute()
    commands = [
        ("create venv", [sys.executable, "-m", "venv", str(env_dir)]),
        (
            "install",
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--timeout",
                "60",
                str(wheel_path),
            ],
        ),
        (
            "verify installed package",
            [
                python,
                str(Path(__file__).with_name("installed_smoke.py").absolute()),
                expected_version,
                expected_apk_sha256,
                str(skill_source),
            ],
        ),
    ]
    for title, command in commands:
        run_logged(
            command,
            cwd=paths.stage_dir,
            log_path=paths.install_log_path,
            title=title,
            env=env,
        )
    remove_directory(env_dir)


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    title: str,
    env: dict[str, str] | None = None,
) -> None:
    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            f"$ {shlex.join(command)}\ncwd: {cwd}\ntitle: {title}\n"
            f"exit code: {result.returncode}\n{result.stdout}\n"
        )
    if result.returncode:
        raise SystemExit(f"{title} failed; see {log_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and verify the local androidctl distribution."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "build", help="Build wheel/sdist with the verified staged Android APK."
    )
    commands.add_parser("check", help="Verify artifacts and run twine check.")
    commands.add_parser(
        "install", help="Verify artifacts and smoke a clean wheel install."
    )
    commands.add_parser(
        "prepare",
        help="Stage the Gradle APK, then build, check and smoke the same candidate.",
    )
    args = parser.parse_args(argv)
    paths = build_release_paths(args.repo_root.resolve())
    if args.command == "build":
        build_distributions(paths)
    elif args.command == "check":
        artifacts, _ = verified_artifacts(paths)
        check_distributions(paths, artifacts)
    elif args.command == "install":
        artifacts, digest = verified_artifacts(paths)
        run_install_smoke(paths, artifacts.wheel_path, digest)
    elif args.command == "prepare":
        android_release.stage_release_apk(paths.android)
        android_release.write_sha256sums(paths.android)
        artifacts, digest = build_distributions(paths)
        check_distributions(paths, artifacts)
        run_install_smoke(paths, artifacts.wheel_path, digest)
    stage = relative_to_repo(paths.stage_dir, paths.android.repo_root)
    print(f"{args.command} completed: {stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
