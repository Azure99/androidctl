from __future__ import annotations

import io
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

import pytest

import androidctld
import androidctld.daemon.service as daemon_service_module
from androidctl_contracts.daemon_api import (
    OWNER_HEADER_NAME,
    TOKEN_HEADER_NAME,
    DaemonErrorEnvelope,
)
from androidctld.auth.active_registry import ActiveDaemonRegistry
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.commands.command_models import ObserveCommand
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.service import CommandService
from androidctld.config import DaemonConfig
from androidctld.daemon.envelope import error_envelope, success_envelope
from androidctld.daemon.ingress import DaemonIngress
from androidctld.daemon.ownership_probe import (
    OwnershipHealthProbeResult,
    OwnershipHealthStatus,
)
from androidctld.daemon.server import AndroidctldHttpServer
from androidctld.daemon.service import DaemonService
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.store import RuntimeStore

from ..support.runtime_store import runtime_store_for_workspace
from .support.retained import assert_retained_omits_semantic_fields

REMOVED_COMMAND_KIND = "ra" + "w"


class FakeOwnershipHealthProbe:
    def __init__(self, result: OwnershipHealthProbeResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def probe(self, **kwargs: object) -> OwnershipHealthProbeResult:
        self.calls.append(dict(kwargs))
        return self.result


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeRuntimeStore:
    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self.runtime = runtime

    def get_runtime(self) -> WorkspaceRuntime:
        return self.runtime

    def persist_runtime(self, runtime: WorkspaceRuntime) -> None:
        del runtime

    @contextmanager
    def begin_serial_command(self, command_name: str) -> Iterator[None]:
        del command_name
        yield

    def close_runtime(self) -> WorkspaceRuntime:
        self.runtime.status = RuntimeStatus.CLOSED
        return self.runtime


class BusyCommandService:
    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self._runtime = runtime
        self.run_calls = 0

    def run(
        self,
        *,
        command: Any,
    ) -> dict[str, Any]:
        del command
        self.run_calls += 1
        raise DaemonError(
            code=DaemonErrorCode.RUNTIME_BUSY,
            message="runtime is busy",
            retryable=True,
            details={},
            http_status=200,
        )

    def close_runtime(self) -> dict[str, Any]:
        self._runtime.status = RuntimeStatus.CLOSED
        self._runtime.current_screen_id = None
        return {
            "ok": True,
            "command": "close",
            "envelope": "lifecycle",
            "artifacts": {},
            "details": {},
        }


def _public_screen_payload(screen_id: str) -> dict[str, object]:
    return {
        "screenId": screen_id,
        "app": {"packageName": "com.android.settings"},
        "surface": {
            "keyboardVisible": False,
            "focus": {},
        },
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {"name": "context", "nodes": []},
            {"name": "dialog", "nodes": []},
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [],
    }


def _observe_result_payload(
    *,
    screen_id: str,
    summary: str,
    command: str = "observe",
    category: str = "observe",
) -> dict[str, object]:
    del summary
    return {
        "ok": True,
        "command": command,
        "category": category,
        "payloadMode": "full",
        "nextScreenId": screen_id,
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "authoritative",
        },
        "screen": _public_screen_payload(screen_id),
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


class RuntimeApiHarness:
    def __init__(self, tmp_path: Path) -> None:
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        artifact_root = workspace_root / ".androidctl"
        runtime = WorkspaceRuntime(
            workspace_root=workspace_root,
            artifact_root=artifact_root,
            runtime_path=artifact_root / "runtime.json",
            status=RuntimeStatus.CONNECTED,
            screen_sequence=3,
            current_screen_id="screen-00003",
        )
        self.token = "daemon-token"
        self.owner_id = "shell:self:1"
        self.runtime_store = FakeRuntimeStore(runtime)
        self.command_service = BusyCommandService(runtime)
        self.service = DaemonService(
            runtime_store=self.runtime_store,
            command_service=self.command_service,
            bound_owner_id=self.owner_id,
        )
        self.ingress = DaemonIngress(
            token_provider=lambda: self.token,
            owner_id_provider=lambda: self.owner_id,
            dispatcher=self.service,
        )

    def auth_headers(self) -> dict[str, str]:
        return {
            TOKEN_HEADER_NAME: self.token,
            OWNER_HEADER_NAME: self.owner_id,
        }

    def post(
        self, path: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> FakeResponse:
        try:
            result = self.ingress.handle(
                method="POST",
                path=path,
                headers=headers,
                body=(__import__("json").dumps(json)).encode("utf-8"),
            )
        except DaemonError as error:
            return FakeResponse(error_envelope(error))
        return FakeResponse(success_envelope(result.payload))


@pytest.fixture
def server(tmp_path: Path) -> RuntimeApiHarness:
    return RuntimeApiHarness(tmp_path)


def test_health_rejects_wrong_owner_even_with_valid_token(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/health",
        headers={
            TOKEN_HEADER_NAME: server.token,
            OWNER_HEADER_NAME: "shell:other:1",
        },
        json={},
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "WORKSPACE_BUSY"


def test_runtime_close_returns_unified_close_result_without_inline_shutdown(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/runtime/close",
        headers=server.auth_headers(),
        json={},
    )

    assert response.json()["ok"] is True
    assert response.json()["result"] == {
        "ok": True,
        "command": "close",
        "envelope": "lifecycle",
        "artifacts": {},
        "details": {},
    }


def test_runtime_get_omits_screen_sequence(server: RuntimeApiHarness) -> None:
    response = server.post(
        "/runtime/get",
        headers=server.auth_headers(),
        json={},
    )

    payload = response.json()["result"]["runtime"]
    assert "screenSequence" not in payload
    assert "currentScreenId" not in payload


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/v1/health", {}),
        ("/v1/runtime/get", {}),
        ("/v1/runtime/close", {}),
        ("/v1/commands/run", {"command": {"kind": "observe"}}),
    ],
)
def test_v1_routes_are_rejected_as_path_not_found(
    server: RuntimeApiHarness,
    path: str,
    payload: dict[str, Any],
) -> None:
    response = server.post(
        path,
        headers=server.auth_headers(),
        json=payload,
    )

    assert response.json() == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "path not found",
            "retryable": False,
            "details": {"path": path},
        },
    }


