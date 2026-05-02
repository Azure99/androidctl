from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from tests.support import (
    SOURCE_SCREEN_ABSENT,
    assert_error_result_spine,
    assert_public_result_spine,
    assert_truth_spine,
    parse_xml,
)

from androidctl import __version__

REPO_ROOT = Path(__file__).resolve().parents[3]
ANDROIDCTL_COMMAND_TIMEOUT_SECONDS = 30


def _write_sitecustomize(tmp_path: Path, source: str) -> Path:
    patch_dir = tmp_path / "pythonpath"
    patch_dir.mkdir(parents=True, exist_ok=True)
    (patch_dir / "sitecustomize.py").write_text(
        textwrap.dedent(source).strip() + "\n",
        encoding="utf-8",
    )
    return patch_dir


def _run_installed_androidctl(
    editable_install_env,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if env_overrides is not None:
        env.update(env_overrides)

    androidctl_executable = editable_install_env.androidctl_executable
    assert androidctl_executable.exists()

    return subprocess.run(
        [
            androidctl_executable.as_posix(),
            *args,
        ],
        cwd=REPO_ROOT / "androidctl",
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=ANDROIDCTL_COMMAND_TIMEOUT_SECONDS,
    )


def test_installed_androidctl_console_script_runs_from_fresh_editable_install(
    editable_install_env,
) -> None:
    result = _run_installed_androidctl(editable_install_env, "--help")

    assert result.returncode == 0
    assert "connect" in result.stdout


def test_installed_androidctl_console_script_version_is_plain_stdout(
    editable_install_env,
) -> None:
    result = _run_installed_androidctl(editable_install_env, "--version")

    assert result.returncode == 0
    assert result.stdout == f"{__version__}\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("command", "usage_line"),
    [
        ("type", "Usage: androidctl type [OPTIONS] REF TEXT"),
        ("scroll", "Usage: androidctl scroll [OPTIONS] REF DIRECTION"),
        ("back", "Usage: androidctl back [OPTIONS]"),
        ("recents", "Usage: androidctl recents [OPTIONS]"),
        ("notifications", "Usage: androidctl notifications [OPTIONS]"),
    ],
)
def test_installed_androidctl_console_script_exposes_action_command_help(
    editable_install_env,
    command: str,
    usage_line: str,
) -> None:
    result = _run_installed_androidctl(editable_install_env, command, "--help")

    assert result.returncode == 0
    assert usage_line in result.stdout


def test_installed_androidctl_console_script_exits_nonzero_for_semantic_failure(
    editable_install_env,
    tmp_path: Path,
) -> None:
    patch_dir = _write_sitecustomize(
        tmp_path,
        """
            from androidctl.commands import run_pipeline


            def _fake_run_command(request, context):
                del request, context
                return run_pipeline.CommandOutcome(
                    payload={
                        "ok": False,
                        "command": "observe",
                        "category": "observe",
                        "payloadMode": "none",
                        "code": "DEVICE_UNAVAILABLE",
                        "message": "device unavailable",
                        "sourceScreenId": None,
                        "nextScreenId": None,
                        "truth": {
                            "executionOutcome": "notApplicable",
                            "continuityStatus": "none",
                            "observationQuality": "none",
                            "changed": None,
                        },
                        "screen": None,
                        "uncertainty": [],
                        "warnings": [],
                        "artifacts": {},
                    },
                )


            run_pipeline.run_command = _fake_run_command
            run_pipeline.build_context = lambda: object()
            """,
    )

    result = _run_installed_androidctl(
        editable_install_env,
        "observe",
        env_overrides={"PYTHONPATH": patch_dir.as_posix()},
    )

    assert result.returncode == 1
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="observe",
        result_family="none",
        source_screen_policy=SOURCE_SCREEN_ABSENT,
        ok=False,
    )
    assert root.attrib["code"] == "DEVICE_UNAVAILABLE"
    assert_truth_spine(
        root,
        execution_outcome="notApplicable",
        continuity_status="none",
        observation_quality="none",
        changed=None,
    )


def test_installed_androidctl_usage_failure_writes_error_result_stderr(
    editable_install_env,
) -> None:
    result = _run_installed_androidctl(editable_install_env, "tap", "bad-ref")

    assert result.returncode == 2
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="USAGE_ERROR",
        exit_code=2,
        tier="usage",
    )


