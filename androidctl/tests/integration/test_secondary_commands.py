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
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import CommandRunRequest
from tests.support import (
    SOURCE_SCREEN_ABSENT,
    SOURCE_SCREEN_REQUIRED,
    assert_error_result_spine,
    assert_public_result_spine,
    assert_retained_result_spine,
    assert_truth_spine,
    parse_xml,
    patch_cli_context,
    retained_result,
    semantic_result,
)
from tests.support.daemon_fakes import ScriptedRecordingDaemon


def _secondary_transition_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    kind = str(command["kind"])
    ref = str(command["ref"])
    return CommandResultCore.model_validate(
        semantic_result(
            command={
                "longTap": "long-tap",
                "focus": "focus",
                "submit": "submit",
                "type": "type",
                "scroll": "scroll",
            }[kind],
            category="transition",
            screen_id=daemon.current_screen_id,
            source_screen_id=daemon.current_screen_id,
            execution_outcome="dispatched",
            continuity_status="stable",
            changed=True,
            screen_kwargs={
                "label": "Connected",
                "focus_ref": ref,
                "target_ref": ref,
            },
        )
    )


def _secondary_global_action_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    source_screen_id = command.get("sourceScreenId")
    assert source_screen_id is None or isinstance(source_screen_id, str)
    screen_id = daemon.current_screen_id or "screen-after-global"
    return CommandResultCore.model_validate(
        semantic_result(
            command=str(command["kind"]),
            category="transition",
            screen_id=screen_id,
            source_screen_id=source_screen_id,
            execution_outcome="dispatched",
            continuity_status="none" if source_screen_id is None else "stable",
            changed=None if source_screen_id is None else False,
        )
    )


def _secondary_global_action_override_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert daemon.current_screen_id is None
    assert command == {"kind": "home", "sourceScreenId": "screen-override"}
    return CommandResultCore.model_validate(
        semantic_result(
            command="home",
            category="transition",
            screen_id="screen-override-next",
            source_screen_id="screen-override",
            execution_outcome="dispatched",
            continuity_status="stable",
            changed=False,
        )
    )


def _secondary_wait_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    return CommandResultCore.model_validate(
        semantic_result(
            command="wait",
            category="wait",
            screen_id=daemon.current_screen_id,
            source_screen_id=daemon.current_screen_id,
            execution_outcome="notApplicable",
            continuity_status="stable",
            changed=False,
        )
    )


def _secondary_wait_unbound_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command["predicate"] == {"kind": "text-present", "text": "Connected"}
    assert "sourceScreenId" not in command
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