def test_health_supports_snake_case_contract_constructor(
    server: RuntimeApiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHealthResult:
        model_fields: ClassVar[dict[str, object]] = {
            "workspaceRoot": object(),
            "ownerId": object(),
        }

        def __init__(
            self,
            *,
            service: str,
            version: str,
            workspace_root: str,
            owner_id: str,
        ) -> None:
            self.service = service
            self.version = version
            self.workspace_root = workspace_root
            self.owner_id = owner_id

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "service": self.service,
                "version": self.version,
                "workspaceRoot": self.workspace_root,
                "ownerId": self.owner_id,
            }

    monkeypatch.setattr(daemon_service_module, "HealthResult", FakeHealthResult)

    response = server.post(
        "/health",
        headers=server.auth_headers(),
        json={},
    )

    assert response.json() == {
        "ok": True,
        "result": {
            "service": "androidctld",
            "version": androidctld.__version__,
            "workspaceRoot": server.runtime_store.runtime.workspace_root.as_posix(),
            "ownerId": server.owner_id,
        },
    }


def test_runtime_get_rejects_non_empty_payload_with_structured_bad_request(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/runtime/get",
        headers=server.auth_headers(),
        json={"x": 1},
    )

    payload = response.json()

    assert payload == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "runtime/get does not accept request payload",
            "retryable": False,
            "details": {},
        },
    }


def test_commands_run_rejects_close_kind(server: RuntimeApiHarness) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={"command": {"kind": "close"}},
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "DAEMON_BAD_REQUEST"


def test_commands_run_rejects_removed_command_kind_before_dispatch(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "command": {
                "kind": REMOVED_COMMAND_KIND,
                "subcommand": "rpc",
                "payload": {
                    "query": "text=secret",
                    "body": {"token": "secret-token"},
                },
            }
        },
    )

    payload = response.json()

    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_BAD_REQUEST"
    assert payload["error"]["message"] == "unsupported command kind"
    assert payload["error"]["details"] == {
        "field": "command.kind",
        "kind": REMOVED_COMMAND_KIND,
    }
    assert "result" not in payload
    assert server.command_service.run_calls == 0
    payload_text = str(payload)
    for unsafe_fragment in ("text=secret", "body", "secret-token"):
        assert unsafe_fragment not in payload_text


