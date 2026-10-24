from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from zipfile import ZipFile

import pytest
from tools.release import pypi_release as release


def write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with ZipFile(path, "w") as wheel:
        for name, payload in members.items():
            wheel.writestr(name, payload)


def write_sdist(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as sdist:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            sdist.addfile(info, io.BytesIO(payload))


def write_archives(paths: release.ReleasePaths) -> release.PackageArtifacts:
    version = paths.android.version
    root = f"androidctl-{version}"
    metadata = f"Name: androidctl\nVersion: {version}\n".encode()
    apk = paths.android.staged_apk_path.name
    paths.build_output_dir.mkdir(parents=True, exist_ok=True)
    archives = release.PackageArtifacts(
        paths.build_output_dir / f"{root}-py3-none-any.whl",
        paths.build_output_dir / f"{root}.tar.gz",
    )
    write_wheel(
        archives.wheel_path,
        {
            f"{root}.dist-info/METADATA": metadata,
            f"androidctl/resources/{apk}": b"release-apk",
        },
    )
    write_sdist(
        archives.sdist_path,
        {
            f"{root}/PKG-INFO": metadata,
            f"{root}/androidctl/src/androidctl/resources/{apk}": b"release-apk",
        },
    )
    return archives


def test_wheel_rejects_wrong_metadata_version(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    write_wheel(
        archives.wheel_path,
        {
            "androidctl-1.2.3.dist-info/METADATA": (
                b"Name: androidctl\nVersion: 9.0.0\n"
            ),
            "androidctl/resources/androidctl-agent-1.2.3-release.apk": b"release-apk",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        "wheel metadata mismatch: expected androidctl 1.2.3, got 'androidctl' '9.0.0'"
    )


def test_wheel_rejects_multiple_distribution_metadata(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    write_wheel(
        archives.wheel_path,
        {
            "androidctl-1.2.3.dist-info/METADATA": (
                b"Name: androidctl\nVersion: 1.2.3\n"
            ),
            "androidctl/resources/androidctl-agent-1.2.3-release.apk": b"release-apk",
            "extra.dist-info/METADATA": b"Name: extra\nVersion: 1\n",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert (
        str(error.value) == "Wheel must contain metadata for exactly one distribution"
    )


def test_wheel_rejects_extra_apk(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    apk_member = "androidctl/resources/androidctl-agent-1.2.3-release.apk"
    write_wheel(
        archives.wheel_path,
        {
            "androidctl-1.2.3.dist-info/METADATA": (
                b"Name: androidctl\nVersion: 1.2.3\n"
            ),
            apk_member: b"release-apk",
            "extra.apk": b"extra",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        f"Expected sole APK resource {apk_member}, got ['{apk_member}', 'extra.apk']"
    )


def test_wheel_rejects_changed_apk_bytes(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    write_wheel(
        archives.wheel_path,
        {
            "androidctl-1.2.3.dist-info/METADATA": (
                b"Name: androidctl\nVersion: 1.2.3\n"
            ),
            "androidctl/resources/androidctl-agent-1.2.3-release.apk": b"changed",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == "Wheel APK bytes differ from the verified staged APK"


def test_wheel_rejects_missing_apk(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    write_wheel(
        archives.wheel_path,
        {"androidctl-1.2.3.dist-info/METADATA": b"Name: androidctl\nVersion: 1.2.3\n"},
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        "Expected sole APK resource "
        "androidctl/resources/androidctl-agent-1.2.3-release.apk, got []"
    )


def test_sdist_rejects_wrong_metadata_version(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    apk_member = (
        "androidctl-1.2.3/androidctl/src/androidctl/resources/"
        "androidctl-agent-1.2.3-release.apk"
    )
    write_sdist(
        archives.sdist_path,
        {
            "androidctl-1.2.3/PKG-INFO": b"Name: androidctl\nVersion: 9.0.0\n",
            apk_member: b"release-apk",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        "sdist metadata mismatch: expected androidctl 1.2.3, got 'androidctl' '9.0.0'"
    )


def test_sdist_rejects_foreign_distribution_metadata(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    apk_member = (
        "androidctl-1.2.3/androidctl/src/androidctl/resources/"
        "androidctl-agent-1.2.3-release.apk"
    )
    write_sdist(
        archives.sdist_path,
        {
            "androidctl-1.2.3/PKG-INFO": b"Name: androidctl\nVersion: 1.2.3\n",
            apk_member: b"release-apk",
            "extra.dist-info/METADATA": b"Name: extra\nVersion: 1\n",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        "sdist metadata mismatch: expected androidctl 1.2.3, got 'extra' '1'"
    )


def test_sdist_rejects_extra_apk(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    apk_member = (
        "androidctl-1.2.3/androidctl/src/androidctl/resources/"
        "androidctl-agent-1.2.3-release.apk"
    )
    write_sdist(
        archives.sdist_path,
        {
            "androidctl-1.2.3/PKG-INFO": b"Name: androidctl\nVersion: 1.2.3\n",
            apk_member: b"release-apk",
            "extra.apk": b"extra",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        f"Expected sole APK resource {apk_member}, got ['{apk_member}', 'extra.apk']"
    )


def test_sdist_rejects_changed_apk_bytes(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    apk_member = (
        "androidctl-1.2.3/androidctl/src/androidctl/resources/"
        "androidctl-agent-1.2.3-release.apk"
    )
    write_sdist(
        archives.sdist_path,
        {
            "androidctl-1.2.3/PKG-INFO": b"Name: androidctl\nVersion: 1.2.3\n",
            apk_member: b"changed",
        },
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == "Sdist APK bytes differ from the verified staged APK"


def test_sdist_rejects_missing_apk(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    write_sdist(
        archives.sdist_path,
        {"androidctl-1.2.3/PKG-INFO": b"Name: androidctl\nVersion: 1.2.3\n"},
    )
    with pytest.raises(SystemExit) as error:
        release.inspect_artifacts(
            paths, archives, hashlib.sha256(b"release-apk").hexdigest()
        )
    assert str(error.value) == (
        "Expected sole APK resource "
        "androidctl-1.2.3/androidctl/src/androidctl/resources/"
        "androidctl-agent-1.2.3-release.apk, got []"
    )


def test_archives_accept_matching_bytes_without_old_logs(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    archives = write_archives(paths)
    release.inspect_artifacts(
        paths, archives, hashlib.sha256(b"release-apk").hexdigest()
    )


def test_collect_rejects_extra_distribution(release_repo: Path) -> None:
    paths = release.build_release_paths(release_repo)
    write_archives(paths)
    (paths.build_output_dir / "dependency-1-py3-none-any.whl").touch()
    with pytest.raises(SystemExit, match="no extra distributions"):
        release.collect_artifacts(paths)


@pytest.mark.parametrize("command", ["build", "check", "install", "prepare"])
def test_direct_entries_require_actual_apk_verification(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def reject(paths: object) -> str:
        raise SystemExit("invalid actual APK")

    monkeypatch.setattr(release.android_release, "verify_release", reject)
    with pytest.raises(SystemExit, match="invalid actual APK"):
        release.main(["--repo-root", str(release_repo), command])


@pytest.mark.parametrize("failure", [None, "build", "check", "install"])
def test_prepare_stages_builds_checks_and_installs_one_candidate(
    release_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    paths = release.build_release_paths(release_repo)
    paths.android.staged_apk_path.write_bytes(b"previous-candidate")
    paths.android.checksums_path.write_text("previous-checksum")
    monkeypatch.setattr(
        release.android_release, "resolve_build_tool", lambda name: Path(name)
    )
    digest = hashlib.sha256(b"release-apk").hexdigest()
    events: list[str] = []
    archives: release.PackageArtifacts | None = None
    env_dir: Path | None = None

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal archives, env_dir
        output = ""
        if command[1] == "dump":
            event = "manifest"
            assert paths.android.staged_apk_path.read_bytes() == b"release-apk"
            assert paths.android.checksums_path.read_text() == (
                f"{digest}  {paths.android.staged_apk_path.name}\n"
            )
            output = (
                "package: name='com.rainng.androidctl' "
                "versionCode='1002003' versionName='1.2.3'\n"
            )
        elif command[1] == "verify":
            event = "signature"
        elif command[1:3] == ["-m", "build"]:
            event = "build"
            source = Path(str(kwargs["cwd"]))
            assert (
                source
                / "androidctl/src/androidctl/resources"
                / paths.android.staged_apk_path.name
            ).read_bytes() == b"release-apk"
            assert command[3:] == ["--outdir", str(paths.build_output_dir), "."]
            archives = write_archives(paths)
        elif command[1:3] == ["-m", "twine"]:
            event = "check"
            assert archives is not None
            assert command[3:] == [
                "check",
                str(archives.wheel_path),
                str(archives.sdist_path),
            ]
        elif command[1:3] == ["-m", "venv"]:
            event = "venv"
            env_dir = Path(command[-1])
            env_dir.mkdir()
        elif command[1:4] == ["-m", "pip", "install"]:
            event = "install"
            assert archives is not None
            assert command[-1] == str(archives.wheel_path)
        else:
            event = "smoke"
            assert Path(command[1]).name == "installed_smoke.py"
            assert command[2:] == [
                "1.2.3",
                digest,
                str((release_repo / "skills/androidctl").absolute()),
            ]
        events.append(event)
        return subprocess.CompletedProcess(command, int(event == failure), output)

    monkeypatch.setattr(subprocess, "run", run)
    if failure is None:
        assert release.main(["--repo-root", str(release_repo), "prepare"]) == 0
    else:
        with pytest.raises(SystemExit, match="failed; see"):
            release.main(["--repo-root", str(release_repo), "prepare"])
    expected = ["manifest", "signature", "build", "check", "venv", "install", "smoke"]
    if failure is not None:
        expected = expected[: expected.index(failure) + 1]
    assert events == expected
    if env_dir is not None:
        assert env_dir.exists() == (failure == "install")


def init_git(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        "dist/\nandroid/app/build/\n*.apk\n/build/\n*.egg-info/\n"
    )
    for args in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.parametrize(
    "prepare", [release.android_release.main, release.main], ids=["android", "pypi"]
)
def test_prepare_rejects_dirty_git_before_replacing_candidate(
    release_repo: Path, prepare: Callable[[list[str]], int]
) -> None:
    paths = release.android_release.build_release_paths(release_repo)
    paths.staged_apk_path.write_bytes(b"previous-candidate")
    paths.checksums_path.write_bytes(b"previous-checksum")
    before = {
        path: path.read_bytes()
        for path in (paths.staged_apk_path, paths.checksums_path)
    }
    init_git(release_repo)
    (release_repo / "README.md").write_text("changed source")

    with pytest.raises(SystemExit, match="pre-stage: source tree must be clean"):
        prepare(["--repo-root", str(release_repo), "prepare"])

    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("dirty_phase", ["none", "pre-build"])
def test_build_checks_real_git_before_building(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, dirty_phase: str
) -> None:
    paths = release.build_release_paths(release_repo)
    resources = release_repo / "androidctl/src/androidctl/resources"
    previous = resources / "androidctl-agent-0.1.0-release.apk"
    previous.write_bytes(b"source-apk")
    preserved = [
        previous,
        release_repo / "build/retained",
        release_repo / "old.egg-info/retained",
    ]
    for path in preserved[1:]:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"existing-source-output")
    before = {path: path.read_bytes() for path in preserved}
    init_git(release_repo)
    if dirty_phase == "pre-build":
        (release_repo / "VERSION").write_text("changed")
    monkeypatch.setattr(
        release.android_release,
        "verify_release",
        lambda _: hashlib.sha256(b"release-apk").hexdigest(),
    )
    built = False
    source: Path | None = None

    def build(command: list[str], **kwargs: object) -> None:
        nonlocal built, source
        built = True
        source = Path(str(kwargs["cwd"]))
        assert source != release_repo
        assert not (source / ".git").exists()
        copied_resources = source / "androidctl/src/androidctl/resources"
        assert (
            copied_resources / paths.android.staged_apk_path.name
        ).read_bytes() == b"release-apk"
        assert not (copied_resources / previous.name).exists()
        assert not (source / "build").exists()
        assert {path: path.read_bytes() for path in preserved} == before
        write_archives(paths)

    monkeypatch.setattr(release, "run_logged", build)
    if dirty_phase == "none":
        release.build_distributions(paths)
    else:
        with pytest.raises(SystemExit, match=dirty_phase):
            release.build_distributions(paths)
    assert built == (dirty_phase != "pre-build")
    assert {path: path.read_bytes() for path in preserved} == before
    assert source is None or not source.exists()


def test_failed_build_removes_only_the_source_copy(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = release.build_release_paths(release_repo)
    original = release_repo / "androidctl/src/androidctl/resources/original.apk"
    original.write_bytes(b"keep-existing")
    monkeypatch.setattr(release.android_release, "verify_release", lambda _: "digest")
    source: Path | None = None

    def fail(command: list[str], **kwargs: object) -> None:
        nonlocal source
        source = Path(str(kwargs["cwd"]))
        assert source != release_repo
        raise SystemExit("build failed")

    monkeypatch.setattr(release, "run_logged", fail)
    with pytest.raises(SystemExit, match="build failed"):
        release.build_distributions(paths)
    assert original.read_bytes() == b"keep-existing"
    assert not original.with_name(paths.android.staged_apk_path.name).exists()
    assert source is not None
    assert not source.exists()


def test_install_smokes_the_wheel_with_isolated_environment(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = release.build_release_paths(release_repo)
    skill = release_repo / "skills/androidctl/SKILL.md"
    skill.write_bytes(b"agent instructions")
    reference = skill.with_name("reference.md")
    reference.write_bytes(b"command reference")
    archives = write_archives(paths)
    monkeypatch.setattr(release, "verified_artifacts", lambda _: (archives, "digest"))
    monkeypatch.setenv("PYTHONPATH", "unrelated-source")
    monkeypatch.setenv("PYTHONHOME", "unrelated-python")
    commands: list[list[str]] = []

    def run(command: list[str], *, env: dict[str, str], **kwargs: object) -> None:
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env
        assert env["PYTHONNOUSERSITE"] == "1"
        commands.append(command)

    monkeypatch.setattr(release, "run_logged", run)
    release.main(["--repo-root", str(release_repo), "install"])
    assert [command[-1] for command in commands if "install" in command] == [
        str(archives.wheel_path),
    ]
    assert (
        len(
            [
                command
                for command in commands
                if command[-3:] == ["1.2.3", "digest", str(skill.parent.absolute())]
            ]
        )
        == 1
    )


def test_venv_executable_keeps_environment_prefix(tmp_path: Path) -> None:
    env = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(env)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = subprocess.run(
        [release.env_executable(env, "python"), "-c", "import sys; print(sys.prefix)"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert Path(result.stdout.strip()).resolve() == env.resolve()


def test_run_logged_retains_failure_output(tmp_path: Path) -> None:
    log = tmp_path / "failure.txt"
    with pytest.raises(SystemExit, match="failed"):
        release.run_logged(
            [sys.executable, "-c", "print('specific failure'); raise SystemExit(4)"],
            cwd=tmp_path,
            log_path=log,
            title="candidate",
        )
    assert "specific failure" in log.read_text()
    assert "exit code: 4" in log.read_text()