def _secondary_screenshot_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(
            command="screenshot",
            envelope="artifact",
            artifacts={
                "screenshotPng": (
                    f"/tmp/.androidctl/screenshots/{daemon.current_screen_id}.png"
                )
            },
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


def _post_action_observation_lost(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del daemon, request, command
    return CommandResultCore.model_validate(
        semantic_result(
            ok=False,
            command="long-tap",
            category="transition",
            payloadMode="none",
            sourceScreenId="screen-00021",
            nextScreenId=None,
            code="POST_ACTION_OBSERVATION_LOST",
            message=(
                "Action may have been dispatched, but no current screen truth is "
                "available."
            ),
            truth={
                "executionOutcome": "dispatched",
                "continuityStatus": "none",
                "observationQuality": "none",
                "changed": None,
            },
            screen=None,
        )
    )


def _action_not_confirmed(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    return CommandResultCore.model_validate(
        semantic_result(
            ok=False,
            command="long-tap",
            category="transition",
            payloadMode="full",
            screen_id=daemon.current_screen_id or "screen-00022",
            source_screen_id="screen-00021",
            code="ACTION_NOT_CONFIRMED",
            message="action was not confirmed on the refreshed screen",
            execution_outcome="dispatched",
            continuity_status="stable",
            changed=False,
            screen_kwargs={
                "label": "Connected",
                "target_ref": "n6",
                "target_actions": "tap",
            },
        )
    )


def _semantic_device_unavailable(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(
            ok=False,
            command="screenshot",
            envelope="artifact",
            code="DEVICE_UNAVAILABLE",
            message="No current device observation is available.",
        )
    )


def _retained_runtime_busy(
    command: str,
    envelope: str,
) -> RetainedResultEnvelope:
    return RetainedResultEnvelope.model_validate(
        retained_result(
            ok=False,
            command=command,
            envelope=envelope,
            code="RUNTIME_BUSY",
            message="overlapping control requests are not allowed",
            details={"reason": "overlapping_control_request"},
        )
    )


def _screenshot_runtime_busy(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return _retained_runtime_busy("screenshot", "artifact")


def _screenshot_artifact_write_failed(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(
            ok=False,
            command="screenshot",
            envelope="artifact",
            code="WORKSPACE_STATE_UNWRITABLE",
            message="artifact write failed",
            details={
                "sourceCode": "ARTIFACT_WRITE_FAILED",
                "sourceKind": "workspace",
                "reason": "candidate-write-failed",
            },
        )
    )


def _close_runtime_retained_busy(
    daemon: ScriptedRecordingDaemon,
) -> RetainedResultEnvelope:
    del daemon
    return _retained_runtime_busy("close", "lifecycle")


def _semantic_wait_timeout(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    return CommandResultCore.model_validate(
        semantic_result(
            ok=False,
            command="wait",
            category="wait",
            payloadMode="none",
            sourceScreenId=daemon.current_screen_id,
            nextScreenId=None,
            code="WAIT_TIMEOUT",
            message="Timed out waiting for the requested condition.",
            truth={
                "executionOutcome": "notApplicable",
                "continuityStatus": "stable",
                "observationQuality": "none",
                "changed": None,
            },
            screen=None,
        )
    )


def _close_runtime_result(
    daemon: ScriptedRecordingDaemon,
    close_calls: list[dict[str, object]],
) -> RetainedResultEnvelope:
    workspace_root = daemon.root.as_posix()
    close_calls.append({"workspaceRoot": workspace_root})
    return RetainedResultEnvelope.model_validate(
        retained_result(command="close", envelope="lifecycle")
    )


def _close_runtime_busy(daemon: ScriptedRecordingDaemon) -> RetainedResultEnvelope:
    del daemon
    raise DaemonApiError(
        code="RUNTIME_BUSY",
        message="runtime busy",
        details={},
    )


def _close_runtime_busy_blank_message(
    daemon: ScriptedRecordingDaemon,
) -> RetainedResultEnvelope:
    del daemon
    raise DaemonApiError(
        code="RUNTIME_BUSY",
        message="",
        details={},
    )


def _semantic_close_failure(
    daemon: ScriptedRecordingDaemon,
) -> RetainedResultEnvelope:
    del daemon
    return RetainedResultEnvelope.model_validate(
        retained_result(
            ok=False,
            command="close",
            envelope="lifecycle",
            code="DEVICE_UNAVAILABLE",
            message="No current device observation is available.",
        )
    )


def _make_secondary_daemon(
    root: Path,
    *,
    current_screen_id: str | None = "screen-00021",
    command_handlers: dict[str, object] | None = None,
    close_calls: list[dict[str, object]] | None = None,
    close_handler: object | None = None,
) -> ScriptedRecordingDaemon:
    handlers: dict[str, object] = {
        "longTap": _secondary_transition_result,
        "focus": _secondary_transition_result,
        "submit": _secondary_transition_result,
        "type": _secondary_transition_result,
        "scroll": _secondary_transition_result,
        "back": _secondary_global_action_result,
        "home": _secondary_global_action_result,
        "recents": _secondary_global_action_result,
        "notifications": _secondary_global_action_result,
        "wait": _secondary_wait_result,
        "screenshot": _secondary_screenshot_result,
    }
    if command_handlers is not None:
        handlers.update(command_handlers)
    resolved_close_handler = close_handler
    if close_calls is not None:

        def _close_handler(daemon: ScriptedRecordingDaemon) -> RetainedResultEnvelope:
            return _close_runtime_result(daemon, close_calls)

        resolved_close_handler = _close_handler
    return ScriptedRecordingDaemon(
        root=root,
        current_screen_id=current_screen_id,
        command_handlers=handlers,
        close_handler=resolved_close_handler,
    )


@pytest.mark.parametrize(
    ("argv", "expected_command", "expected_public_command"),
    [
        (
            ["long-tap", "n6"],
            {"kind": "longTap", "ref": "n6", "sourceScreenId": "screen-00021"},
            "long-tap",
        ),
        (
            ["focus", "n5"],
            {"kind": "focus", "ref": "n5", "sourceScreenId": "screen-00021"},
            "focus",
        ),
        (
            ["submit", "n5"],
            {"kind": "submit", "ref": "n5", "sourceScreenId": "screen-00021"},
            "submit",
        ),
        (
            ["home"],
            {"kind": "home", "sourceScreenId": "screen-00021"},
            "home",
        ),
        (
            ["back"],
            {"kind": "back", "sourceScreenId": "screen-00021"},
            "back",
        ),
        (
            ["recents"],
            {"kind": "recents", "sourceScreenId": "screen-00021"},
            "recents",
        ),
        (
            ["notifications"],
            {"kind": "notifications", "sourceScreenId": "screen-00021"},
            "notifications",
        ),
    ],
)
def test_secondary_commands_use_shared_run_pipeline(
    monkeypatch,
    tmp_path: Path,
    argv: list[str],
    expected_command: dict[str, object],
    expected_public_command: str,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 0
    assert daemon.run_calls[-1]["command"] == expected_command
    assert daemon.runtime_calls[-1]["workspaceRoot"] == tmp_path.as_posix()
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command=expected_public_command,
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=True,
    )
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="stable",
        changed="ref" in expected_command,
    )
    if "ref" in expected_command:
        ref = str(expected_command["ref"])
        assert root.find("./screen/surface/focus").attrib["inputRef"] == ref
        assert root.find("./screen/groups/targets/button").attrib["ref"] == ref


@pytest.mark.parametrize(
    ("argv", "expected_command", "expected_public_command"),
    [
        (
            ["type", "n5", "hello"],
            {
                "kind": "type",
                "ref": "n5",
                "text": "hello",
                "sourceScreenId": "screen-00021",
            },
            "type",
        ),
        (
            ["scroll", "n8", "down"],
            {
                "kind": "scroll",
                "ref": "n8",
                "direction": "down",
                "sourceScreenId": "screen-00021",
            },
            "scroll",
        ),
    ],
)
def test_type_and_scroll_commands_use_shared_run_pipeline(
    monkeypatch,
    tmp_path: Path,
    argv: list[str],
    expected_command: dict[str, object],
    expected_public_command: str,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 0
    assert daemon.run_calls[-1]["command"] == expected_command
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command=expected_public_command,
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=True,
    )
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="stable",
        changed=True,
    )
    ref = str(expected_command["ref"])
    assert root.find("./screen/surface/focus").attrib["inputRef"] == ref
    assert root.find("./screen/groups/targets/button").attrib["ref"] == ref


def test_wait_outputs_the_xml_public_contract(monkeypatch, tmp_path: Path) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, ["wait", "--until", "screen-change"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="wait",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=True,
    )
    assert_truth_spine(
        root,
        execution_outcome="notApplicable",
        continuity_status="stable",
        changed=False,
    )
    assert daemon.run_calls[-1]["command"] == {
        "kind": "wait",
        "predicate": {"kind": "screen-change", "sourceScreenId": "screen-00021"},
        "timeoutMs": 2000,
    }


def test_wait_blank_screen_id_is_usage_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(
        app,
        ["wait", "--until", "screen-change", "--screen-id", "   "],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="wait",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []


def test_wait_text_present_public_xml_omits_stable_screen_basis(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"wait": _secondary_wait_unbound_result},
        ),
    )

    result = CliRunner().invoke(
        app,
        ["wait", "--until", "text-present", "--text", "Connected"],
    )

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="wait",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_ABSENT,
        ok=True,
    )
    assert_truth_spine(
        root,
        execution_outcome="notApplicable",
        continuity_status="none",
        changed=None,
    )
    assert daemon.run_calls[-1]["command"] == {
        "kind": "wait",
        "predicate": {"kind": "text-present", "text": "Connected"},
        "timeoutMs": 2000,
    }


def test_global_action_screen_id_override_wins_without_live_runtime_screen(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            current_screen_id=None,
            command_handlers={"home": _secondary_global_action_override_result},
        ),
    )

    result = CliRunner().invoke(app, ["home", "--screen-id", "screen-override"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="home",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-override",
        ok=True,
    )
    assert root.attrib["nextScreenId"] == "screen-override-next"
    assert daemon.run_calls[-1]["command"] == {
        "kind": "home",
        "sourceScreenId": "screen-override",
    }


def test_screenshot_outputs_xml_with_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, ["screenshot"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="screenshot",
        envelope="artifact",
        ok=True,
    )
    assert (
        root.find("./artifacts").attrib["screenshotPng"]
        == ".androidctl/screenshots/screen-00021.png"
    )


def test_close_uses_runtime_close_route(monkeypatch, tmp_path: Path) -> None:
    close_calls: list[dict[str, object]] = []
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path, close_calls=close_calls),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=True)
    assert close_calls == [{"workspaceRoot": tmp_path.as_posix()}]


def test_close_discovers_existing_daemon_without_starting(
    monkeypatch, tmp_path: Path
) -> None:
    close_calls: list[dict[str, object]] = []
    discovered = _make_secondary_daemon(tmp_path, close_calls=close_calls)
    discovery_calls: list[str] = []

    context = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.build_context", lambda: context
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda *, workspace_root, env: (
            discovery_calls.append(workspace_root.as_posix()) or discovered
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=True)
    assert close_calls == [{"workspaceRoot": tmp_path.as_posix()}]
    assert discovery_calls == [tmp_path.as_posix()]


def test_close_without_daemon_succeeds_without_starting(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd-workspace"
    discovery_calls: list[Path] = []
    context = AppContext(
        daemon=None,
        cwd=cwd,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.build_context", lambda: context
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda *, workspace_root, env: (discovery_calls.append(workspace_root) or None),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("close must not launch androidctld")
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=True)
    assert discovery_calls == [cwd.resolve()]


def test_close_workspace_busy_uses_error_result_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.build_context", lambda: context
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda *, workspace_root, env: (_ for _ in ()).throw(
            DaemonApiError(
                code="WORKSPACE_BUSY",
                message="workspace daemon is owned by a different shell or agent",
                details={"ownerId": "shell:other:1"},
            )
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="close",
        code="WORKSPACE_BUSY",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="workspace daemon is owned by a different shell or agent",
        hint="close the conflicting workspace daemon or use a different workspace",
    )


def test_close_failure_uses_error_result_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            close_handler=_close_runtime_busy,
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert "Traceback" not in result.stderr
    assert "ValidationError" not in result.stderr

    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="close",
        code="RUNTIME_BUSY",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="runtime busy",
        hint="wait for the active progress command to finish, then retry",
    )


def test_close_retained_failure_exits_nonzero_and_writes_xml_to_stdout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            close_handler=_semantic_close_failure,
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=False)
    assert root.attrib["code"] == "DEVICE_UNAVAILABLE"
    assert root.find("./message") is not None
    assert root.find("./message").text == "No current device observation is available."


def test_close_blank_message_error_uses_stable_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            close_handler=_close_runtime_busy_blank_message,
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="close",
        code="RUNTIME_BUSY",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="close failed",
        hint="wait for the active progress command to finish, then retry",
    )


def test_wait_maps_screen_not_ready_to_screen_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"wait": _screen_not_ready},
        ),
    )

    result = CliRunner().invoke(app, ["wait", "--until", "screen-change"])

    assert result.exit_code == int(ExitCode.ERROR)
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="wait",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="screen is not ready",
        hint="run `androidctl observe` to refresh the current screen",
    )


@pytest.mark.parametrize(
    "argv",
    [["home"], ["back"], ["recents"], ["notifications"]],
)
def test_global_action_without_runtime_screen_dispatches_without_source(
    monkeypatch,
    tmp_path: Path,
    argv: list[str],
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path, current_screen_id=None),
    )

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 0
    command = argv[0]
    assert daemon.run_calls[-1]["command"] == {"kind": command}
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command=command,
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_ABSENT,
        ok=True,
    )


def test_wait_semantic_timeout_renders_semantic_xml_on_stdout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"wait": _semantic_wait_timeout},
        ),
    )

    result = CliRunner().invoke(app, ["wait", "--until", "screen-change"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="wait",
        result_family="none",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=False,
    )
    assert root.attrib["code"] == "WAIT_TIMEOUT"


def test_long_tap_semantic_failure_renders_post_action_observation_lost_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"longTap": _post_action_observation_lost},
        ),
    )

    result = CliRunner().invoke(app, ["long-tap", "n6"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="long-tap",
        result_family="none",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=False,
    )
    assert root.attrib["code"] == "POST_ACTION_OBSERVATION_LOST"
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="none",
        observation_quality="none",
        changed=None,
    )


def test_long_tap_action_not_confirmed_renders_full_semantic_xml_on_stdout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"longTap": _action_not_confirmed},
        ),
    )

    result = CliRunner().invoke(app, ["long-tap", "n6"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="long-tap",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00021",
        ok=False,
    )
    assert root.attrib["code"] == "ACTION_NOT_CONFIRMED"
    assert root.tag == "result"
    assert root.find("./screen") is not None
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="stable",
        observation_quality="authoritative",
        changed=False,
    )


def test_screenshot_retained_device_unavailable_renders_retained_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"screenshot": _semantic_device_unavailable},
        ),
    )

    result = CliRunner().invoke(app, ["screenshot"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="screenshot",
        envelope="artifact",
        ok=False,
    )
    assert root.attrib["code"] == "DEVICE_UNAVAILABLE"


def test_screenshot_retained_busy_projects_artifact_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"screenshot": _screenshot_runtime_busy},
        ),
    )

    result = CliRunner().invoke(app, ["screenshot"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="screenshot",
        envelope="artifact",
        ok=False,
    )
    assert root.attrib["code"] == "RUNTIME_BUSY"


def test_screenshot_retained_artifact_write_failure_xml_exits_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            command_handlers={"screenshot": _screenshot_artifact_write_failed},
        ),
    )

    result = CliRunner().invoke(app, ["screenshot"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="screenshot",
        envelope="artifact",
        ok=False,
    )
    assert root.attrib["code"] == "WORKSPACE_STATE_UNWRITABLE"
    assert root.find("./artifacts").attrib == {}
    assert root.find("./details").attrib == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
        "reason": "candidate-write-failed",
    }


def test_close_retained_busy_projects_lifecycle_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(
            tmp_path,
            close_handler=_close_runtime_retained_busy,
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=False)
    assert root.attrib["code"] == "RUNTIME_BUSY"


def test_removed_raw_command_group_does_not_contact_daemon(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_secondary_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, ["raw"])

    assert result.exit_code == int(ExitCode.USAGE)
    assert "Traceback" not in result.stderr
    assert "No such command 'raw'" in result.stderr
    assert daemon.runtime_calls == []
    assert daemon.run_calls == []