def test_commands_run_accepts_global_action_without_source_screen_id(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={"command": {"kind": "back"}},
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "RUNTIME_BUSY"


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        (
            {
                "command": {
                    "kind": "wait",
                    "predicate": {"kind": "idle"},
                    "timeoutMs": "100",
                }
            },
            "command.timeoutMs",
        ),
        (
            {
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "lan",
                        "token": "t",
                        "host": "127.0.0.1",
                        "port": True,
                    },
                }
            },
            "command.connection.port",
        ),
    ],
)
def test_commands_run_rejects_malformed_scalar_values_at_daemon_ingress(
    server: RuntimeApiHarness,
    payload: dict[str, Any],
    expected_field: str,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json=payload,
    )

    assert response.json() == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": f"{expected_field} must be an integer",
            "retryable": False,
            "details": {
                "field": expected_field,
            },
        },
    }
    envelope = DaemonErrorEnvelope.model_validate(response.json())
    assert envelope.error.code.value == "DAEMON_BAD_REQUEST"


def test_error_envelope_serializes_runtime_busy_through_shared_model() -> None:
    payload = error_envelope(
        DaemonError(
            code=DaemonErrorCode.RUNTIME_BUSY,
            message="runtime is busy",
            retryable=False,
            details={},
            http_status=200,
        )
    )

    assert payload == {
        "ok": False,
        "error": {
            "code": "RUNTIME_BUSY",
            "message": "runtime is busy",
            "retryable": False,
            "details": {},
        },
    }
    assert (
        DaemonErrorEnvelope.model_validate(payload).error.code.value == "RUNTIME_BUSY"
    )


def test_daemon_error_to_contract_error_rejects_semantic_only_code() -> None:
    with pytest.raises(ValueError, match="OPEN_FAILED"):
        DaemonError(
            code=DaemonErrorCode.OPEN_FAILED,
            message="open failed",
            retryable=False,
            details={},
            http_status=200,
        ).to_contract_error()


def test_error_envelope_fails_closed_for_semantic_only_code() -> None:
    payload = error_envelope(
        DaemonError(
            code=DaemonErrorCode.OPEN_FAILED,
            message="open failed",
            retryable=False,
            details={"target": "app"},
            http_status=200,
        )
    )

    assert payload == {
        "ok": False,
        "error": {
            "code": "INTERNAL_COMMAND_FAILURE",
            "message": "unexpected daemon failure",
            "retryable": False,
            "details": {},
        },
    }
    assert (
        DaemonErrorEnvelope.model_validate(payload).error.code.value
        == "INTERNAL_COMMAND_FAILURE"
    )


def test_daemon_error_rejects_unknown_code_at_construction() -> None:
    with pytest.raises(ValueError, match="NOT_REAL"):
        DaemonError(
            code="NOT_REAL",
            message="invalid daemon error",
            retryable=False,
            details={},
            http_status=200,
        )


@pytest.mark.parametrize(
    ("payload", "message", "details"),
    [
        ({}, "command is required", {"field": "command"}),
        (
            {"command": "observe"},
            "command must be a JSON object",
            {"field": "command"},
        ),
        (
            {"command": {}},
            "command.kind must be a string",
            {"field": "command.kind"},
        ),
        (
            {"command": {"kind": 1}},
            "command.kind must be a string",
            {"field": "command.kind"},
        ),
        (
            {"command": {"kind": "   "}},
            "command.kind must be a non-empty string",
            {"field": "command.kind"},
        ),
        (
            {"command": {"kind": " close "}},
            "unsupported command kind",
            {"field": "command.kind", "kind": "close"},
        ),
        (
            {"command": {"kind": REMOVED_COMMAND_KIND}},
            "unsupported command kind",
            {"field": "command.kind", "kind": REMOVED_COMMAND_KIND},
        ),
    ],
)
def test_commands_run_preserves_command_kind_bad_request_contract(
    server: RuntimeApiHarness,
    payload: dict[str, Any],
    message: str,
    details: dict[str, Any],
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json=payload,
    )

    assert response.json() == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": message,
            "retryable": False,
            "details": details,
        },
    }


def test_commands_run_rejects_scroll_direction_outside_shared_contract(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "command": {
                "kind": "scroll",
                "ref": "node-1",
                "sourceScreenId": "screen-1",
                "direction": "forward",
            }
        },
    )

    assert response.json() == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "command.direction is invalid",
            "retryable": False,
            "details": {
                "field": "command.direction",
            },
        },
    }


