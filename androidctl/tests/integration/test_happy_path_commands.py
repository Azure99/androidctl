from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl.app import app
from androidctl.commands.run_pipeline import AppContext
from androidctl.daemon.client import DaemonApiError
from androidctl.exit_codes import ExitCode
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import CommandRunRequest, RuntimePayload
from tests.support import (
    SOURCE_SCREEN_REQUIRED,
    assert_error_result_spine,
    assert_public_result_spine,
    assert_retained_result_spine,
    parse_xml,
    patch_cli_context,
    retained_result,
    semantic_result,
)
from tests.support.daemon_fakes import ScriptedRecordingDaemon


def _connect_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(
            command="connect",
            envelope="bootstrap",
        )
    )


def _observe_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    return CommandResultCore.model_validate(
        semantic_result(
            command="observe",
            category="observe",
            screen_id=daemon.current_screen_id,
            source_screen_id=daemon.current_screen_id,
            execution_outcome="notApplicable",
            continuity_status="stable",
            changed=False,
        )
    )


def _open_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    previous = daemon.current_screen_id
    daemon.current_screen_id = "screen-00014"
    return CommandResultCore.model_validate(
        semantic_result(
            command="open",
            category="open",
            screen_id=daemon.current_screen_id,
            source_screen_id=previous,
            execution_outcome="dispatched",
            continuity_status="none",
            changed=True,
            screen_kwargs={
                "app_overrides": {
                    "requestedPackageName": "com.android.settings",
                    "resolvedPackageName": "com.google.android.settings.intelligence",
                    "matchType": "alias",
                }
            },
        )
    )


def _tap_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command["sourceScreenId"] == daemon.current_screen_id
    previous = daemon.current_screen_id
    daemon.current_screen_id = "screen-00015"
    return CommandResultCore.model_validate(
        semantic_result(
            command="tap",
            category="transition",
            screen_id=daemon.current_screen_id,
            source_screen_id=previous,
            execution_outcome="dispatched",
            continuity_status="stable",
            changed=True,
        )
    )


def _list_apps_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> ListAppsResult:
    del daemon, request
    assert command == {"kind": "listApps"}
    return ListAppsResult.model_validate(
        {
            "ok": True,
            "command": "list-apps",
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                },
                {
                    "packageName": "com.example.mail",
                    "appLabel": "Mail & Calendar",
                },
            ],
        }
    )


