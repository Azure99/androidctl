from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from subprocess import CompletedProcess
from zipfile import ZipFile

import pytest

from tools.release.pypi_release import (
    BUILD_LOG_NAME,
    INSTALL_LOG_NAME,
    MANIFEST_NAME,
    PACKAGE_SPECS,
    PUBLISH_DRY_RUN_LOG_NAME,
    PUBLISH_ORDER_LOG_NAME,
    TWINE_CHECK_LOG_NAME,
    build_distributions,
    build_installed_version_assertion_script,
    build_packaged_apk_smoke_script,
    build_release_paths,
    check_distributions,
    collect_project_artifacts,
    collect_wheelhouse_artifacts,
    inspect_packaged_agent_apk,
    install_from_wheelhouse,
    main,
    relative_to_repo,
    render_publish_evidence,
    run_logged,
    resolve_artifacts_for_directory,
    redact_repo_root,
    smoke_env_executable,
    write_manifest,
)


def test_build_release_paths_uses_versioned_stage_dir(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)

    paths = build_release_paths(repo_root)

    assert relative_to_repo(paths.stage_dir, repo_root) == "dist/release/pypi/1.2.3"
    assert paths.build_log_path.name == BUILD_LOG_NAME
    assert paths.twine_check_log_path.name == TWINE_CHECK_LOG_NAME
    assert paths.install_log_path.name == INSTALL_LOG_NAME
    assert paths.publish_order_log_path.name == PUBLISH_ORDER_LOG_NAME
    assert paths.publish_dry_run_log_path.name == PUBLISH_DRY_RUN_LOG_NAME
    assert paths.manifest_path.name == MANIFEST_NAME
    assert paths.smoke_env_dir.name == ".venv-install-smoke"
    assert paths.gradle_apk_metadata_path == (
        repo_root / "android/app/build/outputs/apk/release/output-metadata.json"
    )


def test_smoke_env_executable_keeps_venv_path_for_symlinked_python(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows venv scripts are not symlinked like POSIX bin/python")

    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    bin_dir = paths.smoke_env_dir / "bin"
    bin_dir.mkdir(parents=True)
    target_python = tmp_path / "external-python" / "python3.10"
    target_python.parent.mkdir()
    target_python.write_text("", encoding="utf-8")

    try:
        (bin_dir / "python").symlink_to(target_python)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert smoke_env_executable(paths, "python") == (
        "dist/release/pypi/1.2.3/.venv-install-smoke/bin/python"
    )


def test_collect_project_artifacts_reads_package_dist_outputs(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)

    artifacts = collect_project_artifacts(paths)

    assert [artifact.spec.distribution_name for artifact in artifacts] == [
        spec.distribution_name for spec in PACKAGE_SPECS
    ]
    assert artifacts[0].sdist_path.name == "androidctl_contracts-1.2.3.tar.gz"
    assert artifacts[0].wheel_path.name.startswith("androidctl_contracts-1.2.3-")


def test_collect_wheelhouse_artifacts_requires_expected_distribution_names(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    for spec in PACKAGE_SPECS:
        (
            paths.wheelhouse_dir / f"{spec.normalized_distribution_name}-1.2.3.tar.gz"
        ).write_text(
            "sdist",
            encoding="utf-8",
        )
        (
            paths.wheelhouse_dir
            / f"{spec.normalized_distribution_name}-1.2.3-py3-none-any.whl"
        ).write_text(
            "wheel",
            encoding="utf-8",
        )

    artifacts = collect_wheelhouse_artifacts(paths)

    assert [artifact.spec.distribution_name for artifact in artifacts] == [
        "androidctl-contracts",
        "androidctld",
        "androidctl",
    ]


def test_build_distributions_stages_packaged_apk_for_androidctl_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    build_titles: list[str] = []

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
    ) -> str:
        del command, log_path, repo_root
        build_titles.append(title)
        dist_dir = cwd / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        normalized_names = {
            "contracts": "androidctl_contracts",
            "androidctld": "androidctld",
            "androidctl": "androidctl",
        }
        normalized = normalized_names[cwd.name]
        (dist_dir / f"{normalized}-1.2.3.tar.gz").write_text("sdist", encoding="utf-8")
        (dist_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel", encoding="utf-8"
        )
        if cwd.name == "androidctl":
            assert paths.packaged_apk_resource_path.read_bytes() == b"release-apk"
            _write_androidctl_archives_with_apk(paths, b"release-apk", dist_dir)
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)

    build_distributions(paths)

    assert build_titles == [
        "build androidctl-contracts",
        "build androidctld",
        "build androidctl",
    ]
    assert not paths.packaged_apk_resource_path.exists()
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packaged_apk"]["resource_name"] == (
        "androidctl-agent-1.2.3-release.apk"
    )