def test_runtime_close_requests_shutdown_after_response_write_before_owner_release(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )
    events: list[object] = []
    stop_requested = False

    def request_shutdown() -> None:
        nonlocal stop_requested
        stop_requested = True
        events.append(
            (
                "shutdown",
                config.owner_lock_path.exists(),
                config.active_file_path.exists(),
            )
        )

    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=BusyCommandService(runtime),
        shutdown_callback=request_shutdown,
    )
    server._active_slot.acquire()
    active_record = server._active_slot.prepare(
        host=config.host,
        port=17171,
        token=token,
    )
    server._active_slot.publish(active_record)

    class RecordingWriter:
        def __init__(self) -> None:
            self.body = b""

        def write(self, data: bytes) -> None:
            events.append(
                (
                    "write",
                    stop_requested,
                    config.owner_lock_path.exists(),
                    config.active_file_path.exists(),
                )
            )
            self.body += data

    class FakeHandler:
        def __init__(self) -> None:
            payload = json.dumps({}).encode("utf-8")
            self.command = "POST"
            self.path = "/runtime/close"
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(payload)),
            }
            self.rfile = io.BytesIO(payload)
            self.wfile = RecordingWriter()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            events.append("headers-finished")

    handler = FakeHandler()

    server._handle(handler)

    payload = json.loads(handler.wfile.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["result"]["command"] == "close"
    assert payload["result"]["envelope"] == "lifecycle"
    assert_retained_omits_semantic_fields(payload["result"])
    assert ("write", False, True, True) in events
    assert config.owner_lock_path.exists() is True
    assert config.active_file_path.exists() is True
    assert events[-1] == ("shutdown", True, True)

    server.stop()

    assert config.owner_lock_path.exists() is False
    assert config.active_file_path.exists() is False


def test_http_runtime_close_busy_returns_retained_no_close_without_shutdown(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime_store = RuntimeStore(config)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.current_screen_id = "screen-before-close"
    runtime_store.persist_runtime(runtime)
    shutdown_events: list[str] = []
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=runtime_store,
        shutdown_callback=lambda: shutdown_events.append("shutdown"),
    )

    class RecordingWriter:
        def __init__(self) -> None:
            self.body = b""

        def write(self, data: bytes) -> None:
            self.body += data

    class FakeHandler:
        def __init__(self) -> None:
            payload = json.dumps({}).encode("utf-8")
            self.command = "POST"
            self.path = "/runtime/close"
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(payload)),
            }
            self.rfile = io.BytesIO(payload)
            self.wfile = RecordingWriter()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            pass

    handler = FakeHandler()

    with runtime_store.begin_serial_command("observe"):
        server._handle(handler)

    payload = json.loads(handler.wfile.body.decode("utf-8"))
    assert handler.status_code == 200
    assert payload["ok"] is True
    assert payload["result"]["ok"] is False
    assert payload["result"]["command"] == "close"
    assert payload["result"]["envelope"] == "lifecycle"
    assert payload["result"]["code"] == "RUNTIME_BUSY"
    assert payload["result"]["details"] == {"reason": "overlapping_control_request"}
    assert_retained_omits_semantic_fields(payload["result"])
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.current_screen_id == "screen-before-close"
    assert server._closing is False
    assert server._shutdown_after_close_requested is False
    assert shutdown_events == []


