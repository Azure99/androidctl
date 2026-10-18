from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tools.release import android_release


@pytest.fixture
def fake_build_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_release, "resolve_build_tool", lambda name: Path(name))


@pytest.mark.parametrize("whitespace", ["", "\n", "\r\n", " \t\r\n\n"])
def test_version_trims_surrounding_whitespace(tmp_path, whitespace):
    (tmp_path / "VERSION").write_bytes((whitespace + "1.2.3" + whitespace).encode())
    assert android_release.read_version(tmp_path) == "1.2.3"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1.2",
        "v1.2.3",
        "1. 2.3",
        "1.2.3\n4.5.6",
        "１.2.3",
    ],
)
def test_version_rejects_noncanonical_text(tmp_path, value):
    (tmp_path / "VERSION").write_bytes(value.encode())
    with pytest.raises(SystemExit, match="MAJOR.MINOR.PATCH"):
        android_release.read_version(tmp_path)


def test_stage_and_checksum_use_public_asset(release_repo: Path) -> None:
    paths = android_release.build_release_paths(release_repo)
    digest = android_release.sha256_file(paths.staged_apk_path)
    assert paths.staged_apk_path.name == "androidctl-agent-1.2.3-release.apk"
    assert (
        paths.checksums_path.read_text() == f"{digest}  {paths.staged_apk_path.name}\n"
    )
    assert paths.staged_apk_path.read_bytes() == b"release-apk"


def test_stage_requires_the_gradle_release_apk(release_repo: Path) -> None:
    paths = android_release.build_release_paths(release_repo)
    paths.gradle_apk_path.unlink()
    with pytest.raises(SystemExit, match="Gradle release APK not found"):
        android_release.stage_release_apk(paths)


@pytest.mark.parametrize("change", ["apk", "digest", "filename"])
def test_verify_rejects_broken_byte_chain(release_repo: Path, change: str) -> None:
    paths = android_release.build_release_paths(release_repo)
    if change == "apk":
        paths.staged_apk_path.write_bytes(b"changed")
    elif change == "digest":
        paths.checksums_path.write_text(f"{'0' * 64}  {paths.staged_apk_path.name}\n")
    else:
        paths.checksums_path.write_text(
            f"{android_release.sha256_file(paths.staged_apk_path)}  wrong.apk\n"
        )
    with pytest.raises(SystemExit, match="SHA256SUMS"):
        android_release.verify_release(paths)


@pytest.mark.parametrize("version_code", ["1002003", "7"])
def test_verify_checks_current_apk_without_gradle_output(
    release_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_build_tools: None,
    capsys: pytest.CaptureFixture[str],
    version_code: str,
) -> None:
    paths = android_release.build_release_paths(release_repo)
    original = (paths.staged_apk_path.read_bytes(), paths.checksums_path.read_bytes())
    (release_repo / "android/app/build").rename(release_repo / "former-build")
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = (
            "package: name='com.rainng.androidctl' "
            f"versionCode='{version_code}' versionName='1.2.3'\n"
            if command[1] == "dump"
            else "verified signature"
        )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(subprocess, "run", run)
    android_release.main(["--repo-root", str(release_repo), "verify"])
    assert f"versionCode={version_code}" in capsys.readouterr().out
    assert {command[1] for command in calls} == {"dump", "verify"}
    assert all(command[-1] == str(paths.staged_apk_path) for command in calls)
    assert (
        paths.staged_apk_path.read_bytes(),
        paths.checksums_path.read_bytes(),
    ) == original


def test_signature_failure_replaces_verification_log(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, fake_build_tools: None
) -> None:
    paths = android_release.build_release_paths(release_repo)
    paths.apk_verify_log_path.write_text("old success")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] == "dump":
            return subprocess.CompletedProcess(
                command,
                0,
                "package: name='com.rainng.androidctl' "
                "versionCode='1002003' versionName='1.2.3'",
            )
        return subprocess.CompletedProcess(command, 1, "DOES NOT VERIFY")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit, match="apksigner verification failed"):
        android_release.verify_release(paths)
    assert paths.apk_verify_log_path.read_text() == "DOES NOT VERIFY"


@pytest.mark.parametrize(
    ("package", "version", "field"),
    [
        ("other.package", "1.2.3", "name"),
        ("com.rainng.androidctl", "9.0.0", "versionName"),
    ],
)
def test_verify_rejects_wrong_package_or_version(
    release_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_build_tools: None,
    package: str,
    version: str,
    field: str,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            f"package: name='{package}' versionCode='7' versionName='{version}'",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit, match=f"APK {field} mismatch"):
        android_release.verify_release(
            android_release.build_release_paths(release_repo)
        )


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (["35.0.0", "36.0.0-rc1"], "35.0.0"),
        (["35.0.0", "36.0.0"], "36.0.0"),
        (["36.0.0-rc1", "unknown"], "36.0.0-rc1"),
    ],
)
def test_sdk_build_tools_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str], expected: str
) -> None:
    monkeypatch.setattr(android_release.shutil, "which", lambda _: None)
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    for version in entries:
        tool = tmp_path / "build-tools" / version / "apksigner.bat"
        tool.parent.mkdir(parents=True)
        tool.touch()
    assert (
        android_release.resolve_build_tool("apksigner")
        == tmp_path / "build-tools" / expected / "apksigner.bat"
    )