def test_build_distributions_fails_when_built_apk_evidence_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
    ) -> str:
        del command, log_path, title, repo_root
        dist_dir = cwd / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        normalized_names = {
            "contracts": "androidctl_contracts",
            "androidctld": "androidctld",
            "androidctl": "androidctl",
        }
        normalized = normalized_names[cwd.name]
        (dist_dir / f"{normalized}-1.2.3.tar.gz").write_text("sdist", encoding="utf-8")
        (dist_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel", encoding="utf-8"
        )
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)

    with pytest.raises(SystemExit) as exc_info:
        build_distributions(paths)

    assert "failed to inspect packaged APK artifacts" in str(exc_info.value)
    assert not paths.manifest_path.exists()


def test_inspect_packaged_agent_apk_requires_matching_wheel_and_sdist(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk")

    evidence = inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert evidence.resource_name == "androidctl-agent-1.2.3-release.apk"
    assert evidence.source_sha256 == evidence.wheel_sha256 == evidence.sdist_sha256
    assert evidence.source_sha256 == evidence.gradle_output_sha256
    assert evidence.version_name == "1.2.3"
    assert evidence.version_code == 1_002_003
    assert evidence.gradle_metadata_path == paths.gradle_apk_metadata_path
    assert evidence.gradle_output_path.name == "app-release.apk"
    assert evidence.wheel_member == (
        "androidctl/resources/androidctl-agent-1.2.3-release.apk"
    )
    assert evidence.sdist_member.endswith(
        "/src/androidctl/resources/androidctl-agent-1.2.3-release.apk"
    )


def test_check_distributions_records_packaged_apk_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk")

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
    ) -> str:
        del command, cwd, log_path, title, repo_root
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)

    check_distributions(paths)

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packaged_apk"]["resource_name"] == (
        "androidctl-agent-1.2.3-release.apk"
    )
    assert manifest["packaged_apk"]["source_sha256"] == (
        manifest["packaged_apk"]["wheel_sha256"]
    )
    assert manifest["packaged_apk"]["source_sha256"] == (
        manifest["packaged_apk"]["sdist_sha256"]
    )
    assert manifest["packaged_apk"]["gradle_output_sha256"] == (
        manifest["packaged_apk"]["source_sha256"]
    )
    assert manifest["packaged_apk"]["version_name"] == "1.2.3"
    assert manifest["packaged_apk"]["version_code"] == 1_002_003


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        ("check", "missing distribution directory: androidctl/dist"),
        ("publish-dry-run", "missing wheelhouse: dist/release/pypi/1.2.3/wheelhouse"),
        ("install", "missing wheelhouse: dist/release/pypi/1.2.3/wheelhouse"),
    ],
    ids=["check", "publish-dry-run", "install"],
)
def test_release_operations_clear_stale_manifest_before_collecting_artifacts(
    tmp_path: Path,
    operation: str,
    expected_error: str,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.manifest_path.write_text('{"stale": true}\n', encoding="utf-8")

    if operation == "check":
        shutil.rmtree(repo_root / "androidctl" / "dist")

    with pytest.raises(SystemExit) as exc_info:
        if operation == "check":
            check_distributions(paths)
        elif operation == "publish-dry-run":
            main(["--repo-root", str(repo_root), "publish-dry-run"])
        elif operation == "install":
            install_from_wheelhouse(paths)
        else:
            raise AssertionError(f"unknown operation: {operation}")

    assert str(exc_info.value) == expected_error
    assert not paths.manifest_path.exists()


def test_inspect_packaged_agent_apk_requires_matching_gradle_version(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk", version_name="9.9.9")
    _write_androidctl_archives_with_apk(paths, b"release-apk")

    with pytest.raises(SystemExit) as exc_info:
        inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert str(exc_info.value) == (
        "release APK metadata versionName mismatch: expected 1.2.3, got '9.9.9'"
    )


def test_inspect_packaged_agent_apk_requires_matching_gradle_output(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk")
    (paths.gradle_apk_metadata_path.parent / "app-release.apk").write_bytes(
        b"different-apk"
    )

    with pytest.raises(SystemExit) as exc_info:
        inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert "does not match Gradle output" in str(exc_info.value)


def test_release_taskfile_keeps_prepare_and_publish_dry_run_wiring_stable() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    root_taskfile = repo_root / "Taskfile.yml"

    assert _task_refs(root_taskfile, "release:pypi:prepare") == [
        "release:version-check",
        "release:pypi:install",
        "release:pypi:publish:dry-run",
    ]
    assert _task_refs(root_taskfile, "release:pypi:install") == [
        "release:pypi:check",
        "python",
    ]
    assert _task_refs(root_taskfile, "release:pypi:check") == [
        "release:pypi:build",
        "python",
    ]
    assert _task_refs(root_taskfile, "release:pypi:build") == [
        "release:version-check",
        "android:release:checksum",
        "python",
    ]
    assert _task_refs(root_taskfile, "release:pypi:publish:dry-run") == [
        "release:version-check",
        "python",
    ]
    publish_dry_run_block = _task_block(root_taskfile, "release:pypi:publish:dry-run")
    assert (
        "PYTHON_ARGS: -m tools.release.pypi_release publish-dry-run"
        in publish_dry_run_block
    )
    assert "release:pypi:build" not in publish_dry_run_block
    assert "release:pypi:check" not in publish_dry_run_block
    assert "release:pypi:install" not in publish_dry_run_block


def test_build_installed_version_assertion_script_is_clean_env_safe() -> None:
    script = build_installed_version_assertion_script("1.2.3")

    assert "tools.release.pypi_release" not in script
    assert "from importlib.metadata import PackageNotFoundError, version" in script
    assert "expected = '1.2.3'" in script
    assert "androidctl-contracts" in script
    assert "androidctld" in script
    assert "androidctl" in script


def test_build_packaged_apk_smoke_script_is_clean_env_safe() -> None:
    script = build_packaged_apk_smoke_script("1.2.3")

    assert "tools.release.pypi_release" not in script
    assert "from androidctl.setup.apk_resource import" in script
    assert "expected = '1.2.3'" in script
    assert "packaged_agent_apk_path(expected)" in script


def test_inline_version_script_subprocess_does_not_inherit_parent_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", f"{tmp_path}{os.pathsep}/definitely-not-used")

    result = _run_inline_version_script(
        tmp_path,
        "import sys\nprint('\\n'.join(sys.path))\n",
        {
            "androidctl-contracts": "1.2.3",
            "androidctld": "1.2.3",
            "androidctl": "1.2.3",
        },
    )

    assert result.returncode == 0
    assert "/definitely-not-used" not in result.stdout


def test_inline_version_script_subprocess_pythonpath_only_contains_helper_dir(
    tmp_path: Path,
) -> None:
    helper_dir = tmp_path / "sitecustomize-helper"
    result = _run_inline_version_script(
        tmp_path,
        "import os\nprint(os.environ['PYTHONPATH'])\n",
        {
            "androidctl-contracts": "1.2.3",
            "androidctld": "1.2.3",
            "androidctl": "1.2.3",
        },
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(helper_dir)]


@pytest.mark.parametrize(
    ("installed_versions", "expected_returncode", "expected_lines"),
    [
        (
            {
                "androidctl-contracts": "1.2.3",
                "androidctld": "1.2.3",
                "androidctl": "1.2.3",
            },
            0,
            [
                "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK",
                "androidctld: expected=1.2.3 installed=1.2.3 status=OK",
                "androidctl: expected=1.2.3 installed=1.2.3 status=OK",
            ],
        ),
        (
            {
                "androidctl-contracts": "1.2.3",
                "androidctld": "9.9.9",
                "androidctl": "1.2.3",
            },
            1,
            [
                "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK",
                "androidctld: expected=1.2.3 installed=9.9.9 status=MISMATCH",
                "androidctl: expected=1.2.3 installed=1.2.3 status=OK",
                "installed version check failed; mismatch: androidctld",
            ],
        ),
        (
            {
                "androidctl-contracts": "1.2.3",
                "androidctl": "1.2.3",
            },
            1,
            [
                "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK",
                "androidctld: expected=1.2.3 installed=<missing> status=MISSING",
                "androidctl: expected=1.2.3 installed=1.2.3 status=OK",
                "installed version check failed; missing: androidctld",
            ],
        ),
        (
            {
                "androidctl-contracts": "1.2.3",
                "androidctld": {"__raise__": "runtime"},
                "androidctl": "1.2.3",
            },
            1,
            [
                "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK",
                "androidctld: expected=1.2.3 installed=<missing> status=MISSING",
                "androidctl: expected=1.2.3 installed=1.2.3 status=OK",
                "installed version check failed; missing: androidctld",
            ],
        ),
    ],
    ids=["all-ok", "mismatch", "missing", "metadata-read-error-as-missing"],
)
def test_build_installed_version_assertion_script_runs_in_subprocess(
    tmp_path: Path,
    installed_versions: dict[str, str | dict[str, str]],
    expected_returncode: int,
    expected_lines: list[str],
) -> None:
    result = _run_inline_version_script(
        tmp_path,
        build_installed_version_assertion_script("1.2.3"),
        installed_versions,
    )

    assert result.returncode == expected_returncode
    assert result.stdout.splitlines() == expected_lines


def test_render_publish_evidence_records_order_and_no_upload_api(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    _write_android_release_apk(paths, b"release-apk")
    for artifact in collect_project_artifacts(paths):
        paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
        (paths.wheelhouse_dir / artifact.sdist_path.name).write_text(
            "sdist", encoding="utf-8"
        )
        (paths.wheelhouse_dir / artifact.wheel_path.name).write_text(
            "wheel", encoding="utf-8"
        )
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    render_publish_evidence(paths)

    assert paths.publish_order_log_path.read_text(encoding="utf-8").splitlines() == [
        "1. androidctl-contracts",
        "2. androidctld",
        "3. androidctl",
    ]
    dry_run_text = paths.publish_dry_run_log_path.read_text(encoding="utf-8")
    assert "No upload API was called." in dry_run_text
    assert "GitHub Actions trusted publishing" in dry_run_text
    assert "Android Device Agent APK is embedded in the androidctl sdist/wheel" in (
        dry_run_text
    )
    assert "Packaged APK evidence: androidctl-agent-1.2.3-release.apk" in dry_run_text
    assert "versionName=1.2.3" in dry_run_text
    assert "versionCode=1002003" in dry_run_text
    assert (
        "dist/release/pypi/1.2.3/wheelhouse/androidctl-1.2.3-py3-none-any.whl"
        in dry_run_text
    )
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packaged_apk"]["resource_name"] == (
        "androidctl-agent-1.2.3-release.apk"
    )


def test_render_publish_evidence_fails_closed_without_packaged_apk_evidence(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    for artifact in collect_project_artifacts(paths):
        (paths.wheelhouse_dir / artifact.sdist_path.name).write_text(
            "sdist", encoding="utf-8"
        )
        (paths.wheelhouse_dir / artifact.wheel_path.name).write_text(
            "wheel", encoding="utf-8"
        )

    with pytest.raises(SystemExit) as exc_info:
        render_publish_evidence(paths)

    assert str(exc_info.value) == (
        "missing staged Android release APK for Python package data: "
        "dist/release/android/1.2.3/androidctl-agent-1.2.3-release.apk"
    )
    assert not paths.publish_dry_run_log_path.exists()


def test_write_manifest_records_wheelhouse_copies_when_present(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    artifacts = collect_project_artifacts(paths)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        (paths.wheelhouse_dir / artifact.sdist_path.name).write_text(
            "sdist", encoding="utf-8"
        )
        (paths.wheelhouse_dir / artifact.wheel_path.name).write_text(
            "wheel", encoding="utf-8"
        )

    write_manifest(paths, artifacts)

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["publish_order"] == [
        "androidctl-contracts",
        "androidctld",
        "androidctl",
    ]
    assert manifest["packages"][0]["wheelhouse_sdist_path"] == (
        "dist/release/pypi/1.2.3/wheelhouse/androidctl_contracts-1.2.3.tar.gz"
    )


def test_main_publish_dry_run_does_not_invoke_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    _write_android_release_apk(paths, b"release-apk")
    for spec in PACKAGE_SPECS:
        paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
        (
            paths.wheelhouse_dir / f"{spec.normalized_distribution_name}-1.2.3.tar.gz"
        ).write_text(
            "sdist",
            encoding="utf-8",
        )
        (
            paths.wheelhouse_dir
            / f"{spec.normalized_distribution_name}-1.2.3-py3-none-any.whl"
        ).write_text(
            "wheel",
            encoding="utf-8",
        )
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    def fail_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run must not be called by publish-dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(["--repo-root", str(repo_root), "publish-dry-run"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "wrote publish order evidence" in stdout
    assert "wrote publish dry-run evidence" in stdout


def test_main_publish_dry_run_succeeds_with_wheelhouse_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    _write_android_release_apk(paths, b"release-apk")

    for spec in PACKAGE_SPECS:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )
        shutil.rmtree(repo_root / spec.project_dir / "dist")
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    exit_code = main(["--repo-root", str(repo_root), "publish-dry-run"])

    assert exit_code == 0
    assert paths.publish_order_log_path.is_file()
    assert paths.publish_dry_run_log_path.is_file()
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packages"][0]["sdist_path"] == (
        "dist/release/pypi/1.2.3/wheelhouse/androidctl_contracts-1.2.3.tar.gz"
    )
    assert manifest["packaged_apk"]["version_name"] == "1.2.3"
    stdout = capsys.readouterr().out
    assert "wrote publish order evidence" in stdout


def test_main_publish_dry_run_fails_when_wheelhouse_is_incomplete(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    paths.manifest_path.write_text('{"stale": true}\n', encoding="utf-8")

    for spec in PACKAGE_SPECS[:-1]:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )

    missing_spec = PACKAGE_SPECS[-1]
    (
        paths.wheelhouse_dir
        / f"{missing_spec.normalized_distribution_name}-1.2.3.tar.gz"
    ).write_text(
        "sdist",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["--repo-root", str(repo_root), "publish-dry-run"])

    assert str(exc_info.value) == (
        "expected exactly one wheel for androidctl in "
        "dist/release/pypi/1.2.3/wheelhouse, found 0"
    )
    assert not paths.manifest_path.exists()


def test_install_from_wheelhouse_fails_on_installed_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    for spec in PACKAGE_SPECS:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    def fake_run(
        command: list[str], *args: object, **kwargs: object
    ) -> CompletedProcess[str]:
        executable_name = Path(command[0]).name
        if command[:3] == ["python", "-m", "venv"]:
            return CompletedProcess(args=command, returncode=0, stdout="created env\n")
        if (
            executable_name in {"androidctl", "androidctl.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctl help\n"
            )
        if (
            executable_name in {"androidctld", "androidctld.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctld help\n"
            )
        if command[1:4] == ["-m", "pip", "install"]:
            return CompletedProcess(
                args=command, returncode=0, stdout="installed wheels\n"
            )
        if command[-2] == "-c":
            assert command[-1] == build_installed_version_assertion_script("1.2.3")
            assert "tools.release.pypi_release" not in command[-1]
            return CompletedProcess(
                args=command,
                returncode=1,
                stdout=(
                    "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK\n"
                    "androidctld: expected=1.2.3 installed=9.9.9 status=MISMATCH\n"
                    "androidctl: expected=1.2.3 installed=1.2.3 status=OK\n"
                    "installed version check failed; mismatch: androidctld\n"
                ),
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        install_from_wheelhouse(paths)

    assert str(exc_info.value) == (
        "verify installed project versions failed; "
        "see dist/release/pypi/1.2.3/local-install.txt"
    )
    log_text = paths.install_log_path.read_text(encoding="utf-8")
    assert "androidctld: expected=1.2.3 installed=9.9.9 status=MISMATCH" in log_text
    assert "installed version check failed; mismatch: androidctld" in log_text


def test_install_from_wheelhouse_installs_androidctl_entry_wheel_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
    inline_scripts: list[str] = []

    for spec in PACKAGE_SPECS:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    def fake_run(
        command: list[str], *args: object, **kwargs: object
    ) -> CompletedProcess[str]:
        executable_name = Path(command[0]).name
        if command[:3] == ["python", "-m", "venv"]:
            return CompletedProcess(args=command, returncode=0, stdout="created env\n")
        if command[1:4] == ["-m", "pip", "install"]:
            command_text = " ".join(command)
            assert (
                "dist/release/pypi/1.2.3/wheelhouse/"
                "androidctl-1.2.3-py3-none-any.whl"
            ) in command_text
            assert "androidctld-1.2.3-py3-none-any.whl" not in command_text
            assert "androidctl_contracts-1.2.3-py3-none-any.whl" not in command_text
            return CompletedProcess(
                args=command, returncode=0, stdout="installed entry wheel\n"
            )
        if command[-2] == "-c":
            inline_scripts.append(command[-1])
            if command[-1] == build_installed_version_assertion_script("1.2.3"):
                return CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK\n"
                        "androidctld: expected=1.2.3 installed=1.2.3 status=OK\n"
                        "androidctl: expected=1.2.3 installed=1.2.3 status=OK\n"
                    ),
                )
            if command[-1] == build_packaged_apk_smoke_script("1.2.3"):
                return CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "packaged apk: name=androidctl-agent-1.2.3-release.apk "
                        "size=11 status=OK\n"
                    ),
                )
            raise AssertionError(f"unexpected inline script: {command[-1]}")
        if (
            executable_name in {"androidctl", "androidctl.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctl help\n"
            )
        if (
            executable_name in {"androidctld", "androidctld.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctld help\n"
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    install_from_wheelhouse(paths)
    assert inline_scripts == [
        build_installed_version_assertion_script("1.2.3"),
        build_packaged_apk_smoke_script("1.2.3"),
    ]
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packaged_apk"]["source_sha256"] == (
        manifest["packaged_apk"]["wheel_sha256"]
    )


def test_install_from_wheelhouse_fails_closed_on_packaged_apk_mismatch(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    for spec in PACKAGE_SPECS:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(
        paths,
        b"different-apk",
        paths.wheelhouse_dir,
    )

    with pytest.raises(SystemExit) as exc_info:
        install_from_wheelhouse(paths)

    assert "androidctl sdist packaged APK checksum mismatch" in str(exc_info.value)
    assert not paths.install_log_path.exists()
    assert not paths.manifest_path.exists()


def test_install_from_wheelhouse_fails_on_missing_installed_distribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)
    paths.wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    for spec in PACKAGE_SPECS:
        normalized = spec.normalized_distribution_name
        (paths.wheelhouse_dir / f"{normalized}-1.2.3.tar.gz").write_text(
            "sdist",
            encoding="utf-8",
        )
        (paths.wheelhouse_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.wheelhouse_dir,
    )

    def fake_run(
        command: list[str], *args: object, **kwargs: object
    ) -> CompletedProcess[str]:
        executable_name = Path(command[0]).name
        if command[:3] == ["python", "-m", "venv"]:
            return CompletedProcess(args=command, returncode=0, stdout="created env\n")
        if (
            executable_name in {"androidctl", "androidctl.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctl help\n"
            )
        if (
            executable_name in {"androidctld", "androidctld.exe"}
            and command[-1] == "--help"
        ):
            return CompletedProcess(
                args=command, returncode=0, stdout="androidctld help\n"
            )
        if command[1:4] == ["-m", "pip", "install"]:
            return CompletedProcess(
                args=command, returncode=0, stdout="installed wheels\n"
            )
        if command[-2] == "-c":
            assert command[-1] == build_installed_version_assertion_script("1.2.3")
            assert "tools.release.pypi_release" not in command[-1]
            return CompletedProcess(
                args=command,
                returncode=1,
                stdout=(
                    "androidctl-contracts: expected=1.2.3 installed=1.2.3 status=OK\n"
                    "androidctld: expected=1.2.3 installed=<missing> status=MISSING\n"
                    "androidctl: expected=1.2.3 installed=1.2.3 status=OK\n"
                    "installed version check failed; missing: androidctld\n"
                ),
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        install_from_wheelhouse(paths)

    assert str(exc_info.value) == (
        "verify installed project versions failed; "
        "see dist/release/pypi/1.2.3/local-install.txt"
    )
    log_text = paths.install_log_path.read_text(encoding="utf-8")
    assert "androidctld: expected=1.2.3 installed=<missing> status=MISSING" in log_text
    assert "installed version check failed; missing: androidctld" in log_text


def test_resolve_artifacts_for_directory_requires_single_match(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    spec = PACKAGE_SPECS[1]
    directory = repo_root / spec.project_dir / "dist"
    (directory / "androidctld-1.2.3-py3-none-linux_x86_64.whl").write_text(
        "extra",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        resolve_artifacts_for_directory(paths, spec, directory)


def test_redact_repo_root_replaces_absolute_repo_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert (
        redact_repo_root(f"path={repo_root}/dist", repo_root) == "path=<repo-root>/dist"
    )


def test_run_logged_redacts_repo_root_in_command_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    log_path = repo_root / "log.txt"

    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=["echo"],
            returncode=0,
            stdout=f"stored under {repo_root}/dist\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_logged(
        ["python", str(repo_root / "tool.py")],
        cwd=repo_root,
        log_path=log_path,
        title="fake",
        repo_root=repo_root,
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert str(repo_root) not in log_text
    assert "<repo-root>/tool.py" in log_text
    assert "stored under <repo-root>/dist" in log_text


def _write_repo_fixture(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "VERSION").write_text("1.2.3\n", encoding="utf-8")

    for spec in PACKAGE_SPECS:
        dist_dir = repo_root / spec.project_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        normalized = spec.normalized_distribution_name
        (dist_dir / f"{normalized}-1.2.3.tar.gz").write_text("sdist", encoding="utf-8")
        (dist_dir / f"{normalized}-1.2.3-py3-none-any.whl").write_text(
            "wheel",
            encoding="utf-8",
        )

    return repo_root


def _write_android_release_apk(
    paths,
    payload: bytes,
    *,
    version_name: str | None = None,
    version_code: int | None = None,
) -> None:
    paths.packaged_apk_source_path.parent.mkdir(parents=True, exist_ok=True)
    paths.packaged_apk_source_path.write_bytes(payload)
    paths.gradle_apk_metadata_path.parent.mkdir(parents=True, exist_ok=True)
    (paths.gradle_apk_metadata_path.parent / "app-release.apk").write_bytes(payload)
    resolved_version_name = version_name or paths.version
    resolved_version_code = version_code or _derive_android_version_code(paths.version)
    paths.gradle_apk_metadata_path.write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "versionName": resolved_version_name,
                        "versionCode": resolved_version_code,
                        "outputFile": "app-release.apk",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_androidctl_archives_with_apk(
    paths,
    payload: bytes,
    dist_dir: Path | None = None,
) -> None:
    resource_name = f"androidctl-agent-{paths.version}-release.apk"
    androidctl_dist = dist_dir or paths.repo_root / "androidctl" / "dist"
    androidctl_dist.mkdir(parents=True, exist_ok=True)
    sdist_path = androidctl_dist / f"androidctl-{paths.version}.tar.gz"
    wheel_path = androidctl_dist / f"androidctl-{paths.version}-py3-none-any.whl"
    with tarfile.open(sdist_path, "w:gz") as sdist:
        member = tarfile.TarInfo(
            f"androidctl-{paths.version}/src/androidctl/resources/{resource_name}"
        )
        member.size = len(payload)
        sdist.addfile(member, io.BytesIO(payload))
    with ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(f"androidctl/resources/{resource_name}", payload)


def _derive_android_version_code(version: str) -> int:
    major, minor, patch = (int(part) for part in version.split("."))
    return (major * 1_000_000) + (minor * 1_000) + patch


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


def _run_inline_version_script(
    tmp_path: Path,
    script: str,
    installed_versions: dict[str, str | dict[str, str]],
) -> CompletedProcess[str]:
    helper_dir = tmp_path / "sitecustomize-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / "sitecustomize.py").write_text(
        (
            "import json\n"
            "import os\n"
            "from importlib import metadata as _metadata\n"
            "_versions = json.loads(os.environ['PYPI_RELEASE_TEST_VERSIONS'])\n"
            "def _fake_version(name):\n"
            "    value = _versions.get(name)\n"
            "    if isinstance(value, dict) and value.get('__raise__') == 'runtime':\n"
            "        raise RuntimeError(f'metadata read failed for {name}')\n"
            "    try:\n"
            "        return _versions[name]\n"
            "    except KeyError as exc:\n"
            "        raise _metadata.PackageNotFoundError(name) from exc\n"
            "_metadata.version = _fake_version\n"
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYPI_RELEASE_TEST_VERSIONS"] = json.dumps(installed_versions)
    env["PYTHONPATH"] = str(helper_dir)

    return subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=env,
    )