def test_runtime_close_gate_rejects_followup_work_without_dispatch(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )

    class CloseThenFailCommandService(BusyCommandService):
        def __init__(self, runtime: WorkspaceRuntime) -> None:
            super().__init__(runtime)
            self.run_calls = 0

        def run(self, *, command: Any) -> dict[str, Any]:
            del command
            self.run_calls += 1
            raise AssertionError("commands/run should not dispatch after close")

    command_service = CloseThenFailCommandService(runtime)
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=command_service,
        shutdown_callback=lambda: None,
    )

    class RecordingWriter:
        def __init__(self) -> None:
            self.body = b""

        def write(self, data: bytes) -> None:
            self.body += data

    class FakeHandler:
        def __init__(
            self,
            path: str,
            payload: dict[str, Any],
            *,
            method: str = "POST",
        ) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.command = method
            self.path = path
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(encoded)),
            }
            self.rfile = io.BytesIO(encoded)
            self.wfile = RecordingWriter()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            pass

    close_handler = FakeHandler("/runtime/close", {})
    server._handle(close_handler)

    health_handler = FakeHandler("/health", {})
    server._handle(health_handler)

    health_payload = json.loads(health_handler.wfile.body.decode("utf-8"))
    assert health_payload["ok"] is True
    assert health_payload["result"]["service"] == "androidctld"
    assert health_payload["result"]["ownerId"] == config.owner_id

    followup_handler = FakeHandler(
        "/commands/run",
        {"command": {"kind": "screenshot"}},
    )
    server._handle(followup_handler)

    followup_payload = json.loads(followup_handler.wfile.body.decode("utf-8"))
    assert followup_payload["ok"] is False
    assert followup_payload["error"]["code"] == "RUNTIME_BUSY"
    assert followup_payload["error"]["details"] == {"reason": "daemon_shutting_down"}
    assert command_service.run_calls == 0

    rejected_followup_handler = FakeHandler(
        "/v1/commands/run",
        {"command": {"kind": "screenshot"}},
    )
    server._handle(rejected_followup_handler)

    rejected_followup_payload = json.loads(
        rejected_followup_handler.wfile.body.decode("utf-8")
    )
    assert rejected_followup_handler.status_code == 400
    assert rejected_followup_payload == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "path not found",
            "retryable": False,
            "details": {"path": "/v1/commands/run"},
        },
    }
    assert command_service.run_calls == 0

    non_post_handler = FakeHandler("/runtime/get", {}, method="GET")
    server._handle(non_post_handler)

    non_post_payload = json.loads(non_post_handler.wfile.body.decode("utf-8"))
    assert non_post_handler.status_code == 400
    assert non_post_payload["ok"] is False
    assert non_post_payload["error"]["code"] == "DAEMON_BAD_REQUEST"

    bad_path_handler = FakeHandler("/not-found", {})
    server._handle(bad_path_handler)

    bad_path_payload = json.loads(bad_path_handler.wfile.body.decode("utf-8"))
    assert bad_path_handler.status_code == 400
    assert bad_path_payload["ok"] is False
    assert bad_path_payload["error"]["code"] == "DAEMON_BAD_REQUEST"


def test_runtime_close_gate_rejects_followup_while_response_write_is_blocked(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )

    class CloseThenFailCommandService(BusyCommandService):
        def __init__(self, runtime: WorkspaceRuntime) -> None:
            super().__init__(runtime)
            self.run_calls = 0

        def run(self, *, command: Any) -> dict[str, Any]:
            del command
            self.run_calls += 1
            raise AssertionError("commands/run should not dispatch after close")

    command_service = CloseThenFailCommandService(runtime)
    shutdown_events: list[str] = []
    shutdown_called = threading.Event()

    def record_shutdown() -> None:
        shutdown_events.append("shutdown")
        shutdown_called.set()

    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=command_service,
        shutdown_callback=record_shutdown,
    )

    write_started = threading.Event()
    release_write = threading.Event()

    class RecordingWriter:
        def __init__(self, *, block: bool = False) -> None:
            self.block = block
            self.body = b""

        def write(self, data: bytes) -> None:
            if self.block:
                write_started.set()
                if not release_write.wait(timeout=2.0):
                    raise TimeoutError("close response write did not unblock")
            self.body += data

    class FakeHandler:
        def __init__(
            self,
            path: str,
            payload: dict[str, Any],
            *,
            block_write: bool = False,
        ) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.command = "POST"
            self.path = path
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(encoded)),
            }
            self.rfile = io.BytesIO(encoded)
            self.wfile = RecordingWriter(block=block_write)
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            pass

    close_errors: list[BaseException] = []
    close_handler = FakeHandler("/runtime/close", {}, block_write=True)

    def close_request() -> None:
        try:
            server._handle(close_handler)
        except BaseException as error:  # pragma: no cover - thread assertion aid
            close_errors.append(error)

    close_thread = threading.Thread(target=close_request)
    close_thread.start()

    assert write_started.wait(timeout=2.0)
    assert server._closing is True
    assert shutdown_called.is_set() is False

    followup_handler = FakeHandler(
        "/commands/run",
        {"command": {"kind": "screenshot"}},
    )
    server._handle(followup_handler)

    followup_payload = json.loads(followup_handler.wfile.body.decode("utf-8"))
    assert followup_payload["ok"] is False
    assert followup_payload["error"]["code"] == "RUNTIME_BUSY"
    assert followup_payload["error"]["details"] == {"reason": "daemon_shutting_down"}
    assert command_service.run_calls == 0
    assert shutdown_called.is_set() is False

    rejected_followup_handler = FakeHandler("/v1/runtime/get", {})
    server._handle(rejected_followup_handler)

    rejected_followup_payload = json.loads(
        rejected_followup_handler.wfile.body.decode("utf-8")
    )
    assert rejected_followup_handler.status_code == 400
    assert rejected_followup_payload == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "path not found",
            "retryable": False,
            "details": {"path": "/v1/runtime/get"},
        },
    }
    assert command_service.run_calls == 0
    assert shutdown_called.is_set() is False

    release_write.set()
    close_thread.join(timeout=2.0)

    assert close_thread.is_alive() is False
    assert close_errors == []
    assert shutdown_events == ["shutdown"]
    close_payload = json.loads(close_handler.wfile.body.decode("utf-8"))
    assert close_payload["ok"] is True


