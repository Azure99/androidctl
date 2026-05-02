from __future__ import annotations

from collections.abc import Callable
import json
import subprocess
from pathlib import Path

import pytest

from tools.release.android_release import (
    APK_VERIFY_LOG_NAME,
    CHECKSUMS_NAME,
    CHECKSUM_VERIFY_LOG_NAME,
    PUBLIC_APK_TEMPLATE,
    build_release_paths,
    create_release_bundle,
    main,
    parse_preview_build_tools_version,
    parse_stable_build_tools_version,
    render_upload_dry_run,
    resolve_apksigner,
    run_checksum_verification,
    stage_release_apk,
    write_sha256sums,
)


def test_stage_release_apk_uses_public_asset_name(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)

    source_apk_path = stage_release_apk(paths)

    assert source_apk_path == (
        repo_root / "android/app/build/outputs/apk/release/app-release.apk"
    )
    assert paths.staged_apk_path.name == PUBLIC_APK_TEMPLATE.format(version="1.2.3")
    assert paths.staged_apk_path.read_bytes() == b"release-apk"


def test_write_sha256sums_uses_staged_public_file_name(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    stage_release_apk(paths)

    write_sha256sums(paths)

    checksum_text = paths.checksums_path.read_text(encoding="utf-8")
    assert "app-release.apk" not in checksum_text
    assert paths.staged_apk_path.name in checksum_text
    assert paths.checksums_path.name == CHECKSUMS_NAME


def test_render_upload_dry_run_is_deterministic_for_required_assets(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    stage_release_apk(paths)
    write_sha256sums(paths)

    rendered = render_upload_dry_run(paths)

    assert "Manual commands only; nothing was executed." in rendered
    assert "gh release upload v1.2.3" in rendered
    assert "dist/release/android/1.2.3/androidctl-agent-1.2.3-release.apk" in rendered
    assert "dist/release/android/1.2.3/SHA256SUMS" in rendered
    assert "optional bundle [missing]:" in rendered


def test_create_release_bundle_contains_expected_staged_entries(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    stage_release_apk(paths)
    write_sha256sums(paths)
    paths.checksum_verify_log_path.write_text("checksum ok\n", encoding="utf-8")
    paths.apk_verify_log_path.write_text("signing ok\n", encoding="utf-8")

    create_release_bundle(paths)

    from zipfile import ZipFile

    with ZipFile(paths.bundle_path) as bundle_zip:
        assert sorted(bundle_zip.namelist()) == sorted(
            [
                paths.staged_apk_path.name,
                CHECKSUMS_NAME,
                CHECKSUM_VERIFY_LOG_NAME,
                APK_VERIFY_LOG_NAME,
            ]
        )


def test_run_checksum_verification_writes_sha256sum_compatible_log(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    stage_release_apk(paths)
    write_sha256sums(paths)

    run_checksum_verification(paths)

    assert paths.checksum_verify_log_path.read_text(encoding="utf-8") == (
        f"{paths.staged_apk_path.name}: OK\n"
    )


def test_main_upload_dry_run_does_not_invoke_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _write_repo_fixture(tmp_path)

    def fail_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run must not be called by upload-dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(["--repo-root", str(repo_root), "upload-dry-run"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Manual commands only; nothing was executed." in stdout
    assert "gh release upload v1.2.3" in stdout


def test_main_verify_refreshes_only_verification_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    stage_release_apk(paths)
    write_sha256sums(paths)

    original_apk_bytes = paths.staged_apk_path.read_bytes()
    original_checksums_text = paths.checksums_path.read_text(encoding="utf-8")
    paths.checksum_verify_log_path.write_text("old checksum log\n", encoding="utf-8")
    paths.apk_verify_log_path.write_text("old apk log\n", encoding="utf-8")

    def fake_run(
        args: list[str],
        cwd: Path,
        stdout: int,
        stderr: int,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert args[1:] == [
            "verify",
            "--verbose",
            "--print-certs",
            paths.staged_apk_path.name,
        ]
        assert cwd == paths.stage_dir
        assert stdout == subprocess.PIPE
        assert stderr == subprocess.STDOUT
        assert text is True
        assert check is False
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="signing ok\n",
        )

    monkeypatch.setattr(
        "tools.release.android_release.resolve_apksigner",
        lambda: Path("/tmp/fake-apksigner"),
    )
    monkeypatch.setattr(
        "tools.release.android_release.stage_release_apk",
        lambda _paths: pytest.fail("verify must not stage the APK"),
    )
    monkeypatch.setattr(
        "tools.release.android_release.write_sha256sums",
        lambda _paths: pytest.fail("verify must not rewrite SHA256SUMS"),
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(["--repo-root", str(repo_root), "verify"])

    assert exit_code == 0
    assert paths.staged_apk_path.read_bytes() == original_apk_bytes
    assert paths.checksums_path.read_text(encoding="utf-8") == original_checksums_text
    assert paths.checksum_verify_log_path.read_text(encoding="utf-8") == (
        f"{paths.staged_apk_path.name}: OK\n"
    )
    assert paths.apk_verify_log_path.read_text(encoding="utf-8") == "signing ok\n"
    stdout = capsys.readouterr().out
    assert "wrote verify log" in stdout
    assert "wrote checksum check log" in stdout


def test_release_taskfiles_keep_verify_as_verify_only() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    root_taskfile = repo_root / "Taskfile.yml"
    android_taskfile = repo_root / "android/Taskfile.yml"

    assert _task_refs(root_taskfile, "release:android:prepare") == [
        "release:version-check",
        "android:release:checksum",
        "android:release:verify",
        "android:release:upload:dry-run",
    ]
    assert _task_refs(root_taskfile, "release:android:verify") == [
        "release:version-check",
        "android:release:verify",
    ]
    assert _task_refs(android_taskfile, "release:stage") == ["release:build", "python"]
    assert _task_refs(android_taskfile, "release:checksum") == [
        "release:stage",
        "python",
    ]
    assert _task_refs(android_taskfile, "release:verify") == ["python"]
    assert _task_refs(android_taskfile, "release:bundle") == [
        "release:checksum",
        "release:verify",
        "python",
    ]
    assert (
        "PYTHON_ARGS: ../tools/release/android_release.py --repo-root .. verify"
        in _task_block(android_taskfile, "release:verify")
    )
    assert (
        "PYTHON_ARGS: ../tools/release/android_release.py --repo-root .. bundle"
        in _task_block(android_taskfile, "release:bundle")
    )
    assert "release:checksum" not in _task_block(android_taskfile, "release:verify")


@pytest.mark.parametrize(
    ("sdk_entries", "enabled_env_vars", "expected_entry"),
    [
        (
            [("36.0.0", "apksigner.bat"), ("36.0.0-rc1", "apksigner")],
            ("ANDROID_HOME", "ANDROID_SDK_ROOT"),
            ("36.0.0", "apksigner.bat"),
        ),
        (
            [("36.0.0-rc1", "apksigner"), ("weird-preview", "apksigner")],
            ("ANDROID_HOME",),
            ("36.0.0-rc1", "apksigner"),
        ),
        (
            [("35.0.0", "apksigner.bat"), ("35.0.0-rc1", "apksigner.bat")],
            ("ANDROID_HOME",),
            ("35.0.0", "apksigner.bat"),
        ),
    ],
    ids=["stable-over-preview", "preview-fallback", "bat-only-stable"],
)
def test_resolve_apksigner_uses_sdk_build_tools_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sdk_entries: list[tuple[str, str]],
    enabled_env_vars: tuple[str, ...],
    expected_entry: tuple[str, str],
) -> None:
    sdk_root = tmp_path / "sdk"
    expected_apksigner: Path | None = None
    for version_name, file_name in sdk_entries:
        apksigner_path = _write_apksigner(sdk_root, version_name, file_name)
        if (version_name, file_name) == expected_entry:
            expected_apksigner = apksigner_path
    assert expected_apksigner is not None

    monkeypatch.setattr("tools.release.android_release.shutil.which", lambda _: None)
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env_var in enabled_env_vars:
            monkeypatch.setenv(env_var, str(sdk_root))
        else:
            monkeypatch.delenv(env_var, raising=False)

    assert resolve_apksigner() == expected_apksigner


@pytest.mark.parametrize(
    ("parser", "version_name", "expected"),
    [
        (parse_stable_build_tools_version, "36.0.0", (36, 0, 0)),
        (parse_stable_build_tools_version, "36.0.0-rc1", None),
        (parse_preview_build_tools_version, "36.0.0-rc1", (36, 0, 0)),
        (parse_preview_build_tools_version, "weird-preview", None),
    ],
    ids=[
        "stable-version",
        "stable-rejects-preview",
        "preview-version",
        "preview-rejects-invalid",
    ],
)
def test_build_tools_version_parsers_handle_preview_and_invalid_names(
    parser: Callable[[str], tuple[int, int, int] | None],
    version_name: str,
    expected: tuple[int, int, int] | None,
) -> None:
    assert parser(version_name) == expected


def _write_repo_fixture(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    metadata_path = (
        repo_root / "android/app/build/outputs/apk/release/output-metadata.json"
    )
    apk_path = repo_root / "android/app/build/outputs/apk/release/app-release.apk"
    version_path = repo_root / "VERSION"

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text("1.2.3\n", encoding="utf-8")
    apk_path.write_bytes(b"release-apk")
    metadata_path.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "versionName": "1.2.3",
                        "outputFile": "app-release.apk",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return repo_root


def _write_apksigner(
    sdk_root: Path,
    version_name: str,
    file_name: str = "apksigner",
) -> Path:
    apksigner_path = sdk_root / "build-tools" / version_name / file_name
    apksigner_path.parent.mkdir(parents=True, exist_ok=True)
    apksigner_path.write_text("#!/bin/sh\n", encoding="utf-8")
    return apksigner_path


def _task_block(taskfile_path: Path, task_name: str) -> str:
    lines = taskfile_path.read_text(encoding="utf-8").splitlines()
    header = f"  {task_name}:"

    start_index: int | None = None
    for index, line in enumerate(lines):
        if line == header:
            start_index = index
            break
    if start_index is None:
        raise AssertionError(f"task {task_name!r} not found in {taskfile_path}")

    block_lines: list[str] = []
    for line in lines[start_index + 1 :]:
        if line.startswith("  ") and line.endswith(":") and not line.startswith("    "):
            break
        block_lines.append(line)
    return "\n".join(block_lines)


def _task_refs(taskfile_path: Path, task_name: str) -> list[str]:
    refs: list[str] = []
    for line in _task_block(taskfile_path, task_name).splitlines():
        stripped = line.strip()
        if stripped.startswith("- task: "):
            refs.append(stripped.removeprefix("- task: ").strip())
    return refs
