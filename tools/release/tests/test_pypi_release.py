from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from subprocess import CompletedProcess
from zipfile import ZipFile

import pytest
from tools.release.pypi_release import (
    BUILD_LOG_NAME,
    BUILD_OUTPUT_DIR_NAME,
    INSTALL_LOG_NAME,
    PACKAGE_SPECS,
    TWINE_CHECK_LOG_NAME,
    build_distributions,
    build_installed_version_assertion_script,
    build_packaged_apk_smoke_script,
    build_release_paths,
    check_distributions,
    collect_project_artifacts,
    inspect_packaged_agent_apk,
    inspect_python_artifact_members,
    install_from_build_output,
    relative_to_repo,
    run_logged,
    smoke_env_executable,
    smoke_env_executable_absolute,
)


def test_build_release_paths_uses_single_package_stage_layout(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)

    paths = build_release_paths(repo_root)

    assert relative_to_repo(paths.stage_dir, repo_root) == "dist/release/pypi/1.2.3"
    assert paths.build_output_dir.name == BUILD_OUTPUT_DIR_NAME
    assert paths.build_log_path.name == BUILD_LOG_NAME
    assert paths.twine_check_log_path.name == TWINE_CHECK_LOG_NAME
    assert paths.install_log_path.name == INSTALL_LOG_NAME
    assert paths.sdist_smoke_env_dir.name == ".venv-sdist-install-smoke"
    assert paths.gradle_apk_metadata_path == (
        repo_root / "android/app/build/outputs/apk/release/output-metadata.json"
    )
    assert [spec.distribution_name for spec in PACKAGE_SPECS] == ["androidctl"]


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


def test_absolute_venv_executable_does_not_resolve_symlinked_python(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows venv launchers are not POSIX symlinks")

    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", paths.smoke_env_dir.as_posix()],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
        timeout=60,
    )
    executable_text = smoke_env_executable_absolute(paths, "python")

    result = subprocess.run(
        [executable_text, "-c", "import sys; print(sys.prefix)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout
    assert Path(result.stdout.strip()).resolve() == paths.smoke_env_dir.resolve()


def test_build_distributions_builds_single_root_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    (paths.stage_dir / "wheelhouse").mkdir(parents=True)
    (paths.stage_dir / "wheelhouse" / "old.whl").write_text("old", encoding="utf-8")
    for legacy_file_name in (
        "artifact-manifest.json",
        "publish-order.txt",
        "publish-dry-run.txt",
        "pypi-availability.txt",
    ):
        (paths.stage_dir / legacy_file_name).write_text("old\n", encoding="utf-8")
    seen_titles: list[str] = []

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
    ) -> str:
        del log_path, repo_root
        seen_titles.append(title)
        assert title == "build androidctl"
        assert cwd == paths.repo_root
        assert command[:3] == [sys.executable, "-m", "build"]
        assert "--outdir" in command
        assert paths.packaged_apk_resource_path.read_bytes() == b"release-apk"
        _write_androidctl_archives_with_apk(
            paths, b"release-apk", paths.build_output_dir
        )
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)

    build_distributions(paths)

    assert seen_titles == ["build androidctl"]
    assert not paths.packaged_apk_resource_path.exists()
    assert not (paths.stage_dir / "wheelhouse").exists()
    for legacy_file_name in (
        "artifact-manifest.json",
        "publish-order.txt",
        "publish-dry-run.txt",
        "pypi-availability.txt",
    ):
        assert not (paths.stage_dir / legacy_file_name).exists()
    artifacts = collect_project_artifacts(paths)
    assert artifacts[0].sdist_path.name == "androidctl-1.2.3.tar.gz"
    assert artifacts[0].wheel_path.name == "androidctl-1.2.3-py3-none-any.whl"