def test_runtime_close_failure_does_not_enter_closing_gate(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )

    class FailingCloseCommandService(BusyCommandService):
        def close_runtime(self) -> dict[str, Any]:
            raise DaemonError(
                code=DaemonErrorCode.DAEMON_BAD_REQUEST,
                message="close failed",
                retryable=False,
                details={"reason": "close_failed"},
                http_status=400,
            )

    shutdown_events: list[str] = []
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=FailingCloseCommandService(runtime),
        shutdown_callback=lambda: shutdown_events.append("shutdown"),
    )

    class RecordingWriter:
        def __init__(self) -> None:
            self.body = b""

        def write(self, data: bytes) -> None:
            self.body += data

    class FakeHandler:
        def __init__(self) -> None:
            payload = json.dumps({}).encode("utf-8")
            self.command = "POST"
            self.path = "/runtime/close"
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(payload)),
            }
            self.rfile = io.BytesIO(payload)
            self.wfile = RecordingWriter()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            pass

    handler = FakeHandler()
    server._handle(handler)

    payload = json.loads(handler.wfile.body.decode("utf-8"))
    assert handler.status_code == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_BAD_REQUEST"
    assert payload["error"]["details"] == {"reason": "close_failed"}
    assert server._closing is False
    assert shutdown_events == []

    status, followup_payload = server.handle(
        method="POST",
        path="/runtime/get",
        headers={},
        body=b"{}",
    )

    assert status == 200
    assert "runtime" in followup_payload


def test_runtime_close_write_failure_still_gates_and_requests_shutdown(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )
    logger = logging.getLogger(f"tests.androidctld.runtime_close.{id(tmp_path)}")
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO, logger=logger.name)
    shutdown_events: list[str] = []
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=BusyCommandService(runtime),
        logger=logger,
        shutdown_callback=lambda: shutdown_events.append("shutdown"),
    )

    class FailingWriter:
        def write(self, data: bytes) -> None:
            del data
            raise BrokenPipeError("client disconnected")

    class FakeHandler:
        def __init__(self) -> None:
            payload = json.dumps({}).encode("utf-8")
            self.command = "POST"
            self.path = "/runtime/close"
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": str(len(payload)),
            }
            self.rfile = io.BytesIO(payload)
            self.wfile = FailingWriter()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            pass

    server._handle(FakeHandler())

    assert runtime.status == RuntimeStatus.CLOSED
    assert shutdown_events == ["shutdown"]
    assert server._closing is True
    assert "client disconnected before response write completed" in caplog.text
    assert "response=close_success" in caplog.text
    assert "unexpected daemon failure" not in caplog.text

    with pytest.raises(DaemonError) as error:
        server.handle(
            method="POST",
            path="/runtime/get",
            headers={},
            body=b"{}",
        )

    assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
    assert error.value.details == {"reason": "daemon_shutting_down"}