def test_installed_androidctl_removed_raw_is_click_unknown_command(
    editable_install_env,
) -> None:
    result = _run_installed_androidctl(
        editable_install_env,
        "raw",
        "rpc",
        "meta.get",
        "text=secret",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "No such command 'raw'" in result.stderr
    assert "text=secret" not in result.stderr


def test_installed_androidctl_pre_dispatch_not_connected_writes_error_result_stderr(
    editable_install_env,
    tmp_path: Path,
) -> None:
    patch_dir = _write_sitecustomize(
        tmp_path,
        """
            from androidctl.commands import run_pipeline
            from androidctl.daemon.client import DaemonApiError


            def _fake_run_command(request, context):
                del request, context
                raise run_pipeline.PreDispatchCommandError(
                    DaemonApiError(
                        code="RUNTIME_NOT_CONNECTED",
                        message="runtime is not connected to a device",
                        details={},
                    ),
                    execution_outcome="notAttempted",
                    error_tier="preDispatch",
                )


            run_pipeline.run_command = _fake_run_command
            run_pipeline.build_context = lambda: object()
            """,
    )

    result = _run_installed_androidctl(
        editable_install_env,
        "tap",
        "n3",
        env_overrides={"PYTHONPATH": patch_dir.as_posix()},
    )

    assert result.returncode == 3
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="DEVICE_NOT_CONNECTED",
        exit_code=3,
        tier="preDispatch",
        message="runtime is not connected to a device",
        hint="re-run `androidctl connect`",
    )


def test_installed_androidctl_wrapped_workspace_busy_writes_outer_error_result_stderr(
    editable_install_env,
    tmp_path: Path,
) -> None:
    patch_dir = _write_sitecustomize(
        tmp_path,
        """
            from androidctl.commands import run_pipeline
            from androidctl.daemon.client import DaemonApiError


            def _fake_run_command(request, context):
                del request, context
                raise run_pipeline.PreDispatchCommandError(
                    DaemonApiError(
                        code="WORKSPACE_BUSY",
                        message="workspace daemon is owned by another shell or agent",
                        details={},
                    ),
                    execution_outcome="notAttempted",
                )


            run_pipeline.run_command = _fake_run_command
            run_pipeline.build_context = lambda: object()
            """,
    )

    result = _run_installed_androidctl(
        editable_install_env,
        "observe",
        env_overrides={"PYTHONPATH": patch_dir.as_posix()},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="observe",
        code="WORKSPACE_BUSY",
        exit_code=1,
        tier="outer",
        message="workspace daemon is owned by another shell or agent",
        hint="close the conflicting workspace daemon or use a different workspace",
    )


def test_installed_androidctl_semantic_runtime_busy_writes_outer_error_result_stderr(
    editable_install_env,
    tmp_path: Path,
) -> None:
    patch_dir = _write_sitecustomize(
        tmp_path,
        """
            from androidctl.commands import run_pipeline
            from androidctl.daemon.client import DaemonApiError


            def _fake_run_command(request, context):
                del request, context
                raise DaemonApiError(
                    code="RUNTIME_BUSY",
                    message="runtime already has an in-flight progress command",
                    details={},
                )


            run_pipeline.run_command = _fake_run_command
            run_pipeline.build_context = lambda: object()
            """,
    )

    result = _run_installed_androidctl(
        editable_install_env,
        "tap",
        "n3",
        env_overrides={"PYTHONPATH": patch_dir.as_posix()},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="RUNTIME_BUSY",
        exit_code=1,
        tier="outer",
        message="runtime already has an in-flight progress command",
        hint="wait for the active progress command to finish, then retry",
    )


def test_installed_androidctl_close_runtime_busy_writes_outer_error_result_stderr(
    editable_install_env,
    tmp_path: Path,
) -> None:
    patch_dir = _write_sitecustomize(
        tmp_path,
        """
            from androidctl.commands import run_pipeline
            from androidctl.daemon.client import DaemonApiError


            def _fake_run_close_command(context, workspace_root):
                del context, workspace_root
                raise DaemonApiError(
                    code="RUNTIME_BUSY",
                    message="runtime busy",
                    details={},
                )


            run_pipeline.run_close_command = _fake_run_close_command
            run_pipeline.build_context = lambda: object()
            """,
    )

    result = _run_installed_androidctl(
        editable_install_env,
        "close",
        env_overrides={"PYTHONPATH": patch_dir.as_posix()},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="close",
        code="RUNTIME_BUSY",
        exit_code=1,
        tier="outer",
        message="runtime busy",
        hint="wait for the active progress command to finish, then retry",
    )