def test_build_distributions_rejects_untracked_package_source(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    (repo_root / ".gitignore").write_text(
        "dist/\n/android/app/build/\n",
        encoding="utf-8",
    )
    _init_git_repo(repo_root)
    untracked_source = repo_root / "androidctl/src/androidctl/private_internal.py"
    untracked_source.parent.mkdir(parents=True)
    untracked_source.write_text("SECRET = True\n", encoding="utf-8")
    paths = build_release_paths(repo_root)

    with pytest.raises(SystemExit) as exc_info:
        build_distributions(paths)

    message = str(exc_info.value)
    assert "pre-build source clean check failed; source tree is not clean" in message
    assert "androidctl/src/androidctl/private_internal.py" in message


def test_collect_artifacts_rejects_split_package_artifacts(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    (paths.build_output_dir / "androidctld-1.2.3-py3-none-any.whl").write_text(
        "split",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        collect_project_artifacts(paths)

    assert "forbidden split-package artifact" in str(exc_info.value)


def test_collect_artifacts_rejects_dependency_wheels(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    (paths.build_output_dir / "click-8.1.8-py3-none-any.whl").write_text(
        "dependency",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        collect_project_artifacts(paths)

    assert "unexpected artifact(s)" in str(exc_info.value)
    assert "click-8.1.8-py3-none-any.whl" in str(exc_info.value)


def test_python_artifact_member_check_rejects_wheel_child_metadata(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    wheel_path = paths.build_output_dir / "androidctl-1.2.3-py3-none-any.whl"
    with ZipFile(wheel_path, "a") as wheel:
        wheel.writestr("androidctld-1.2.3.dist-info/METADATA", "Name: androidctld\n")

    with pytest.raises(SystemExit) as exc_info:
        inspect_python_artifact_members(paths, collect_project_artifacts(paths))

    assert "dist-info mismatch" in str(exc_info.value)
    assert "androidctld-1.2.3.dist-info" in str(exc_info.value)


@pytest.mark.parametrize(
    ("archive_type", "extra_name", "expected_prefix", "expected_member"),
    [
        (
            "wheel",
            "debug.apk",
            "wheel androidctl-1.2.3-py3-none-any.whl APK resource mismatch",
            "androidctl/resources/debug.apk",
        ),
        (
            "sdist",
            "debug.apk",
            "sdist androidctl-1.2.3.tar.gz APK resource mismatch",
            "androidctl-1.2.3/androidctl/src/androidctl/resources/debug.apk",
        ),
    ],
)
def test_python_artifact_member_check_rejects_extra_apk_resource(
    tmp_path: Path,
    archive_type: str,
    extra_name: str,
    expected_prefix: str,
    expected_member: str,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_androidctl_archives_with_apk(
        paths,
        b"release-apk",
        paths.build_output_dir,
        extra_wheel_apk_name=extra_name if archive_type == "wheel" else None,
        extra_sdist_apk_name=extra_name if archive_type == "sdist" else None,
    )

    with pytest.raises(SystemExit) as exc_info:
        inspect_python_artifact_members(paths, collect_project_artifacts(paths))

    assert expected_prefix in str(exc_info.value)
    assert expected_member in str(exc_info.value)


def test_python_artifact_member_check_rejects_sdist_child_metadata(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    sdist_path = paths.build_output_dir / "androidctl-1.2.3.tar.gz"
    with tarfile.open(sdist_path, "w:gz") as sdist:
        for member_name, member_payload in {
            "androidctl-1.2.3/androidctl/src/androidctl/__init__.py": b"",
            "androidctl-1.2.3/androidctld/src/androidctld/__init__.py": b"",
            ("androidctl-1.2.3/contracts/src/" "androidctl_contracts/__init__.py"): b"",
            "androidctl-1.2.3/androidctl.egg-info/PKG-INFO": b"Name: androidctl\n",
            (
                "androidctl-1.2.3/androidctl/src/androidctl/resources/"
                "androidctl-agent-1.2.3-release.apk"
            ): b"release-apk",
        }.items():
            member = tarfile.TarInfo(member_name)
            member.size = len(member_payload)
            sdist.addfile(member, io.BytesIO(member_payload))
        payload = b"Name: androidctld\n"
        member = tarfile.TarInfo("androidctl-1.2.3/androidctld.egg-info/PKG-INFO")
        member.size = len(payload)
        sdist.addfile(member, io.BytesIO(payload))

    with pytest.raises(SystemExit) as exc_info:
        inspect_python_artifact_members(paths, collect_project_artifacts(paths))

    assert "contains child metadata" in str(exc_info.value)
    assert "androidctld.egg-info" in str(exc_info.value)


def test_inspect_packaged_agent_apk_records_android_evidence(tmp_path: Path) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)

    evidence = inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert evidence.resource_name == "androidctl-agent-1.2.3-release.apk"
    assert evidence.source_sha256 == _sha256(b"release-apk")
    assert evidence.version_name == "1.2.3"
    assert evidence.version_code == 1_002_003
    assert evidence.sdist_member.endswith(
        "/androidctl/src/androidctl/resources/androidctl-agent-1.2.3-release.apk"
    )
    assert (
        evidence.wheel_member
        == "androidctl/resources/androidctl-agent-1.2.3-release.apk"
    )
    assert evidence.android_release.checksums_path == paths.android_checksums_path


@pytest.mark.parametrize(
    ("log_name", "failure_fragment"),
    [
        ("checksum", "checksum verification log does not mention APK sha256"),
        ("apk", "APK signature verification log does not mention APK sha256"),
    ],
)
def test_inspect_packaged_agent_apk_rejects_unbound_verify_logs(
    tmp_path: Path,
    log_name: str,
    failure_fragment: str,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    if log_name == "checksum":
        paths.android_checksum_verify_log_path.write_text(
            f"verified_file={paths.packaged_apk_source_path.name}\n"
            f"{paths.packaged_apk_source_path.name}: OK\n",
            encoding="utf-8",
        )
    else:
        paths.android_apk_verify_log_path.write_text(
            f"verified_file={paths.packaged_apk_source_path.name}\n"
            "Verified using v1 scheme (JAR signing): true\n",
            encoding="utf-8",
        )

    with pytest.raises(SystemExit) as exc_info:
        inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert failure_fragment in str(exc_info.value)


def test_inspect_packaged_agent_apk_rejects_failed_checksum_verify_log(
    tmp_path: Path,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    digest = _sha256(b"release-apk")
    paths.android_checksum_verify_log_path.write_text(
        f"verified_file={paths.packaged_apk_source_path.name}\n"
        f"verified_sha256={digest}\n"
        f"{paths.packaged_apk_source_path.name}: FAILED\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        inspect_packaged_agent_apk(paths, collect_project_artifacts(paths))

    assert "does not confirm checksum success" in str(exc_info.value)


def test_check_distributions_runs_twine_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    seen_commands: list[list[str]] = []

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
    ) -> str:
        del cwd, log_path, title, repo_root
        seen_commands.append(command)
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)

    check_distributions(paths)

    assert seen_commands == [
        [
            sys.executable,
            "-m",
            "twine",
            "check",
            "androidctl-1.2.3.tar.gz",
            "androidctl-1.2.3-py3-none-any.whl",
        ]
    ]


def test_release_taskfile_wires_simplified_pypi_prepare() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    root_taskfile = repo_root / "Taskfile.yml"

    assert _task_refs(root_taskfile, "release:pypi:prepare") == [
        "release:pypi:install",
    ]
    assert _task_refs(root_taskfile, "release:pypi:build") == [
        "release:version-check",
        "android:release:checksum",
        "release:android:verify",
        "python",
    ]
    assert "release:pypi:availability:" not in root_taskfile.read_text(encoding="utf-8")
    assert "release:pypi:publish:dry-run:" not in root_taskfile.read_text(
        encoding="utf-8"
    )


def test_public_release_docs_describe_single_pypi_install() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
    agents_text = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

    assert "pip install androidctl" in readme_text
    assert "pip install androidctld" not in readme_text
    assert "pip install androidctl-contracts" not in readme_text
    assert "docs/" not in agents_text
    assert "AGENTS.override" not in agents_text


def test_child_package_directories_fail_closed_for_build_and_editable_install(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    before_snapshot = _child_distribution_metadata_snapshot(repo_root)
    env = _clean_subprocess_env()
    venv_dir = tmp_path / "editable-install-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", venv_dir.as_posix()],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
        timeout=60,
    )
    venv_python = _venv_python(venv_dir)

    try:
        for child_dir in ("androidctl", "androidctld", "contracts"):
            child_path = repo_root / child_dir
            child_build_out = tmp_path / "child-build" / child_dir
            build_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--outdir",
                    child_build_out.as_posix(),
                    child_path.as_posix(),
                ],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=60,
            )
            assert build_result.returncode != 0, build_result.stdout
            assert "does not appear to be a Python project" in build_result.stdout

            editable_result = subprocess.run(
                [
                    venv_python.as_posix(),
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    child_path.as_posix(),
                ],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=60,
            )
            assert editable_result.returncode != 0, editable_result.stdout
            assert "does not appear to be a Python project" in editable_result.stdout
    finally:
        assert _child_distribution_metadata_snapshot(repo_root) == before_snapshot


def test_build_installed_version_assertion_script_allows_single_distribution(
    tmp_path: Path,
) -> None:
    result = _run_inline_version_script(
        tmp_path,
        build_installed_version_assertion_script("1.2.3"),
        {
            "androidctl": "1.2.3",
        },
    )

    assert result.returncode == 0
    assert "distribution androidctl: expected=1.2.3 actual=1.2.3 status=OK" in (
        result.stdout
    )
    assert (
        "distribution androidctld: expected=<missing> actual=<missing> status=OK"
        in result.stdout
    )
    assert "runtime androidctl_contracts: expected=1.2.3 actual=1.2.3 status=OK" in (
        result.stdout
    )


def test_build_installed_version_assertion_script_rejects_split_distribution(
    tmp_path: Path,
) -> None:
    result = _run_inline_version_script(
        tmp_path,
        build_installed_version_assertion_script("1.2.3"),
        {
            "androidctl": "1.2.3",
            "androidctld": "1.2.3",
        },
    )

    assert result.returncode != 0
    assert "distribution androidctld: expected=<missing> actual=1.2.3 status=FAIL" in (
        result.stdout
    )


def test_build_packaged_apk_smoke_script_validates_filesystem_resource(
    tmp_path: Path,
) -> None:
    expected_sha = _sha256(b"release-apk")
    package_dir = tmp_path / "androidctl" / "setup"
    package_dir.mkdir(parents=True)
    resources_dir = tmp_path / "androidctl" / "resources"
    resources_dir.mkdir(parents=True)
    (tmp_path / "androidctl" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (resources_dir / "__init__.py").write_text("", encoding="utf-8")
    (resources_dir / "androidctl-agent-1.2.3-release.apk").write_bytes(b"release-apk")
    real_apk_resource = Path(__file__).resolve().parents[3] / (
        "androidctl/src/androidctl/setup/apk_resource.py"
    )
    (package_dir / "apk_resource.py").write_text(
        real_apk_resource.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            build_packaged_apk_smoke_script("1.2.3", expected_sha),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stdout
    assert "packaged apk:" in result.stdout


def test_install_from_build_output_smokes_wheel_and_sdist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _write_repo_fixture(tmp_path)
    paths = build_release_paths(repo_root)
    _write_android_release_apk(paths, b"release-apk")
    _write_androidctl_archives_with_apk(paths, b"release-apk", paths.build_output_dir)
    calls: list[tuple[list[str], str, dict[str, str] | None]] = []

    def fake_run_logged(
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        title: str,
        repo_root: Path,
        env: dict[str, str] | None = None,
    ) -> str:
        del cwd, log_path, repo_root
        calls.append((command, title, env))
        return ""

    monkeypatch.setattr("tools.release.pypi_release.run_logged", fake_run_logged)
    monkeypatch.setattr(
        "tools.release.pypi_release.shutil.rmtree",
        lambda *args, **kwargs: None,
    )

    install_from_build_output(paths)

    commands = [command for command, _title, _env in calls]
    titles = [title for _command, title, _env in calls]
    command_text = [" ".join(command) for command in commands]
    assert any("-m venv" in text for text in command_text)
    assert any("-m pip install" in text for text in command_text)
    assert any("androidctl-1.2.3-py3-none-any.whl" in text for text in command_text)
    assert any("androidctl-1.2.3.tar.gz" in text for text in command_text)
    assert any(".venv-sdist-install-smoke" in text for text in command_text)
    assert "sdist smoke: install androidctl sdist" in titles
    assert any(
        command[-1] == "--help" and "androidctl" in command[0] for command in commands
    )
    assert any(
        command[-1] == "--help" and "androidctld" in command[0] for command in commands
    )
    assert any(command[1:3] == ["-m", "androidctld"] for command in commands)
    for _command, _title, env in calls:
        assert env is not None
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env
        assert env["PYTHONNOUSERSITE"] == "1"


def test_run_logged_redacts_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    log_path = repo_root / "dist" / "command.log"
    tool_path = repo_root / "tool.py"
    tool_path.write_text(
        (
            "from pathlib import Path\n"
            "import sys\n"
            "repo = Path(sys.argv[1])\n"
            "print(repo / 'tool.py')\n"
            "print(f'stored under {repo / \"dist\"}')\n"
        ),
        encoding="utf-8",
    )

    run_logged(
        [sys.executable, tool_path.as_posix(), repo_root.as_posix()],
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
    return repo_root


def _init_git_repo(repo_root: Path) -> None:
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "release-test@example.invalid"],
        ["git", "config", "user.name", "Release Test"],
        ["git", "add", ".gitignore", "VERSION"],
        ["git", "commit", "-m", "fixture baseline"],
    ):
        subprocess.run(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
            timeout=60,
        )


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
    digest = _sha256(payload)
    paths.android_checksums_path.write_text(
        f"{digest}  {paths.packaged_apk_source_path.name}\n",
        encoding="utf-8",
    )
    paths.android_checksum_verify_log_path.write_text(
        f"verified_file={paths.packaged_apk_source_path.name}\n"
        f"verified_sha256={digest}\n"
        f"{paths.packaged_apk_source_path.name}: OK\n",
        encoding="utf-8",
    )
    paths.android_apk_verify_log_path.write_text(
        f"verified_file={paths.packaged_apk_source_path.name}\n"
        f"verified_sha256={digest}\n"
        "Verified using v1 scheme (JAR signing): true\n",
        encoding="utf-8",
    )


def _write_androidctl_archives_with_apk(
    paths,
    payload: bytes,
    dist_dir: Path,
    *,
    extra_sdist_apk_name: str | None = None,
    extra_wheel_apk_name: str | None = None,
) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    _write_androidctl_sdist_with_apk(
        paths,
        payload,
        dist_dir,
        extra_apk_name=extra_sdist_apk_name,
    )
    _write_androidctl_wheel_with_apk(
        paths,
        payload,
        dist_dir,
        extra_apk_name=extra_wheel_apk_name,
    )


def _write_androidctl_sdist_with_apk(
    paths,
    payload: bytes,
    dist_dir: Path,
    *,
    extra_apk_name: str | None = None,
) -> None:
    resource_name = f"androidctl-agent-{paths.version}-release.apk"
    sdist_path = dist_dir / f"androidctl-{paths.version}.tar.gz"
    with tarfile.open(sdist_path, "w:gz") as sdist:
        for member_name, member_payload in {
            f"androidctl-{paths.version}/androidctl/src/androidctl/__init__.py": (
                b'__version__ = "1.2.3"\n'
            ),
            f"androidctl-{paths.version}/androidctld/src/androidctld/__init__.py": (
                b'__version__ = "1.2.3"\n'
            ),
            (
                f"androidctl-{paths.version}/contracts/src/"
                "androidctl_contracts/__init__.py"
            ): b'__version__ = "1.2.3"\n',
            f"androidctl-{paths.version}/androidctl.egg-info/PKG-INFO": (
                b"Name: androidctl\nVersion: 1.2.3\n"
            ),
        }.items():
            member = tarfile.TarInfo(member_name)
            member.size = len(member_payload)
            sdist.addfile(member, io.BytesIO(member_payload))
        member = tarfile.TarInfo(
            f"androidctl-{paths.version}/androidctl/src/androidctl/resources/{resource_name}"
        )
        member.size = len(payload)
        sdist.addfile(member, io.BytesIO(payload))
        if extra_apk_name is not None:
            extra_payload = b"extra-apk"
            extra_member = tarfile.TarInfo(
                f"androidctl-{paths.version}/androidctl/src/androidctl/resources/"
                f"{extra_apk_name}"
            )
            extra_member.size = len(extra_payload)
            sdist.addfile(extra_member, io.BytesIO(extra_payload))


def _write_androidctl_wheel_with_apk(
    paths,
    payload: bytes,
    dist_dir: Path,
    *,
    extra_apk_name: str | None = None,
) -> None:
    resource_name = f"androidctl-agent-{paths.version}-release.apk"
    dist_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = dist_dir / f"androidctl-{paths.version}-py3-none-any.whl"
    with ZipFile(wheel_path, "w") as wheel:
        wheel.writestr("androidctl/__init__.py", '__version__ = "1.2.3"\n')
        wheel.writestr("androidctld/__init__.py", '__version__ = "1.2.3"\n')
        wheel.writestr(
            "androidctl_contracts/__init__.py",
            '__version__ = "1.2.3"\n',
        )
        wheel.writestr(f"androidctl/resources/{resource_name}", payload)
        if extra_apk_name is not None:
            wheel.writestr(f"androidctl/resources/{extra_apk_name}", b"extra-apk")
        wheel.writestr(
            f"androidctl-{paths.version}.dist-info/METADATA",
            "Name: androidctl\nVersion: 1.2.3\n",
        )
        wheel.writestr(
            f"androidctl-{paths.version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\n",
        )


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
    installed_versions: dict[str, str],
) -> CompletedProcess[str]:
    helper_dir = tmp_path / "sitecustomize-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    for package_name in ("androidctl", "androidctld", "androidctl_contracts"):
        package_dir = helper_dir / package_name
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (helper_dir / "sitecustomize.py").write_text(
        (
            "import json\n"
            "import os\n"
            "from importlib import metadata as _metadata\n"
            "_versions = json.loads(os.environ['PYPI_RELEASE_TEST_VERSIONS'])\n"
            "def _fake_version(name):\n"
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


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _venv_python(venv_dir: Path) -> Path:
    scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
    executable_name = "python.exe" if sys.platform == "win32" else "python"
    return venv_dir / scripts_dir / executable_name


def _child_distribution_metadata_snapshot(repo_root: Path) -> tuple[str, ...]:
    roots = (
        repo_root / "androidctl",
        repo_root / "androidctld",
        repo_root / "contracts",
    )
    paths: list[str] = []
    for root in roots:
        for pattern in ("*.egg-info", "*.dist-info"):
            paths.extend(
                path.relative_to(repo_root).as_posix() for path in root.rglob(pattern)
            )
        child_dist = root / "dist"
        if child_dist.exists():
            paths.append(child_dist.relative_to(repo_root).as_posix())
    return tuple(sorted(paths))


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
