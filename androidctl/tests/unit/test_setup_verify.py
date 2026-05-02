from __future__ import annotations

from pathlib import Path

import pytest
from androidctl_contracts.command_results import RetainedResultEnvelope
from androidctl_contracts.daemon_api import CommandRunRequest

from androidctl.daemon.client import DaemonApiError
from androidctl.setup import verify
from tests.support import retained_result
from tests.support.daemon_fakes import ScriptedRecordingDaemon, patch_cli_context


def _connect_success(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(command="connect", envelope="bootstrap")
    )


def test_verify_setup_readiness_uses_connect_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            command_handlers={"connect": _connect_success},
        ),
    )

    result = verify.verify_setup_readiness(
        serial="device-1",
        token="secret-host-token",
        workspace_root=tmp_path,
    )

    assert result.command == "connect"
    assert result.envelope == "bootstrap"
    assert daemon.run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {
            "mode": "adb",
            "token": "secret-host-token",
            "serial": "device-1",
        },
    }
    assert daemon.runtime_calls[-1]["workspaceRoot"] == tmp_path.as_posix()


def test_verify_setup_readiness_discovers_daemon_for_workspace_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd"
    workspace_root = tmp_path / "workspace"
    cwd.mkdir()
    workspace_root.mkdir()
    resolved_workspace_root = workspace_root.resolve()
    discovery_calls: list[Path] = []
    daemons: list[ScriptedRecordingDaemon] = []

    def discover(workspace: Path) -> ScriptedRecordingDaemon:
        discovery_calls.append(workspace)
        daemon = ScriptedRecordingDaemon(
            root=workspace,
            command_handlers={"connect": _connect_success},
        )
        daemons.append(daemon)
        return daemon

    context = verify.run_pipeline.AppContext(
        daemon=None,
        cwd=cwd,
        env={},
        daemon_discovery=discover,
    )
    monkeypatch.setattr(verify.run_pipeline, "build_context", lambda: context)

    result = verify.verify_setup_readiness(
        serial="device-1",
        token="secret-host-token",
        workspace_root=workspace_root,
    )

    assert result.command == "connect"
    assert discovery_calls == [resolved_workspace_root]
    assert daemons[-1].runtime_calls[-1] == {
        "workspaceRoot": resolved_workspace_root.as_posix(),
        "artifactRoot": f"{resolved_workspace_root.as_posix()}/.androidctl",
    }
    assert daemons[-1].run_calls[-1]["command"] == {
        "kind": "connect",
        "connection": {
            "mode": "adb",
            "token": "secret-host-token",
            "serial": "device-1",
        },
    }


def test_verify_setup_readiness_defaults_workspace_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    discovery_calls: list[Path] = []

    def discover(workspace: Path) -> ScriptedRecordingDaemon:
        discovery_calls.append(workspace)
        return ScriptedRecordingDaemon(
            root=workspace,
            command_handlers={"connect": _connect_success},
        )

    context = verify.run_pipeline.AppContext(
        daemon=None,
        cwd=cwd,
        env={},
        daemon_discovery=discover,
    )
    monkeypatch.setattr(verify.run_pipeline, "build_context", lambda: context)

    verify.verify_setup_readiness(
        serial="device-1",
        token="secret-host-token",
        workspace_root=None,
    )

    assert discovery_calls == [cwd.resolve()]


def test_verify_setup_readiness_uses_workspace_root_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd"
    workspace_root = tmp_path / "workspace-from-env"
    cwd.mkdir()
    workspace_root.mkdir()
    discovery_calls: list[Path] = []

    def discover(workspace: Path) -> ScriptedRecordingDaemon:
        discovery_calls.append(workspace)
        return ScriptedRecordingDaemon(
            root=workspace,
            command_handlers={"connect": _connect_success},
        )

    context = verify.run_pipeline.AppContext(
        daemon=None,
        cwd=cwd,
        env={"ANDROIDCTL_WORKSPACE_ROOT": workspace_root.as_posix()},
        daemon_discovery=discover,
    )
    monkeypatch.setattr(verify.run_pipeline, "build_context", lambda: context)

    verify.verify_setup_readiness(
        serial="device-1",
        token="secret-host-token",
        workspace_root=None,
    )

    assert discovery_calls == [workspace_root.resolve()]


@pytest.mark.parametrize(
    ("code", "expected_layer"),
    [
        ("DEVICE_AGENT_UNAUTHORIZED", "auth"),
        ("ACCESSIBILITY_NOT_READY", "accessibility"),
        ("DEVICE_AGENT_UNAVAILABLE", "server"),
        ("DEVICE_AGENT_VERSION_MISMATCH", "daemon"),
    ],
)
def test_verify_setup_readiness_maps_retained_failure_layers(
    code: str,
    expected_layer: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def connect_failure(
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
                code=code,
                message="failed with token secret-host-token",
            )
        )

    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            command_handlers={"connect": connect_failure},
        ),
    )

    with pytest.raises(verify.SetupVerificationError) as exc_info:
        verify.verify_setup_readiness(
            serial="device-1",
            token="secret-host-token",
            workspace_root=None,
        )

    assert exc_info.value.code == code
    assert exc_info.value.layer == expected_layer
    assert "secret-host-token" not in exc_info.value.message
    assert "<redacted>" in exc_info.value.message