def _list_apps_daemon_failure(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> ListAppsResult:
    del daemon, request, command
    raise DaemonApiError(
        code="DEVICE_RPC_FAILED",
        message="apps.list returned malformed payload",
        details={},
    )


def _wait_app_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command == {
        "kind": "wait",
        "predicate": {
            "kind": "app",
            "packageName": "com.android.settings",
        },
        "timeoutMs": 2000,
    }
    return CommandResultCore.model_validate(
        semantic_result(
            command="wait",
            category="wait",
            screen_id=daemon.current_screen_id,
            source_screen_id=None,
            execution_outcome="notApplicable",
            continuity_status="none",
            changed=None,
        )
    )


def _screen_not_ready(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del daemon, request, command
    raise DaemonApiError(
        code="SCREEN_NOT_READY",
        message="screen is not ready",
        details={},
    )


def _runtime_busy(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del daemon, request, command
    raise DaemonApiError(
        code="RUNTIME_BUSY",
        message="runtime already has an in-flight progress command",
        details={},
    )


def _make_daemon(
    root: Path,
    *,
    current_screen_id: str | None = "screen-00013",
    command_handlers: dict[str, object] | None = None,
) -> ScriptedRecordingDaemon:
    handlers: dict[str, object] = {
        "connect": _connect_result,
        "observe": _observe_result,
        "open": _open_result,
        "tap": _tap_result,
    }
    if command_handlers is not None:
        handlers.update(command_handlers)
    return ScriptedRecordingDaemon(
        root=root,
        current_screen_id=current_screen_id,
        command_handlers=handlers,
    )


def test_connect_outputs_the_xml_public_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["connect", "--adb", "--token", "abc"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="connect", envelope="bootstrap", ok=True)
    assert daemon.run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {"mode": "adb", "token": "abc"},
    }
    assert daemon.runtime_calls[-1]["workspaceRoot"] == tmp_path.as_posix()


def test_connect_without_workspace_root_discovers_daemon_from_cwd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd-workspace"
    daemon_root = tmp_path / "daemon-runtime"
    daemon = _make_daemon(daemon_root)
    discovery_calls: list[Path] = []
    context = AppContext(
        daemon=None,
        cwd=cwd,
        env={},
        daemon_discovery=lambda workspace_root: (
            discovery_calls.append(workspace_root) or daemon
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.build_context", lambda: context
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context", lambda: context
    )

    result = CliRunner().invoke(app, ["connect", "--adb", "--token", "abc"])

    assert result.exit_code == 0
    assert discovery_calls == [cwd.resolve()]
    assert daemon.run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {"mode": "adb", "token": "abc"},
    }


def test_connect_serial_is_serialized_when_supplied(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(
        app,
        ["connect", "--adb", "--serial", "emulator-5554", "--token", "abc"],
    )

    assert result.exit_code == 0
    assert daemon.run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {
            "mode": "adb",
            "token": "abc",
            "serial": "emulator-5554",
        },
    }


def test_connect_lan_serializes_explicit_port(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(
        app,
        [
            "connect",
            "--host",
            "192.168.0.10",
            "--port",
            "18181",
            "--token",
            "abc",
        ],
    )

    assert result.exit_code == 0
    assert daemon.run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {
            "mode": "lan",
            "token": "abc",
            "host": "192.168.0.10",
            "port": 18181,
        },
    }


def test_connect_retained_device_unauthorized_renders_stdout_xml_and_exit_3(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def _wrong_token_result(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        del daemon, request, command
        return RetainedResultEnvelope.model_validate(
            retained_result(
                command="connect",
                envelope="bootstrap",
                ok=False,
                code="DEVICE_AGENT_UNAUTHORIZED",
                message="device agent rejected request",
                details={
                    "sourceCode": "DEVICE_AGENT_UNAUTHORIZED",
                    "sourceKind": "device",
                    "reason": "wrong-token",
                    "token": "Bearer device-secret",
                    "serial": "emulator-5554",
                    "endpoint": "http://127.0.0.1:17171",
                    "raw": {"Authorization": "Bearer device-secret"},
                },
            )
        )

    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(
            tmp_path,
            command_handlers={"connect": _wrong_token_result},
        ),
    )

    result = CliRunner().invoke(app, ["connect", "--adb", "--token", "wrong-token"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="connect",
        envelope="bootstrap",
        ok=False,
    )
    assert root.attrib["code"] == "DEVICE_AGENT_UNAUTHORIZED"
    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "DEVICE_AGENT_UNAUTHORIZED",
        "sourceKind": "device",
        "reason": "wrong-token",
    }
    for unsafe in (
        "DAEMON_UNAVAILABLE",
        "Bearer",
        "device-secret",
        "emulator-5554",
        "127.0.0.1",
        "Authorization",
    ):
        assert unsafe not in result.stdout


def test_connect_retained_version_mismatch_renders_stdout_xml_and_exit_3(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def _version_mismatch_result(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        del daemon, request, command
        return RetainedResultEnvelope.model_validate(
            retained_result(
                command="connect",
                envelope="bootstrap",
                ok=False,
                code="DEVICE_AGENT_VERSION_MISMATCH",
                message=(
                    "device agent release version mismatch: daemon=0.1.0 "
                    "agent=0.1.1; install matching androidctld and Android "
                    "agent/APK versions"
                ),
                details={
                    "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
                    "sourceKind": "device",
                    "expectedReleaseVersion": "0.1.0",
                    "actualReleaseVersion": "0.1.1",
                    "token": "Bearer device-secret",
                },
            )
        )

    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(
            tmp_path,
            command_handlers={"connect": _version_mismatch_result},
        ),
    )

    result = CliRunner().invoke(app, ["connect", "--adb", "--token", "abc"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="connect",
        envelope="bootstrap",
        ok=False,
    )
    assert root.attrib["code"] == "DEVICE_AGENT_VERSION_MISMATCH"
    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
        "expectedReleaseVersion": "0.1.0",
        "actualReleaseVersion": "0.1.1",
    }
    assert "Bearer" not in result.stdout


def test_open_outputs_the_xml_public_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["open", "app:com.android.settings"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="open",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        ok=True,
    )
    assert root.attrib["nextScreenId"] == "screen-00014"
    assert root.find("./artifacts") is not None
    assert daemon.run_calls[-1]["command"] == {
        "kind": "open",
        "target": {"kind": "app", "value": "com.android.settings"},
    }


def test_open_xml_renders_app_core_fields(monkeypatch, tmp_path: Path) -> None:
    patch_cli_context(monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path))

    result = CliRunner().invoke(app, ["open", "app:com.android.settings"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    app_node = root.find("./screen/app")
    assert app_node is not None
    assert app_node.attrib["packageName"] == "com.android.settings"
    assert app_node.attrib["activityName"] == "com.android.settings.Settings"
    assert app_node.attrib["requestedPackageName"] == "com.android.settings"
    assert (
        app_node.attrib["resolvedPackageName"]
        == "com.google.android.settings.intelligence"
    )
    assert app_node.attrib["matchType"] == "alias"


def test_list_apps_outputs_xml_public_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(tmp_path, command_handlers={"listApps": _list_apps_result}),
    )

    result = CliRunner().invoke(app, ["list-apps"])

    assert result.exit_code == 0
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert root.tag == "listAppsResult"
    assert root.attrib == {"ok": "true", "command": "list-apps"}
    app_nodes = root.findall("./apps/app")
    assert [node.attrib for node in app_nodes] == [
        {"packageName": "com.android.settings", "appLabel": "Settings"},
        {"packageName": "com.example.mail", "appLabel": "Mail & Calendar"},
    ]
    assert daemon.run_calls[-1]["command"] == {"kind": "listApps"}


def test_list_apps_daemon_failure_renders_outer_error_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(
            tmp_path,
            command_handlers={"listApps": _list_apps_daemon_failure},
        ),
    )

    result = CliRunner().invoke(app, ["list-apps"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="list-apps",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="apps.list returned malformed payload",
        hint="retry the command after the daemon is available",
    )


def test_list_apps_get_runtime_failure_renders_outer_error_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class RuntimeErrorDaemon(ScriptedRecordingDaemon):
        def get_runtime(self) -> RuntimePayload:
            raise DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="runtime is not connected",
                details={},
            )

    daemon = RuntimeErrorDaemon(
        root=tmp_path,
        command_handlers={"listApps": _list_apps_result},
    )
    patch_cli_context(monkeypatch, tmp_path=tmp_path, daemon=daemon)

    result = CliRunner().invoke(app, ["list-apps"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    assert daemon.run_calls == []
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="list-apps",
        code="DEVICE_NOT_CONNECTED",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="runtime is not connected",
        hint="re-run `androidctl connect`",
    )


def test_tap_injects_runtime_current_screen_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["tap", "n3"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="tap",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00013",
        ok=True,
    )
    assert daemon.run_calls[-1]["command"] == {
        "kind": "tap",
        "ref": "n3",
        "sourceScreenId": "screen-00013",
    }


def test_wait_app_predicate_payload_is_unchanged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(tmp_path, command_handlers={"wait": _wait_app_result}),
    )

    result = CliRunner().invoke(
        app,
        ["wait", "--until", "app", "--app", "com.android.settings"],
    )

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(root, command="wait", result_family="full", ok=True)
    assert daemon.run_calls[-1]["command"] == {
        "kind": "wait",
        "predicate": {
            "kind": "app",
            "packageName": "com.android.settings",
        },
        "timeoutMs": 2000,
    }


def test_connect_usage_error_uses_error_result_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(
        app,
        ["connect", "--host", "127.0.0.1", "--token", "abc"],
    )

    assert result.exit_code == 2
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="connect",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


def test_connect_blank_token_uses_error_result_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["connect", "--adb", "--token", "   "])

    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="connect",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


def test_tap_blank_screen_id_is_usage_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["tap", "n3", "--screen-id", "   "])

    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


def test_tap_bad_ref_usage_error_renders_not_attempted_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["tap", "bad-ref"])

    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["scroll", "n8", "sideways"],
        ["scroll", "n8", "sideways", "--screen-id", "screen-override"],
    ],
)
def test_scroll_bad_direction_usage_error_renders_not_attempted_xml_without_daemon(
    monkeypatch,
    tmp_path: Path,
    argv: list[str],
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="scroll",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


def test_tap_missing_runtime_screen_renders_not_attempted_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(tmp_path, current_screen_id=None),
    )

    result = CliRunner().invoke(app, ["tap", "n3"])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="preDispatch",
        message="screen is not ready yet",
        hint="run `androidctl observe` to refresh the current screen",
    )
    assert daemon.runtime_calls == [
        {
            "workspaceRoot": tmp_path.as_posix(),
            "artifactRoot": f"{tmp_path.as_posix()}/.androidctl",
        }
    ]
    assert daemon.run_calls == []