def test_server_active_slot_restore_reads_current_workspace_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    other_workspace_root = tmp_path / "other-workspace"
    other_workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    other_config = DaemonConfig(
        workspace_root=other_workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    wrong_token_store = DaemonTokenStore(other_config)
    config.token_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.token_file_path.write_text(
        json.dumps({"token": "current-workspace-token"}),
        encoding="utf-8",
    )
    other_config.token_file_path.parent.mkdir(parents=True, exist_ok=True)
    other_config.token_file_path.write_text(
        json.dumps({"token": "wrong-workspace-token"}),
        encoding="utf-8",
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(
        host="127.0.0.1",
        port=17631,
        token="current-workspace-token",
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(
            OwnershipHealthStatus.LIVE_MATCH,
            token="current-workspace-token",
        )
    )
    monkeypatch.setattr(
        "androidctld.daemon.active_slot.OwnershipHealthProbe",
        lambda: probe,
    )
    server = AndroidctldHttpServer(
        config=config,
        token_store=wrong_token_store,
        active_registry=registry,
    )
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        server._active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "current-workspace-token"
    assert probe.calls[0]["tokens"] == ["current-workspace-token"]


def test_commands_run_uses_runtime_scoped_request_and_surfaces_runtime_busy(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "command": {"kind": "screenshot"},
        },
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "RUNTIME_BUSY"


def test_commands_run_removed_command_kind_returns_bad_request(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifact_root = workspace_root / ".androidctl"
    workspace_root.mkdir()
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=artifact_root,
        runtime_path=artifact_root / "runtime.json",
        status=RuntimeStatus.CONNECTED,
        screen_sequence=1,
        current_screen_id="screen-00001",
    )
    runtime_store = FakeRuntimeStore(runtime)

    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )

    with pytest.raises(DaemonError) as error:
        service.handle(
            method="POST",
            path="/commands/run",
            headers={TOKEN_HEADER_NAME: "daemon-token"},
            body=json.dumps(
                {
                    "command": {
                        "kind": REMOVED_COMMAND_KIND,
                        "subcommand": "screen",
                    },
                }
            ).encode("utf-8"),
        )

    assert error.value.code == DaemonErrorCode.DAEMON_BAD_REQUEST
    assert error.value.message == "unsupported command kind"
    assert error.value.details == {
        "field": "command.kind",
        "kind": REMOVED_COMMAND_KIND,
    }


def test_http_server_unknown_exception_uses_internal_command_failure(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    token = token_store.current_token()
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(
            WorkspaceRuntime(
                workspace_root=workspace_root,
                artifact_root=workspace_root / ".androidctl",
                runtime_path=workspace_root / ".androidctl" / "runtime.json",
                status=RuntimeStatus.READY,
            )
        ),
        command_service=BusyCommandService(
            WorkspaceRuntime(
                workspace_root=workspace_root,
                artifact_root=workspace_root / ".androidctl",
                runtime_path=workspace_root / ".androidctl" / "runtime.json",
                status=RuntimeStatus.READY,
            )
        ),
    )

    def _boom(**kwargs: Any) -> Any:
        del kwargs
        raise RuntimeError("boom")

    server._ingress.handle = _boom  # type: ignore[method-assign]

    class _Writer:
        def __init__(self) -> None:
            self.body = b""

        def write(self, data: bytes) -> None:
            self.body += data

    class _Handler:
        def __init__(self) -> None:
            self.command = "POST"
            self.path = "/health"
            self.headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: config.owner_id,
                "Content-Length": "2",
            }
            self.rfile = io.BytesIO(b"{}")
            self.wfile = _Writer()
            self.status_code: int | None = None
            self.sent_headers: list[tuple[str, str]] = []

        def send_response(self, status_code: int) -> None:
            self.status_code = status_code

        def send_header(self, name: str, value: str) -> None:
            self.sent_headers.append((name, value))

        def end_headers(self) -> None:
            return None

    handler = _Handler()

    server._handle(handler)

    payload = json.loads(handler.wfile.body.decode("utf-8"))
    assert handler.status_code == 500
    assert payload == {
        "ok": False,
        "error": {
            "code": "INTERNAL_COMMAND_FAILURE",
            "message": "unexpected daemon failure",
            "retryable": False,
            "details": {},
        },
    }


def test_runtime_close_ignores_progress_lock_busy_state(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime_store.persist_runtime(runtime)

    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )

    acquired = runtime.progress_lock.acquire(blocking=False)
    assert acquired is True
    try:
        status, payload = service.handle(
            method="POST",
            path="/runtime/close",
            headers={TOKEN_HEADER_NAME: "daemon-token"},
            body=b"{}",
        )
    finally:
        runtime.progress_lock.release()

    assert status == 200
    assert payload == {
        "ok": True,
        "command": "close",
        "envelope": "lifecycle",
        "artifacts": {},
        "details": {},
    }

    persisted_runtime = json.loads(runtime.runtime_path.read_text())
    assert persisted_runtime["status"] == "closed"


def test_runtime_close_does_not_change_observe_execution_semantics(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    observe_calls = 0

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        nonlocal observe_calls
        del command
        observe_calls += 1
        return _observe_result_payload(
            screen_id=f"screen-{observe_calls}",
            summary=f"observe call {observe_calls}",
        )

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    daemon = DaemonService(
        runtime_store=runtime_store,
        command_service=service,
    )

    first_status, first_payload = daemon.handle(
        method="POST",
        path="/commands/run",
        headers={TOKEN_HEADER_NAME: "daemon-token"},
        body=json.dumps({"command": {"kind": "observe"}}).encode("utf-8"),
    )
    close_status, close_payload = daemon.handle(
        method="POST",
        path="/runtime/close",
        headers={TOKEN_HEADER_NAME: "daemon-token"},
        body=b"{}",
    )
    second_status, second_payload = daemon.handle(
        method="POST",
        path="/commands/run",
        headers={TOKEN_HEADER_NAME: "daemon-token"},
        body=json.dumps({"command": {"kind": "observe"}}).encode("utf-8"),
    )

    assert first_status == 200
    assert close_status == 200
    assert second_status == 200
    assert first_payload == _observe_result_payload(
        screen_id="screen-1",
        summary="observe call 1",
    )
    assert close_payload["command"] == "close"
    assert close_payload["envelope"] == "lifecycle"
    assert_retained_omits_semantic_fields(close_payload)
    assert second_payload == _observe_result_payload(
        screen_id="screen-2",
        summary="observe call 2",
    )
    assert observe_calls == 2


def test_commands_run_emits_canonical_command_result_without_explicit_nulls(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        return {
            **_observe_result_payload(
                screen_id="screen-1",
                summary="observe call 1",
            ),
            "sourceScreenId": None,
            "code": None,
            "message": None,
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "none",
                "observationQuality": "authoritative",
                "changed": None,
            },
            "artifacts": {"screenshotPng": None},
        }

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )
    daemon = DaemonService(
        runtime_store=runtime_store,
        command_service=service,
    )

    status, payload = daemon.handle(
        method="POST",
        path="/commands/run",
        headers={TOKEN_HEADER_NAME: "daemon-token"},
        body=json.dumps({"command": {"kind": "observe"}}).encode("utf-8"),
    )

    assert status == 200
    assert payload["command"] == "observe"
    assert payload["payloadMode"] == "full"
    assert "sourceScreenId" not in payload
    assert "code" not in payload
    assert "message" not in payload
    assert "changed" not in payload["truth"]
    assert payload["artifacts"] == {}
    assert payload["nextScreenId"] == "screen-1"
    assert payload["screen"]["screenId"] == "screen-1"


def test_commands_run_rejects_client_command_id_field(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "clientCommandId": "observe-1",
            "command": {"kind": "observe"},
        },
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "DAEMON_BAD_REQUEST"
    assert response.json()["error"]["details"] == {
        "field": "root",
        "unknownFields": ["clientCommandId"],
    }


def test_commands_run_accepts_omitted_options(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    seen: list[str] = []

    class RecordingCommandService:
        def run(
            self,
            *,
            command: Any,
        ) -> dict[str, Any]:
            seen.append(command.kind)
            return {"command": command.kind}

        def close_runtime(self) -> dict[str, Any]:
            raise AssertionError("close_runtime should not be called")

    daemon = DaemonService(
        runtime_store=runtime_store,
        command_service=RecordingCommandService(),  # type: ignore[arg-type]
    )

    first_status, first_payload = daemon.handle(
        method="POST",
        path="/commands/run",
        headers={TOKEN_HEADER_NAME: "daemon-token"},
        body=json.dumps({"command": {"kind": "observe"}}).encode("utf-8"),
    )

    assert first_status == 200
    assert first_payload["command"] == "observe"
    assert seen == ["observe"]


def test_commands_run_accepts_trimmed_command_kind(server: RuntimeApiHarness) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "command": {"kind": " observe "},
        },
    )

    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "RUNTIME_BUSY"


def test_commands_run_rejects_options_root_field(
    server: RuntimeApiHarness,
) -> None:
    response = server.post(
        "/commands/run",
        headers=server.auth_headers(),
        json={
            "command": {"kind": "observe"},
            "options": {"debug": True},
        },
    )

    assert response.json() == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "root has unsupported fields",
            "retryable": False,
            "details": {
                "field": "root",
                "unknownFields": ["options"],
            },
        },
    }