def test_verify_setup_readiness_retries_transient_readiness_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    def flaky_connect(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        nonlocal attempts
        del daemon, request, command
        attempts += 1
        if attempts == 1:
            return RetainedResultEnvelope.model_validate(
                retained_result(
                    command="connect",
                    envelope="bootstrap",
                    ok=False,
                    code="DEVICE_RPC_TRANSPORT_RESET",
                    message="device RPC transport was reset",
                )
            )
        return RetainedResultEnvelope.model_validate(
            retained_result(command="connect", envelope="bootstrap")
        )

    sleep_calls: list[float] = []
    monkeypatch.setattr(verify.time, "sleep", lambda delay: sleep_calls.append(delay))
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            command_handlers={"connect": flaky_connect},
        ),
    )

    result = verify.verify_setup_readiness(
        serial="device-1",
        token="secret-host-token",
        workspace_root=None,
        attempts=3,
        retry_delay_seconds=0.25,
    )

    assert result.command == "connect"
    assert attempts == 2
    assert len(daemon.run_calls) == 2
    assert sleep_calls == [0.25]


def test_verify_setup_readiness_does_not_retry_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    def unauthorized_connect(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        nonlocal attempts
        del daemon, request, command
        attempts += 1
        return RetainedResultEnvelope.model_validate(
            retained_result(
                command="connect",
                envelope="bootstrap",
                ok=False,
                code="DEVICE_AGENT_UNAUTHORIZED",
                message="wrong token secret-host-token",
            )
        )

    monkeypatch.setattr(
        verify.time,
        "sleep",
        lambda delay: pytest.fail(f"auth failure must not sleep: {delay}"),
    )
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            command_handlers={"connect": unauthorized_connect},
        ),
    )

    with pytest.raises(verify.SetupVerificationError) as exc_info:
        verify.verify_setup_readiness(
            serial="device-1",
            token="secret-host-token",
            workspace_root=None,
            attempts=3,
        )

    assert attempts == 1
    assert exc_info.value.code == "DEVICE_AGENT_UNAUTHORIZED"
    assert exc_info.value.layer == "auth"
    assert "secret-host-token" not in exc_info.value.message


def test_verify_setup_readiness_maps_daemon_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = verify.run_pipeline.AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda workspace_root: (_ for _ in ()).throw(
            RuntimeError(
                f"cannot start daemon with secret-host-token at {workspace_root}"
            )
        ),
    )
    monkeypatch.setattr(verify.run_pipeline, "build_context", lambda: context)

    with pytest.raises(verify.SetupVerificationError) as exc_info:
        verify.verify_setup_readiness(
            serial="device-1",
            token="secret-host-token",
            workspace_root=tmp_path,
        )

    assert exc_info.value.code == "DAEMON_UNAVAILABLE"
    assert exc_info.value.layer == "daemon"
    assert "secret-host-token" not in exc_info.value.message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_verify_setup_readiness_preserves_workspace_busy_from_daemon_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = verify.run_pipeline.AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda workspace_root: (_ for _ in ()).throw(
            DaemonApiError(
                code="WORKSPACE_BUSY",
                message=(
                    "workspace is controlled by another owner with secret-host-token"
                ),
                details={},
            )
        ),
    )
    monkeypatch.setattr(verify.run_pipeline, "build_context", lambda: context)

    with pytest.raises(verify.SetupVerificationError) as exc_info:
        verify.verify_setup_readiness(
            serial="device-1",
            token="secret-host-token",
            workspace_root=tmp_path,
        )

    assert exc_info.value.code == "WORKSPACE_BUSY"
    assert exc_info.value.layer == "daemon"
    assert "secret-host-token" not in exc_info.value.message
    assert "<redacted>" in exc_info.value.message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_verify_setup_readiness_rejects_unexpected_success_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def wrong_success(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        del daemon, request, command
        return RetainedResultEnvelope.model_validate(
            retained_result(command="close", envelope="lifecycle")
        )

    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            command_handlers={"connect": wrong_success},
        ),
    )

    with pytest.raises(verify.SetupVerificationError) as exc_info:
        verify.verify_setup_readiness(
            serial="device-1",
            token="secret-host-token",
            workspace_root=tmp_path,
        )

    assert exc_info.value.code == "SETUP_VERIFY_FAILED"
    assert exc_info.value.layer == "daemon"