def test_tap_post_dispatch_screen_unavailable_stays_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(tmp_path, command_handlers={"tap": _screen_not_ready}),
    )

    result = CliRunner().invoke(app, ["tap", "n3"])

    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="screen is not ready",
        hint="run `androidctl observe` to refresh the current screen",
    )
    assert daemon.run_calls == [
        {
            "command": {
                "kind": "tap",
                "ref": "n3",
                "sourceScreenId": "screen-00013",
            }
        }
    ]


def test_tap_semantic_runtime_busy_renders_outer_error_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_daemon(tmp_path, command_handlers={"tap": _runtime_busy}),
    )

    result = CliRunner().invoke(app, ["tap", "n3"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="tap",
        code="RUNTIME_BUSY",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="runtime already has an in-flight progress command",
        hint="wait for the active progress command to finish, then retry",
    )
    assert daemon.run_calls == [
        {
            "command": {
                "kind": "tap",
                "ref": "n3",
                "sourceScreenId": "screen-00013",
            }
        }
    ]


def test_removed_raw_command_is_click_unknown_command_without_daemon_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch, tmp_path=tmp_path, daemon=_make_daemon(tmp_path)
    )

    result = CliRunner().invoke(app, ["raw", "rpc", "method", "text=secret"])

    assert result.exit_code == int(ExitCode.USAGE)
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert "No such command 'raw'" in result.stderr
    assert "text=secret" not in result.stderr
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []
