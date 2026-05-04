from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from androidctl.app import app
from androidctl.command_payloads import build_ref_action_command, build_scroll_command
from androidctl.commands import run_pipeline
from androidctl.commands.execute import (
    render_command_outcome,
    render_exception,
    run_and_render,
)
from androidctl.daemon.client import (
    DaemonApiError,
    DaemonProtocolError,
    IncompatibleDaemonVersionError,
)
from androidctl.exit_codes import ExitCode
from androidctl.output import CLI_OUTPUT_FAILED, CLI_RENDER_FAILED, CliOutputError
from androidctl_contracts.daemon_api import ObserveCommandPayload
from tests.support import semantic_result
from tests.support.semantic_contract import (
    assert_error_result_spine,
    assert_public_result_spine,
    assert_retained_result_spine,
    assert_truth_spine,
    parse_xml,
    retained_result,
)


def test_run_and_render_semantic_failure_keeps_stdout_xml_and_exits_error(
    monkeypatch,
) -> None:
    seen: dict[str, object] = {"stdout": []}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="long-tap",
        command=build_ref_action_command(
            kind="longTap",
            ref="n6",
            source_screen_id="screen-00021",
        ),
    )

    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload=semantic_result(
                ok=False,
                command="long-tap",
                category="transition",
                payloadMode="none",
                sourceScreenId="screen-00021",
                nextScreenId=None,
                code="POST_ACTION_OBSERVATION_LOST",
                message=(
                    "Action may have been dispatched, but no current screen truth "
                    "is available."
                ),
                truth={
                    "executionOutcome": "dispatched",
                    "continuityStatus": "none",
                    "observationQuality": "none",
                    "changed": None,
                },
                screen=None,
            ),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_success_text",
        lambda **kwargs: seen.update(render=kwargs) or "<result ok='false' />",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: seen["stdout"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="long-tap")

    assert exc_info.value.exit_code == int(ExitCode.ERROR)
    assert set(seen["render"]) == {"payload"}
    assert seen["render"]["payload"]["ok"] is False
    assert seen["stdout"] == ["<result ok='false' />"]


def test_run_and_render_action_not_confirmed_writes_result_xml_to_stdout(
    monkeypatch,
) -> None:
    stdout: list[str] = []
    stderr: list[str] = []
    cli_request = run_pipeline.CliCommandRequest(
        public_command="long-tap",
        command=build_ref_action_command(
            kind="longTap",
            ref="n6",
            source_screen_id="screen-00021",
        ),
    )

    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload=semantic_result(
                ok=False,
                command="long-tap",
                category="transition",
                payloadMode="full",
                source_screen_id="screen-00021",
                screen_id="screen-00022",
                code="ACTION_NOT_CONFIRMED",
                message="action was not confirmed on the refreshed screen",
                execution_outcome="dispatched",
                continuity_status="stable",
                changed=False,
            ),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        stdout.append,
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        stderr.append,
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="long-tap")

    assert exc_info.value.exit_code == int(ExitCode.ERROR)
    assert stderr == []
    assert len(stdout) == 1
    root = parse_xml(stdout[0])
    assert_public_result_spine(
        root,
        command="long-tap",
        result_family="full",
        ok=False,
    )
    assert root.attrib["code"] == "ACTION_NOT_CONFIRMED"
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="stable",
        observation_quality="authoritative",
    )
    assert root.find("./actionTarget") is None


@pytest.mark.parametrize(
    ("code", "execution_outcome"),
    [
        ("TARGET_NOT_ACTIONABLE", "notAttempted"),
        ("ACTION_NOT_CONFIRMED", "dispatched"),
    ],
)
def test_run_and_render_scroll_semantic_failures_stay_stdout_result_xml(
    monkeypatch,
    code: str,
    execution_outcome: str,
) -> None:
    stdout: list[str] = []
    stderr: list[str] = []
    cli_request = run_pipeline.CliCommandRequest(
        public_command="scroll",
        command=build_scroll_command(
            ref="n6",
            direction="up",
            source_screen_id="screen-00021",
        ),
    )

    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload=semantic_result(
                ok=False,
                command="scroll",
                category="transition",
                payloadMode="full",
                source_screen_id="screen-00021",
                screen_id="screen-00022",
                code=code,
                message="scroll semantic failure",
                execution_outcome=execution_outcome,
                continuity_status="stable",
                changed=False,
            ),
        ),
    )
    monkeypatch.setattr("androidctl.commands.execute.write_stdout_xml", stdout.append)
    monkeypatch.setattr("androidctl.commands.execute.write_stderr_xml", stderr.append)

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="scroll")

    assert exc_info.value.exit_code == int(ExitCode.ERROR)
    assert stderr == []
    assert len(stdout) == 1
    root = parse_xml(stdout[0])
    assert_public_result_spine(
        root,
        command="scroll",
        result_family="full",
        ok=False,
    )
    assert root.attrib["code"] == code
    assert_truth_spine(
        root,
        execution_outcome=execution_outcome,
        continuity_status="stable",
    )


def test_run_and_render_falls_back_to_cli_request_public_command(monkeypatch) -> None:
    seen: dict[str, object] = {}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="long-tap",
        command=build_ref_action_command(
            kind="longTap",
            ref="n6",
            source_screen_id="screen-00021",
        ),
    )

    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: (
            seen.update(request=request)
            or run_pipeline.CommandOutcome(
                payload=semantic_result(command="long-tap", category="transition"),
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_command_outcome",
        lambda **kwargs: seen.update(render=kwargs),
    )

    run_and_render(cli_request)

    assert seen["request"] is cli_request
    assert seen["render"] == {
        "outcome": run_pipeline.CommandOutcome(
            payload=semantic_result(command="long-tap", category="transition"),
        ),
        "public_command": "long-tap",
    }


def test_close_renders_through_shared_outcome_renderer(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(
                command="close",
                envelope="lifecycle",
            ),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_outcome",
        lambda **kwargs: seen.update(kwargs) or "<retainedResult ok='true' />",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: seen.update(stdout=message),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == 0
    assert set(seen) == {"payload", "stdout"}
    assert seen["payload"]["ok"] is True
    assert seen["stdout"] == "<retainedResult ok='true' />"


def test_close_uses_shared_command_failure_exit(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(
                ok=False,
                command="close",
                envelope="lifecycle",
                code="DEVICE_UNAVAILABLE",
                message="No current device observation is available.",
            ),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_outcome",
        lambda **kwargs: "<retainedResult ok='false' />",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: None,
    )

    def exit_for_command_failure(payload: dict[str, object]) -> None:
        seen["payload"] = payload
        raise typer.Exit(code=int(ExitCode.ERROR))

    monkeypatch.setattr(
        "androidctl.commands.execute._exit_for_command_failure",
        exit_for_command_failure,
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert seen["payload"]["command"] == "close"
    assert seen["payload"]["ok"] is False


def test_close_expected_error_uses_shared_exception_renderer(monkeypatch) -> None:
    seen: dict[str, object] = {}
    error = DaemonApiError(code="RUNTIME_BUSY", message="runtime busy", details={})
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: (_ for _ in ()).throw(error),
    )

    def render_exception(error_arg: Exception, *, command: str | None = None) -> None:
        seen["error"] = error_arg
        seen["command"] = command
        raise typer.Exit(code=int(ExitCode.ERROR))

    monkeypatch.setattr(
        "androidctl.commands.execute.render_exception",
        render_exception,
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert seen == {"error": error, "command": "close"}


def test_execute_close_expected_error_renders_error_result(
    monkeypatch,
) -> None:
    error = DaemonApiError(code="RUNTIME_BUSY", message="runtime busy", details={})
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: (_ for _ in ()).throw(error),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stdout == ""
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


def test_execute_close_retained_failure_payload_writes_retained_stdout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(
                command="close",
                envelope="lifecycle",
                ok=False,
                code="RUNTIME_BUSY",
                message="runtime busy",
                details={"source": "daemon"},
            ),
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="close",
        envelope="lifecycle",
        ok=False,
    )
    assert root.attrib["code"] == "RUNTIME_BUSY"
    assert root.find("./message").text == "runtime busy"


@pytest.mark.parametrize(
    "code",
    [
        "WORKSPACE_STATE_UNWRITABLE",
        "DEVICE_AGENT_UNAUTHORIZED",
        "DEVICE_AGENT_VERSION_MISMATCH",
    ],
)
def test_execute_retained_failure_allowlist_exits_environment(
    monkeypatch,
    code: str,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(
                command="close",
                envelope="lifecycle",
                ok=False,
                code=code,
                message="retained environment failure",
                details={"sourceCode": code, "sourceKind": "device"},
            ),
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="close",
        envelope="lifecycle",
        ok=False,
    )
    assert root.attrib["code"] == code


def test_execute_retained_failure_uses_top_level_code_for_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(
                command="close",
                envelope="lifecycle",
                ok=False,
                code="RUNTIME_BUSY",
                message="runtime busy",
                details={
                    "sourceCode": "WORKSPACE_STATE_UNWRITABLE",
                    "sourceKind": "workspace",
                },
            ),
        ),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(
        root,
        command="close",
        envelope="lifecycle",
        ok=False,
    )
    assert root.attrib["code"] == "RUNTIME_BUSY"


@pytest.mark.parametrize(
    "argv",
    [
        ["connect", "--host", "127.0.0.1", "--token", "abc"],
    ],
)
def test_execute_usage_errors_render_error_result_stderr(
    argv: list[str],
) -> None:
    result = CliRunner().invoke(app, argv)

    assert result.exit_code == int(ExitCode.USAGE)
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command=argv[0],
        code="USAGE_ERROR",
        exit_code=int(ExitCode.USAGE),
        tier="usage",
    )
    assert root.find("./message") is not None


def test_execute_runtime_boundary_error_renders_outer_error_result_stderr(
    monkeypatch,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="observe",
        command=ObserveCommandPayload(kind="observe"),
    )
    error = run_pipeline.PreDispatchCommandError(
        DaemonApiError(
            code="RUNTIME_NOT_CONNECTED",
            message="runtime is not connected to a device",
            details={},
        ),
        execution_outcome="notAttempted",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="observe")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(str(seen["stderr"][0]))
    assert_error_result_spine(
        root,
        command="observe",
        code="DEVICE_NOT_CONNECTED",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="runtime is not connected to a device",
        hint="re-run `androidctl connect`",
    )


def test_execute_live_source_admission_error_renders_pre_dispatch_result(
    monkeypatch,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    error = run_pipeline.PreDispatchCommandError(
        DaemonApiError(
            code="RUNTIME_NOT_CONNECTED",
            message="runtime is not connected to a device",
            details={},
        ),
        execution_outcome="notAttempted",
        error_tier="preDispatch",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(error, command="tap")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(seen["stderr"][0])
    assert_error_result_spine(
        root,
        command="tap",
        code="DEVICE_NOT_CONNECTED",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="preDispatch",
        message="runtime is not connected to a device",
        hint="re-run `androidctl connect`",
    )


def test_execute_daemon_api_error_renders_error_result_stderr(
    monkeypatch,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="observe",
        command=ObserveCommandPayload(kind="observe"),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: (_ for _ in ()).throw(
            DaemonApiError(
                code="INTERNAL_COMMAND_FAILURE",
                message="command failed",
                details={},
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="observe")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(str(seen["stderr"][0]))
    assert_error_result_spine(
        root,
        command="observe",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="androidctld failed while handling the request",
        hint="retry the command; if it keeps failing, inspect daemon logs",
    )


def test_run_command_success_render_failure_uses_static_cli_render_failed(
    monkeypatch,
) -> None:
    seen: dict[str, list[object]] = {"stdout": [], "stderr": []}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="observe",
        command=ObserveCommandPayload(kind="observe"),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload=semantic_result(command="observe", category="observe"),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_success_text",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: seen["stdout"].append(message),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: seen["stderr"].append(data),
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="observe")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    assert seen["stdout"] == []
    stderr = bytes(seen["stderr"][0]).decode("ascii")
    assert CLI_RENDER_FAILED in stderr
    assert "DAEMON_UNAVAILABLE" not in stderr


def test_projection_failure_after_run_command_uses_static_cli_render_failed(
    monkeypatch,
) -> None:
    seen: dict[str, list[bytes]] = {"stderr": []}
    cli_request = run_pipeline.CliCommandRequest(
        public_command="observe",
        command=ObserveCommandPayload(kind="observe"),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload=semantic_result(command="observe", category="observe"),
        ),
    )
    monkeypatch.setattr(
        "androidctl.renderers.xml.project_xml_payload",
        lambda payload: (_ for _ in ()).throw(TypeError("projection failed")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: seen["stderr"].append(data),
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_and_render(cli_request, public_command="observe")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(seen["stderr"][0].decode("ascii"))
    assert root.attrib["code"] == CLI_RENDER_FAILED
    assert root.attrib["command"] == "observe"


def test_stdout_output_failure_exits_environment_before_command_failure_exit(
    monkeypatch,
) -> None:
    seen: dict[str, list[object]] = {"stdout": [], "stderr": []}
    payload = semantic_result(ok=False, command="tap", category="transition")
    payload.update(
        {
            "payloadMode": "none",
            "code": "ACTION_FAILED",
            "message": "action failed",
            "screen": None,
            "nextScreenId": None,
        }
    )

    monkeypatch.setattr(
        "androidctl.commands.execute.render_outcome",
        lambda **kwargs: "<result ok='false' />",
    )

    def partial_stdout(message: str) -> None:
        seen["stdout"].append("partial")
        raise CliOutputError("stdout")

    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        partial_stdout,
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: seen["stderr"].append(data),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_command_outcome(
            outcome=run_pipeline.CommandOutcome(payload=payload),
            public_command="tap",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    assert seen["stdout"] == ["partial"]
    stderr = bytes(seen["stderr"][0]).decode("ascii")
    assert CLI_OUTPUT_FAILED in stderr


def test_execution_error_renderer_failure_uses_static_cli_render_failed(
    monkeypatch,
) -> None:
    seen: dict[str, list[bytes]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.render_error_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("render broke")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: seen["stderr"].append(data),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(
            DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="no device",
                details={},
            ),
            command="tap",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    stderr = seen["stderr"][0].decode("ascii")
    assert CLI_RENDER_FAILED in stderr
    assert "DAEMON_UNAVAILABLE" not in stderr


def test_execution_error_stderr_output_failure_uses_cli_output_failed(
    monkeypatch,
) -> None:
    seen: dict[str, list[bytes]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.render_error_text",
        lambda *args, **kwargs: "<result ok='false' code='DEVICE_NOT_CONNECTED' />",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: (_ for _ in ()).throw(CliOutputError("stderr")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: seen["stderr"].append(data),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(
            DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="no device",
                details={},
            ),
            command="tap",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    assert CLI_OUTPUT_FAILED in seen["stderr"][0].decode("ascii")


def test_execution_error_stderr_partial_write_exits_output_failed_without_traceback(
    monkeypatch,
) -> None:
    class PartialThenRecordingStderr:
        def __init__(self) -> None:
            self.data = bytearray()
            self.write_calls = 0

        def write(self, data: bytes) -> int:
            self.write_calls += 1
            if self.write_calls == 1:
                marker = b'code="DEVICE_NOT_CONNECTED"'
                partial_len = data.index(marker) + len(marker)
                self.data.extend(data[:partial_len])
                return partial_len
            self.data.extend(data)
            return len(data)

        def flush(self) -> None:
            pass

    stream = PartialThenRecordingStderr()
    monkeypatch.setattr(
        "androidctl.output.click.get_binary_stream",
        lambda name: stream,
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(
            DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="no device",
                details={},
            ),
            command="tap",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    stderr = stream.data.decode("utf-8")
    assert "DEVICE_NOT_CONNECTED" in stderr
    assert CLI_OUTPUT_FAILED in stderr
    assert "DAEMON_UNAVAILABLE" not in stderr
    assert "Traceback" not in stderr
    assert stream.write_calls == 2


def test_execution_error_stderr_and_fallback_output_failure_exits_without_traceback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.execute.render_error_text",
        lambda *args, **kwargs: "<result ok='false' code='DEVICE_NOT_CONNECTED' />",
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: (_ for _ in ()).throw(CliOutputError("stderr")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: (_ for _ in ()).throw(CliOutputError("stderr")),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(
            DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="no device",
                details={},
            ),
            command="tap",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)


def test_render_failure_fallback_stderr_failure_exits_without_traceback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.execute.render_outcome",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("render failed")),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_bytes",
        lambda data: (_ for _ in ()).throw(CliOutputError("stderr")),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_command_outcome(
            outcome=run_pipeline.CommandOutcome(
                payload=semantic_result(command="observe", category="observe"),
            ),
            public_command="observe",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)


def test_close_render_failure_uses_cli_render_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(command="close", envelope="lifecycle"),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_success_text",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("render failed")),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    assert CLI_RENDER_FAILED in result.stderr
    assert "DAEMON_UNAVAILABLE" not in result.stderr
    root = parse_xml(result.stderr)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=False)


def test_close_stdout_output_failure_uses_cli_output_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.run_close_command",
        lambda ctx, workspace_root: run_pipeline.CommandOutcome(
            payload=retained_result(command="close", envelope="lifecycle"),
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: (_ for _ in ()).throw(CliOutputError("stdout")),
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    assert CLI_OUTPUT_FAILED in result.stderr
    assert "DAEMON_UNAVAILABLE" not in result.stderr
    root = parse_xml(result.stderr)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=False)


def test_list_apps_cli_render_failure_uses_outer_cli_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload={
                "ok": True,
                "command": "list-apps",
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                    }
                ],
            },
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.render_success_text",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("render failed")),
    )

    result = CliRunner().invoke(app, ["list-apps"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    assert "<listAppsResult" not in result.stderr
    assert 'command="observe"' not in result.stderr
    assert "DAEMON_UNAVAILABLE" not in result.stderr
    assert "DEVICE_" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="list-apps",
        code=CLI_RENDER_FAILED,
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="androidctl failed while rendering command output",
        hint=None,
    )


def test_list_apps_cli_stdout_output_failure_uses_outer_cli_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.run_command",
        lambda request, ctx: run_pipeline.CommandOutcome(
            payload={
                "ok": True,
                "command": "list-apps",
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                    }
                ],
            },
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stdout_xml",
        lambda message: (_ for _ in ()).throw(CliOutputError("stdout")),
    )

    result = CliRunner().invoke(app, ["list-apps"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    assert "<listAppsResult" not in result.stderr
    assert 'command="observe"' not in result.stderr
    assert "DAEMON_UNAVAILABLE" not in result.stderr
    assert "DEVICE_" not in result.stderr
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="list-apps",
        code=CLI_OUTPUT_FAILED,
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="androidctl failed while writing command output",
        hint=None,
    )


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (httpx.RequestError("boom"), "DAEMON_UNAVAILABLE"),
        (DaemonProtocolError("bad daemon response"), "DAEMON_UNAVAILABLE"),
    ],
)
def test_request_and_protocol_execution_errors_keep_daemon_mapping(
    monkeypatch,
    error: Exception,
    expected_code: str,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(error, command="observe")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(seen["stderr"][0])
    assert_error_result_spine(
        root,
        command="observe",
        code=expected_code,
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
    )


def test_incompatible_daemon_version_renders_install_hint(
    monkeypatch,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            ),
            command="close",
        )

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(seen["stderr"][0])
    assert_error_result_spine(
        root,
        command="close",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
    )
    hint = root.find("./hint")
    assert hint is not None
    assert hint.text == "install matching androidctl and androidctld versions"


def test_observe_cli_entry_renders_install_hint_for_incompatible_live_daemon(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context = run_pipeline.AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.build_context",
        lambda: context,
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context",
        lambda: context,
    )

    result = CliRunner().invoke(app, ["observe"])

    assert result.exit_code == int(ExitCode.ENVIRONMENT)
    assert result.stdout == ""
    root = parse_xml(result.stderr)
    assert_error_result_spine(
        root,
        command="observe",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
    )
    hint = root.find("./hint")
    assert hint is not None
    assert hint.text == "install matching androidctl and androidctld versions"


@pytest.mark.parametrize(
    ("daemon_code", "message", "expected_hint"),
    [
        (
            "WORKSPACE_BUSY",
            "workspace daemon is owned by another shell or agent",
            "close the conflicting workspace daemon or use a different workspace",
        ),
        (
            "RUNTIME_BUSY",
            "runtime already has an in-flight progress command",
            "wait for the active progress command to finish, then retry",
        ),
    ],
)
def test_wrapped_owner_busy_pre_dispatch_errors_remain_outer(
    monkeypatch,
    daemon_code: str,
    message: str,
    expected_hint: str,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )
    error = run_pipeline.PreDispatchCommandError(
        DaemonApiError(code=daemon_code, message=message, details={}),
        execution_outcome="notAttempted",
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(error, command="tap")

    assert exc_info.value.exit_code == int(ExitCode.ERROR)
    root = parse_xml(seen["stderr"][0])
    assert_error_result_spine(
        root,
        command="tap",
        code=daemon_code,
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message=message,
        hint=expected_hint,
    )


def test_wrapped_device_disconnected_boundary_error_remains_outer(
    monkeypatch,
) -> None:
    seen: dict[str, list[str]] = {"stderr": []}
    monkeypatch.setattr(
        "androidctl.commands.execute.write_stderr_xml",
        lambda message: seen["stderr"].append(message),
    )
    error = run_pipeline.PreDispatchCommandError(
        DaemonApiError(
            code="DEVICE_DISCONNECTED",
            message="device disconnected",
            details={},
        ),
        execution_outcome="notAttempted",
    )

    with pytest.raises(typer.Exit) as exc_info:
        render_exception(error, command="tap")

    assert exc_info.value.exit_code == int(ExitCode.ENVIRONMENT)
    root = parse_xml(seen["stderr"][0])
    assert_error_result_spine(
        root,
        command="tap",
        code="DEVICE_NOT_CONNECTED",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="device disconnected",
        hint=None,
    )


def test_local_no_daemon_close_renders_retained_lifecycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context = run_pipeline.AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
    )
    monkeypatch.setattr(
        "androidctl.commands.close.run_pipeline.build_context",
        lambda: context,
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda workspace_root, env: None,
    )

    result = CliRunner().invoke(app, ["close"])

    assert result.exit_code == 0
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=True)
