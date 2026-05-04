from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import androidctl_contracts.daemon_api as wire_api
import androidctld.schema.daemon_api as daemon_api_module
from androidctl_contracts.command_catalog import DAEMON_COMMAND_KINDS
from androidctl_contracts.command_results import ActionTargetPayload
from androidctl_contracts.daemon_api import (
    CommandRunRequest as WireCommandRunRequest,
)
from androidctl_contracts.daemon_api import (
    GlobalActionCommandPayload,
    OpenCommandPayload,
    WaitCommandPayload,
)
from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.actions.executor import ActionExecutionFailure, ActionExecutor
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.command_models import (
    AppWaitPredicate,
    ConnectCommand,
    ListAppsCommand,
    ObserveCommand,
    OpenCommand,
    ScreenshotCommand,
    WaitCommand,
    WaitKind,
)
from androidctld.commands.from_boundary import (
    compile_global_action_command,
    compile_open_command,
    compile_ref_action_command,
    compile_service_wait_command,
)
from androidctld.commands.handlers.action import ActionCommandHandler
from androidctld.commands.handlers.observe import ObserveCommandHandler
from androidctld.commands.handlers.screenshot import ScreenshotCommandHandler
from androidctld.commands.handlers.wait import WaitCommandHandler
from androidctld.commands.registry import resolve_command_spec
from androidctld.commands.result_models import (
    CommandAppPayload,
    SemanticResultAssemblyInput,
)
from androidctld.commands.service import CommandService
from androidctld.daemon.service import DaemonService
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    ConnectionSpec,
    DeviceCapabilities,
    DeviceEndpoint,
    RuntimeTransport,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.runtime import RuntimeKernel, capture_lifecycle_lease
from androidctld.runtime.models import ScreenState
from androidctld.runtime_policy import DEFAULT_DEVICE_PORT, SCREENSHOT_MAX_OUTPUT_PIXELS
from androidctld.schema.daemon_api import parse_command_run_request
from androidctld.schema.validation_errors import validation_error_to_bad_request
from androidctld.semantics.public_models import PublicNode
from androidctld.waits.evaluators import WaitMatchData
from androidctld.waits.loop import WaitLoopTimedOut

from ..support.runtime_store import runtime_store_for_workspace
from .support.doubles import (
    CallbackScreenRefresh,
    PassiveRuntimeKernel,
    StaticScreenRefresh,
    StaticSnapshotService,
)
from .support.retained import assert_retained_omits_semantic_fields
from .support.runtime import (
    build_artifact_path,
    build_connected_runtime,
    build_screen_artifacts,
    install_screen_state,
)
from .support.semantic_screen import (
    make_compiled_screen as _make_compiled_screen,
)
from .support.semantic_screen import (
    make_public_screen as _make_screen,
)
from .support.semantic_screen import (
    make_snapshot as _make_snapshot,
)

REMOVED_COMMAND_KIND = "ra" + "w"

_VALID_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_VALID_PNG_1X1_BASE64 = base64.b64encode(_VALID_PNG_1X1).decode("ascii")

_COMMAND_RUN_ADAPTER_CASES: tuple[
    tuple[str, dict[str, object], type[object], dict[str, object]],
    ...,
] = (
    (
        "connect-adb-default-port",
        {
            "kind": "connect",
            "connection": {
                "mode": "adb",
                "token": "device-token",
                "serial": "emulator-5554",
            },
        },
        wire_api.ConnectCommandPayload,
        {
            "kind": "connect",
            "connection": {
                "mode": "adb",
                "token": "device-token",
                "serial": "emulator-5554",
                "port": DEFAULT_DEVICE_PORT,
            },
        },
    ),
    (
        "connect-lan-explicit-port",
        {
            "kind": "connect",
            "connection": {
                "mode": "lan",
                "token": "device-token",
                "host": "192.168.0.10",
                "port": 17171,
            },
        },
        wire_api.ConnectCommandPayload,
        {
            "kind": "connect",
            "connection": {
                "mode": "lan",
                "token": "device-token",
                "host": "192.168.0.10",
                "port": 17171,
            },
        },
    ),
    (
        "observe",
        {"kind": "observe"},
        wire_api.ObserveCommandPayload,
        {"kind": "observe"},
    ),
    (
        "listApps",
        {"kind": "listApps"},
        wire_api.ListAppsCommandPayload,
        {"kind": "listApps"},
    ),
    (
        "open-app",
        {
            "kind": "open",
            "target": {"kind": "app", "value": "com.example.settings"},
        },
        wire_api.OpenCommandPayload,
        {
            "kind": "open",
            "target": {"kind": "app", "value": "com.example.settings"},
        },
    ),
    (
        "open-url",
        {
            "kind": "open",
            "target": {"kind": "url", "value": "https://example.test/path"},
        },
        wire_api.OpenCommandPayload,
        {
            "kind": "open",
            "target": {"kind": "url", "value": "https://example.test/path"},
        },
    ),
    (
        "tap",
        {"kind": "tap", "ref": "n1", "sourceScreenId": "screen-1"},
        wire_api.RefActionCommandPayload,
        {"kind": "tap", "ref": "n1", "sourceScreenId": "screen-1"},
    ),
    (
        "longTap",
        {"kind": "longTap", "ref": "n1", "sourceScreenId": "screen-1"},
        wire_api.RefActionCommandPayload,
        {"kind": "longTap", "ref": "n1", "sourceScreenId": "screen-1"},
    ),
    (
        "focus",
        {"kind": "focus", "ref": "n1", "sourceScreenId": "screen-1"},
        wire_api.RefActionCommandPayload,
        {"kind": "focus", "ref": "n1", "sourceScreenId": "screen-1"},
    ),
    (
        "submit",
        {"kind": "submit", "ref": "n1", "sourceScreenId": "screen-1"},
        wire_api.RefActionCommandPayload,
        {"kind": "submit", "ref": "n1", "sourceScreenId": "screen-1"},
    ),
    (
        "type",
        {
            "kind": "type",
            "ref": "n1",
            "sourceScreenId": "screen-1",
            "text": "hello",
        },
        wire_api.TypeCommandPayload,
        {
            "kind": "type",
            "ref": "n1",
            "sourceScreenId": "screen-1",
            "text": "hello",
        },
    ),
    (
        "scroll",
        {
            "kind": "scroll",
            "ref": "n1",
            "sourceScreenId": "screen-1",
            "direction": "down",
        },
        wire_api.ScrollCommandPayload,
        {
            "kind": "scroll",
            "ref": "n1",
            "sourceScreenId": "screen-1",
            "direction": "down",
        },
    ),
    (
        "back",
        {"kind": "back", "sourceScreenId": "screen-1"},
        wire_api.GlobalActionCommandPayload,
        {"kind": "back", "sourceScreenId": "screen-1"},
    ),
    (
        "home",
        {"kind": "home", "sourceScreenId": "screen-1"},
        wire_api.GlobalActionCommandPayload,
        {"kind": "home", "sourceScreenId": "screen-1"},
    ),
    (
        "recents",
        {"kind": "recents", "sourceScreenId": "screen-1"},
        wire_api.GlobalActionCommandPayload,
        {"kind": "recents", "sourceScreenId": "screen-1"},
    ),
    (
        "notifications",
        {"kind": "notifications", "sourceScreenId": "screen-1"},
        wire_api.GlobalActionCommandPayload,
        {"kind": "notifications", "sourceScreenId": "screen-1"},
    ),
    (
        "wait-text",
        {
            "kind": "wait",
            "predicate": {"kind": "text-present", "text": "Wi-Fi"},
            "timeoutMs": 100,
        },
        wire_api.WaitCommandPayload,
        {
            "kind": "wait",
            "predicate": {"kind": "text-present", "text": "Wi-Fi"},
            "timeoutMs": 100,
        },
    ),
    (
        "wait-screen-change",
        {
            "kind": "wait",
            "predicate": {"kind": "screen-change", "sourceScreenId": "screen-1"},
            "timeoutMs": 100,
        },
        wire_api.WaitCommandPayload,
        {
            "kind": "wait",
            "predicate": {"kind": "screen-change", "sourceScreenId": "screen-1"},
            "timeoutMs": 100,
        },
    ),
    (
        "wait-gone",
        {
            "kind": "wait",
            "predicate": {
                "kind": "gone",
                "sourceScreenId": "screen-1",
                "ref": "n7",
            },
        },
        wire_api.WaitCommandPayload,
        {
            "kind": "wait",
            "predicate": {
                "kind": "gone",
                "sourceScreenId": "screen-1",
                "ref": "n7",
            },
        },
    ),
    (
        "wait-app",
        {
            "kind": "wait",
            "predicate": {"kind": "app", "packageName": "com.example.settings"},
        },
        wire_api.WaitCommandPayload,
        {
            "kind": "wait",
            "predicate": {"kind": "app", "packageName": "com.example.settings"},
        },
    ),
    (
        "wait-idle",
        {"kind": "wait", "predicate": {"kind": "idle"}},
        wire_api.WaitCommandPayload,
        {"kind": "wait", "predicate": {"kind": "idle"}},
    ),
    (
        "screenshot",
        {"kind": "screenshot"},
        wire_api.ScreenshotCommandPayload,
        {"kind": "screenshot"},
    ),
)


def _png_header_base64(width_px: int, height_px: int) -> str:
    payload = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width_px.to_bytes(4, byteorder="big")
        + height_px.to_bytes(4, byteorder="big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    return base64.b64encode(payload).decode("ascii")


def _make_runtime(tmp_path: Path) -> Any:
    return build_connected_runtime(
        tmp_path,
        status=RuntimeStatus.CONNECTED,
        screen_sequence=1,
        current_screen_id="screen-1",
    )


def parse_command_request_payload(payload: dict[str, object]) -> object:
    try:
        return WireCommandRunRequest.model_validate(
            {"command": payload},
            strict=True,
        ).command
    except ValidationError as error:
        raise validation_error_to_bad_request(error, field_name="command") from error


def _make_runtime_kernel(runtime: Any) -> PassiveRuntimeKernel[Any]:
    return PassiveRuntimeKernel(
        runtime,
        lifecycle_lease_factory=capture_lifecycle_lease,
    )


class _UnusedCommandService:
    def run(
        self,
        *,
        command: Any,
    ) -> dict[str, Any]:
        del command
        raise AssertionError("run should not be called")

    def close_runtime(self) -> dict[str, Any]:
        raise AssertionError("close_runtime should not be called")


def _runtime_get_payload(runtime_store: Any) -> dict[str, object]:
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=_UnusedCommandService(),  # type: ignore[arg-type]
        bound_owner_id="shell:self:1",
    )
    _, payload = service.handle(
        method="POST",
        path="/runtime/get",
        headers={},
        body=b"{}",
    )
    return payload


def _install_authoritative_screen(
    runtime: Any,
    *,
    screen_id: str = "screen-1",
    snapshot_id: int = 1,
    ref: str = "n1",
) -> None:
    snapshot = _make_snapshot(snapshot_id=snapshot_id)
    compiled_screen = _make_compiled_screen(
        screen_id,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="" if snapshot.package_name is None else snapshot.package_name,
        activity_name=snapshot.activity_name,
        fingerprint=f"fingerprint-{screen_id}",
        ref=ref,
    )
    runtime.status = RuntimeStatus.READY
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=compiled_screen.to_public_screen(),
        compiled_screen=compiled_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=screen_id),
    )


def test_registry_exposes_semantic_command_set() -> None:
    assert resolve_command_spec("observe").command_name == "observe"
    assert resolve_command_spec("wait").command_name == "wait"


def test_wait_payload_uses_one_predicate_shape() -> None:
    payload = parse_command_request_payload(
        {
            "kind": "wait",
            "predicate": {"kind": "screen-change", "sourceScreenId": "screen-1"},
            "timeoutMs": 2_000,
        }
    )

    assert payload.kind == "wait"
    assert payload.predicate.kind == "screen-change"


def test_wait_payload_supports_gone_predicate_shape() -> None:
    payload = parse_command_request_payload(
        {
            "kind": "wait",
            "predicate": {
                "kind": "gone",
                "sourceScreenId": "screen-1",
                "ref": "n7",
            },
            "timeoutMs": 2_000,
        }
    )

    assert payload.kind == "wait"
    assert payload.predicate.kind == "gone"
    assert payload.predicate.source_screen_id == "screen-1"
    assert payload.predicate.ref == "n7"


def test_wait_payload_rejects_gone_text_predicate() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "sourceScreenId": "screen-1",
                    "text": "Wi-Fi",
                },
                "timeoutMs": 2_000,
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_wait_payload_rejects_gone_ref_without_source_screen_id() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "ref": "n7",
                },
                "timeoutMs": 2_000,
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_global_action_payload_accepts_missing_source_screen_id() -> None:
    payload = parse_command_request_payload({"kind": "back"})

    assert payload.kind == "back"
    assert payload.source_screen_id is None


def test_global_action_payload_accepts_source_screen_id() -> None:
    payload = parse_command_request_payload(
        {
            "kind": "back",
            "sourceScreenId": "screen-1",
        }
    )

    assert payload.kind == "back"
    assert payload.source_screen_id == "screen-1"


def test_ref_action_payload_rejects_non_string_source_screen_id() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": "tap",
                "ref": "n3",
                "sourceScreenId": 7,
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_type_payload_rejects_unknown_extra_field() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": "type",
                "ref": "n1",
                "sourceScreenId": "screen-1",
                "text": "hello",
                "extra": True,
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_removed_command_kind_rejects_unknown_extra_field() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": REMOVED_COMMAND_KIND,
                "subcommand": "rpc",
                "payload": {"method": "device.ping", "params": {}},
                "extra": True,
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_list_apps_payload_rejects_unknown_extra_field_with_normalized_path() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_run_request(
            {
                "command": {
                    "kind": "listApps",
                    "includeSystem": True,
                }
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"
    assert error.value.details["field"] == "command"
    assert error.value.details["unknownFields"] == ["includeSystem"]


def test_removed_command_kind_rejects_at_parser_boundary() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_request_payload(
            {
                "kind": REMOVED_COMMAND_KIND,
                "subcommand": "rpc",
                "payload": {"method": "device.ping", "params": {}},
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_command_run_request_adapts_shared_wait_payload_to_internal_models() -> None:
    request = parse_command_run_request(
        {
            "command": {
                "kind": "wait",
                "predicate": {
                    "kind": "app",
                    "packageName": "com.example.settings",
                },
                "timeoutMs": 100,
            },
        }
    )

    assert isinstance(request.command, WaitCommand)
    assert request.command.timeout_ms == 100
    assert request.command.wait_kind is WaitKind.APP
    assert isinstance(request.command.predicate, AppWaitPredicate)
    assert request.command.predicate.package_name == "com.example.settings"


def test_command_run_request_adapts_list_apps_payload_to_internal_model() -> None:
    request = parse_command_run_request({"command": {"kind": "listApps"}})

    assert isinstance(request.command, ListAppsCommand)
    assert request.command.kind.value == "listApps"


def test_command_run_request_accepts_trimmed_open_target_kind() -> None:
    request = parse_command_run_request(
        {
            "command": {
                "kind": " open ",
                "target": {
                    "kind": " app ",
                    "value": "com.example.settings",
                },
            },
        }
    )

    assert isinstance(request.command, OpenCommand)
    assert request.command.kind.value == "open"
    assert request.command.target.package_name == "com.example.settings"


def test_command_run_request_accepts_trimmed_wait_predicate_kind() -> None:
    request = parse_command_run_request(
        {
            "command": {
                "kind": " wait ",
                "predicate": {
                    "kind": " app ",
                    "packageName": "com.example.settings",
                },
                "timeoutMs": 100,
            },
        }
    )

    assert isinstance(request.command, WaitCommand)
    assert request.command.kind.value == "wait"
    assert request.command.wait_kind is WaitKind.APP
    assert isinstance(request.command.predicate, AppWaitPredicate)
    assert request.command.predicate.package_name == "com.example.settings"
    assert request.command.timeout_ms == 100


def test_command_run_request_accepts_global_action_without_source_screen_id() -> None:
    request = parse_command_run_request(
        {
            "command": {
                "kind": "back",
            },
        }
    )

    assert request.command.kind.value == "global"
    assert request.command.action == "back"
    assert request.command.source_screen_id is None


def test_command_run_request_supports_snake_case_contract_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAppPredicatePayload:
        def __init__(self, *, package_name: str) -> None:
            self.kind = "app"
            self.package_name = package_name

        def model_dump(
            self,
            *,
            by_alias: bool,
            mode: str,
            exclude_none: bool,
        ) -> dict[str, object]:
            del by_alias, mode, exclude_none
            return {
                "kind": self.kind,
                "packageName": self.package_name,
            }

    class FakeWaitCommandPayload:
        def __init__(
            self,
            *,
            predicate: FakeAppPredicatePayload,
            timeout_ms: int | None,
        ) -> None:
            self.kind = "wait"
            self.predicate = predicate
            self.timeout_ms = timeout_ms

        def model_dump(
            self,
            *,
            by_alias: bool,
            mode: str,
            exclude_none: bool,
        ) -> dict[str, object]:
            del by_alias, mode, exclude_none
            return {
                "kind": self.kind,
                "predicate": self.predicate.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_none=True,
                ),
                "timeoutMs": self.timeout_ms,
            }

    class FakeBoundaryRequest:
        def __init__(self) -> None:
            self.command = FakeWaitCommandPayload(
                predicate=FakeAppPredicatePayload(package_name="com.example.settings"),
                timeout_ms=250,
            )

    class FakeCommandRunRequestModel:
        @staticmethod
        def model_validate(
            payload: dict[str, Any],
            strict: bool,
        ) -> FakeBoundaryRequest:
            del payload, strict
            return FakeBoundaryRequest()

    monkeypatch.setattr(
        daemon_api_module.wire_api,
        "CommandRunRequest",
        FakeCommandRunRequestModel,
    )
    monkeypatch.setattr(
        daemon_api_module.wire_api,
        "WaitCommandPayload",
        FakeWaitCommandPayload,
    )
    monkeypatch.setattr(
        daemon_api_module.wire_api,
        "AppPredicatePayload",
        FakeAppPredicatePayload,
    )

    request = parse_command_run_request(
        {
            "command": {
                "kind": "wait",
                "predicate": {
                    "kind": "app",
                    "packageName": "com.example.settings",
                },
                "timeoutMs": 100,
            },
        }
    )

    assert isinstance(request.command, WaitCommand)
    assert request.command.timeout_ms == 250
    assert request.command.wait_kind is WaitKind.APP
    assert isinstance(request.command.predicate, AppWaitPredicate)
    assert request.command.predicate.package_name == "com.example.settings"


def test_command_run_request_rejects_client_command_id() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_run_request(
            {
                "clientCommandId": "cli-connect",
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "adb",
                        "token": "device-token",
                        "serial": "emulator-5554",
                    },
                },
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"
    assert error.value.details == {
        "field": "root",
        "unknownFields": ["clientCommandId"],
    }


@pytest.mark.parametrize(
    ("case_id", "command_payload", "expected_model", "expected_dump"),
    _COMMAND_RUN_ADAPTER_CASES,
    ids=[case[0] for case in _COMMAND_RUN_ADAPTER_CASES],
)
def test_command_run_request_adapter_parity_with_shared_contract(
    case_id: str,
    command_payload: dict[str, object],
    expected_model: type[object],
    expected_dump: dict[str, object],
) -> None:
    del case_id, expected_model, expected_dump
    request_payload = {"command": command_payload}

    wire_request = WireCommandRunRequest.model_validate(request_payload, strict=True)
    request = parse_command_run_request(request_payload)

    assert wire_request.command.kind == command_payload["kind"]
    assert resolve_command_spec(request.command).daemon_kind == command_payload["kind"]


def test_command_run_request_adapter_cases_cover_shared_daemon_catalog() -> None:
    covered_kinds = {
        command_payload["kind"]
        for _, command_payload, _, _ in _COMMAND_RUN_ADAPTER_CASES
    }

    assert covered_kinds == DAEMON_COMMAND_KINDS


def test_command_run_request_adapts_adb_connect_without_boundary_hash() -> None:
    payload = {
        "command": {
            "kind": "connect",
            "connection": {
                "mode": "adb",
                "token": "device-token",
                "serial": "emulator-5554",
            },
        },
    }

    request = parse_command_run_request(payload)

    assert isinstance(request.command, ConnectCommand)
    assert request.command.connection.port == DEFAULT_DEVICE_PORT


def test_command_run_request_adapts_lan_connect_with_explicit_port() -> None:
    payload = {
        "command": {
            "kind": "connect",
            "connection": {
                "mode": "lan",
                "token": "device-token",
                "host": "192.168.0.10",
                "port": 17171,
            },
        },
    }

    request = parse_command_run_request(payload)

    assert isinstance(request.command, ConnectCommand)
    assert request.command.connection.host == "192.168.0.10"
    assert request.command.connection.port == 17171


def test_command_run_request_rejects_lan_connect_without_port() -> None:
    with pytest.raises(DaemonError) as error:
        parse_command_run_request(
            {
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "lan",
                        "token": "device-token",
                        "host": "192.168.0.10",
                    },
                }
            }
        )

    assert error.value.code == "DAEMON_BAD_REQUEST"


def test_observe_handler_emits_semantic_result_shape(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-1"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-1"),
    )
    next_screen = _make_screen("screen-2")
    artifacts = build_screen_artifacts(runtime, screen_id="screen-2")
    snapshot_service = StaticSnapshotService(_make_snapshot())

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=snapshot_service,
        screen_refresh=StaticScreenRefresh(
            public_screen=next_screen,
            artifacts=artifacts,
        ),
    )

    payload = handler.handle(command=ObserveCommand())

    assert payload["ok"] is True
    assert payload["command"] == "observe"
    assert payload["category"] == "observe"
    assert payload["payloadMode"] == "full"
    assert "sourceScreenId" not in payload
    assert payload["nextScreenId"] == "screen-2"
    assert "summary" not in payload
    assert "runtime" not in payload
    assert "changed" not in payload
    assert payload["truth"]["continuityStatus"] == "none"
    assert snapshot_service.fetch_calls[0][1] is True


def test_observe_handler_force_refreshes_when_current_is_not_authoritative(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.current_screen_id = None
    runtime.latest_snapshot = _make_snapshot(snapshot_id=1)
    next_screen = _make_screen("screen-2")
    snapshot_service = StaticSnapshotService(_make_snapshot(snapshot_id=2))

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=snapshot_service,
        screen_refresh=StaticScreenRefresh(
            public_screen=next_screen,
            artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
        ),
    )

    payload = handler.handle(command=ObserveCommand())

    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-2"
    assert snapshot_service.fetch_calls[0][1] is True


def test_observe_handler_does_not_expose_stale_screen_when_fresh_fetch_fails(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.current_screen_id = None
    runtime.latest_snapshot = _make_snapshot(snapshot_id=1)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-stale"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-stale"),
    )
    fetch_calls: list[bool] = []

    class _FailingSnapshotService:
        def fetch(
            self,
            runtime: Any,
            force_refresh: bool = False,
            *,
            lifecycle_lease: Any | None = None,
        ) -> Any:
            del runtime, lifecycle_lease
            fetch_calls.append(force_refresh)
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="snapshot failed",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=_FailingSnapshotService(),
        screen_refresh=StaticScreenRefresh(
            public_screen=_make_screen("screen-2"),
            artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
        ),
    )

    payload = handler.handle(command=ObserveCommand())

    assert fetch_calls == [True]
    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert "screen" not in payload
    assert "nextScreenId" not in payload


def test_observe_handler_uses_repaired_stable_continuity(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    source_snapshot = _make_snapshot(snapshot_id=1)
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="fingerprint-source",
    )
    install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-source"),
    )
    runtime.status = RuntimeStatus.READY
    next_snapshot = _make_snapshot(snapshot_id=2)
    next_compiled = _make_compiled_screen(
        "screen-repaired",
        source_snapshot_id=next_snapshot.snapshot_id,
        captured_at=next_snapshot.captured_at,
        fingerprint="fingerprint-repaired",
    )
    next_screen = next_compiled.to_public_screen()
    artifacts = build_screen_artifacts(runtime, screen_id="screen-repaired")
    snapshot_service = StaticSnapshotService(next_snapshot)

    def _refresh_runtime(
        runtime: Any,
        refreshed_snapshot: Any,
        **kwargs: Any,
    ) -> None:
        del kwargs
        runtime.latest_snapshot = refreshed_snapshot
        runtime.screen_state = ScreenState(
            public_screen=next_screen,
            compiled_screen=next_compiled,
            artifacts=artifacts,
        )
        runtime.current_screen_id = next_screen.screen_id

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
    )

    payload = handler.handle(command=ObserveCommand())

    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is True
    assert payload["nextScreenId"] == "screen-repaired"


def test_observe_handler_maps_runtime_disconnect_to_device_unavailable(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.connection = None
    runtime.device_token = None

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=StaticSnapshotService(_make_snapshot()),
        screen_refresh=StaticScreenRefresh(public_screen=None),
    )

    payload = handler.handle(command=ObserveCommand())

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert payload["message"] == "No current device observation is available."


def test_observe_handler_drops_stale_cached_screen_for_device_unavailable(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-stale"),
        compiled_screen=_make_compiled_screen(
            "screen-stale",
            fingerprint="fingerprint-stale",
        ),
        artifacts=build_screen_artifacts(runtime, screen_id="screen-stale"),
    )

    class _SnapshotService:
        def fetch(
            self,
            runtime: Any,
            force_refresh: bool,
            *,
            lifecycle_lease: Any = None,
        ) -> Any:
            del runtime, force_refresh, lifecycle_lease
            raise DaemonError(
                code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
                message="runtime is not connected to a device",
                retryable=False,
                details={},
                http_status=200,
            )

    handler = ObserveCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        snapshot_service=_SnapshotService(),
        screen_refresh=StaticScreenRefresh(public_screen=None),
    )

    payload = handler.handle(command=ObserveCommand())

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert "screen" not in payload
    assert "nextScreenId" not in payload


def test_global_action_handler_preserves_source_screen_id_in_semantic_result(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-1",
            )
        ),
    )

    assert payload["command"] == "back"
    assert payload["category"] == "transition"
    assert payload["sourceScreenId"] == "screen-1"
    assert "debug" not in payload


def test_ref_action_handler_passes_public_safe_action_target(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    install_screen_state(
        runtime,
        snapshot=_make_snapshot(snapshot_id=2),
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )
    action_target = ActionTargetPayload(
        source_ref="n1",
        source_screen_id="screen-1",
        subject_ref="n2",
        dispatched_ref="n2",
        next_screen_id="screen-2",
        next_ref="n2",
        identity_status="sameRef",
        evidence=("refRepair", "requestTarget", "focusConfirmation"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return SemanticResultAssemblyInput(action_target=action_target)

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "focus",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["actionTarget"] == action_target.model_dump(by_alias=True)


def test_ref_action_handler_uses_executor_success_execution_outcome(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    install_screen_state(
        runtime,
        snapshot=_make_snapshot(snapshot_id=2),
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return SemanticResultAssemblyInput(execution_outcome="notAttempted")

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "focus",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notAttempted"
    assert "actionTarget" not in payload


def test_global_action_handler_uses_repaired_stable_continuity(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1)
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="fingerprint-source",
    )
    install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-source"),
    )
    next_snapshot = _make_snapshot(snapshot_id=2)
    next_compiled = _make_compiled_screen(
        "screen-repaired",
        source_snapshot_id=next_snapshot.snapshot_id,
        captured_at=next_snapshot.captured_at,
        fingerprint="fingerprint-repaired",
    )
    next_screen = next_compiled.to_public_screen()

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del record, command, lifecycle_lease
            install_screen_state(
                runtime,
                snapshot=next_snapshot,
                public_screen=next_screen,
                compiled_screen=next_compiled,
                artifacts=build_screen_artifacts(runtime, screen_id="screen-repaired"),
            )
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-source",
            )
        ),
    )

    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is True
    assert payload["nextScreenId"] == "screen-repaired"


def test_global_action_handler_uses_authoritative_basis_for_source_less_global(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1)
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="fingerprint-source",
    )
    install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-source"),
    )
    next_snapshot = _make_snapshot(snapshot_id=2)
    next_compiled = _make_compiled_screen(
        "screen-repaired",
        source_snapshot_id=next_snapshot.snapshot_id,
        captured_at=next_snapshot.captured_at,
        fingerprint="fingerprint-repaired",
    )
    next_screen = next_compiled.to_public_screen()

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del record, command, lifecycle_lease
            install_screen_state(
                runtime,
                snapshot=next_snapshot,
                public_screen=next_screen,
                compiled_screen=next_compiled,
                artifacts=build_screen_artifacts(runtime, screen_id="screen-repaired"),
            )
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(GlobalActionCommandPayload(kind="back")),
    )

    assert payload["sourceScreenId"] == "screen-source"
    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is True
    assert payload["nextScreenId"] == "screen-repaired"


def test_global_action_handler_omits_source_and_changed_without_authoritative_basis(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.CONNECTED

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del record, command, lifecycle_lease
            runtime.screen_state = ScreenState(
                public_screen=_make_screen("screen-after"),
                compiled_screen=None,
                artifacts=build_screen_artifacts(runtime, screen_id="screen-after"),
            )
            runtime.current_screen_id = "screen-after"
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(GlobalActionCommandPayload(kind="back")),
    )

    assert "sourceScreenId" not in payload
    assert "changed" not in payload["truth"]
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["nextScreenId"] == "screen-after"


def test_global_action_handler_preserves_unrevalidated_explicit_source_without_changed(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    live_snapshot = _make_snapshot(snapshot_id=1)
    live_compiled = _make_compiled_screen(
        "screen-live",
        source_snapshot_id=live_snapshot.snapshot_id,
        captured_at=live_snapshot.captured_at,
        fingerprint="fingerprint-live",
    )
    install_screen_state(
        runtime,
        snapshot=live_snapshot,
        public_screen=live_compiled.to_public_screen(),
        compiled_screen=live_compiled,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-live"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-source",
            )
        ),
    )

    assert payload["sourceScreenId"] == "screen-source"
    assert payload["nextScreenId"] == "screen-live"
    assert payload["truth"]["continuityStatus"] == "stale"
    assert "changed" not in payload["truth"]


def test_open_handler_success_uses_dispatched_execution_outcome(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen(
            "screen-2",
            package_name="com.google.android.settings.intelligence",
        ),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _SemanticAssembly:
        warnings: tuple[str, ...] = ("opened alias target",)
        app_payload = CommandAppPayload(
            package_name="com.google.android.settings.intelligence",
            activity_name="SettingsActivity",
            requested_package_name="com.android.settings",
            resolved_package_name="com.google.android.settings.intelligence",
            match_type="alias",
        )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return _SemanticAssembly()

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload.model_validate(
                {
                    "kind": "open",
                    "target": {"kind": "app", "value": "com.android.settings"},
                }
            )
        )
    )

    assert payload["command"] == "open"
    assert payload["category"] == "open"
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["screen"]["app"]["requestedPackageName"] == "com.android.settings"
    assert (
        payload["screen"]["app"]["resolvedPackageName"]
        == "com.google.android.settings.intelligence"
    )
    assert payload["screen"]["app"]["matchType"] == "alias"
    assert payload["warnings"] == ["opened alias target"]


def test_open_failure_preserves_dispatched_execution_outcome(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            raise DaemonError(
                code=DaemonErrorCode.OPEN_FAILED,
                message="open failed",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload.model_validate(
                {
                    "kind": "open",
                    "target": {"kind": "app", "value": "com.android.settings"},
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["truth"]["executionOutcome"] == "dispatched"


def test_global_action_precondition_failure_maps_to_not_attempted(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            raise DaemonError(
                code=DaemonErrorCode.SCREEN_NOT_READY,
                message="screen is not ready yet",
                retryable=False,
                details={},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-1",
            )
        ),
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert payload["sourceScreenId"] == "screen-1"
    assert payload["truth"]["executionOutcome"] == "notAttempted"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert "actionTarget" not in payload


def test_ref_action_focus_mismatch_preserves_dispatched_execution_outcome(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
                message="focus did not land on the requested input target",
                retryable=True,
                details={
                    "reason": "focus_mismatch",
                    "ref": "n1",
                    "actionTarget": {
                        "sourceRef": "raw-rid:w1:0.5",
                        "nextRef": "/tmp/.androidctl/artifact",
                    },
                },
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "focus",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["code"] == SemanticResultCode.TARGET_NOT_ACTIONABLE
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert "actionTarget" not in payload


def test_ref_scroll_unexposed_direction_returns_not_attempted_semantic_failure(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    screen_id = "screen-2"
    install_screen_state(
        runtime,
        snapshot=_make_snapshot(snapshot_id=2),
        public_screen=_make_screen(
            screen_id,
            targets=(
                PublicNode(
                    kind="container",
                    role="scroll-container",
                    label="Results",
                    ref="n1",
                    actions=("scroll",),
                    scroll_directions=("down",),
                ),
            ),
        ),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id=screen_id),
    )

    class _Client:
        def action_perform(self, payload: Any, *, request_id: str) -> Any:
            del payload, request_id
            raise AssertionError("unexposed scroll direction must not dispatch")

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=ActionExecutor(
            device_client_factory=lambda runtime, *, lifecycle_lease=None: _Client(),
            screen_refresh=object(),
            settler=object(),
            repairer=object(),
        ),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "scroll",
                    "ref": "n1",
                    "sourceScreenId": screen_id,
                    "direction": "up",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "scroll"
    assert payload["code"] == SemanticResultCode.TARGET_NOT_ACTIONABLE
    assert payload["truth"]["executionOutcome"] == "notAttempted"
    assert payload["truth"]["observationQuality"] == "authoritative"


def test_ref_action_post_dispatch_action_not_confirmed_keeps_authoritative_truth(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_authoritative_screen(runtime, screen_id="screen-2")

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.ACTION_NOT_CONFIRMED,
                message="action was not confirmed on the refreshed screen",
                retryable=False,
                details={},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "longTap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "long-tap"
    assert payload["payloadMode"] == "full"
    assert payload["code"] == SemanticResultCode.ACTION_NOT_CONFIRMED
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "authoritative"
    assert payload["nextScreenId"] == "screen-2"
    assert "screen" in payload
    assert "actionTarget" not in payload


def test_ref_action_post_dispatch_action_not_confirmed_without_basis_maps_to_lost(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.ACTION_NOT_CONFIRMED,
                message="action was not confirmed on the refreshed screen",
                retryable=False,
                details={},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "longTap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "long-tap"
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "none"
    assert "nextScreenId" not in payload
    assert "screen" not in payload
    assert "actionTarget" not in payload


def test_ref_action_post_dispatch_type_not_confirmed_without_basis_maps_to_lost(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.TYPE_NOT_CONFIRMED,
                message="typed text was not confirmed on the refreshed screen",
                retryable=True,
                details={},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "type",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                    "text": "hello",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "type"
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "none"
    assert "nextScreenId" not in payload
    assert "screen" not in payload
    assert "actionTarget" not in payload


def test_ref_action_post_dispatch_submit_not_confirmed_keeps_authoritative_truth(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_authoritative_screen(runtime, screen_id="screen-2")

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
                message="submit effect could not be confirmed",
                retryable=True,
                details={"reason": "direct_submit_not_confirmed"},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "submit",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "submit"
    assert payload["payloadMode"] == "full"
    assert payload["code"] == SemanticResultCode.SUBMIT_NOT_CONFIRMED
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "authoritative"
    assert payload["nextScreenId"] == "screen-2"
    assert "screen" in payload
    assert "actionTarget" not in payload


def test_ref_action_post_dispatch_submit_not_confirmed_without_basis_maps_to_lost(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
                message="submit effect could not be confirmed",
                retryable=True,
                details={"reason": "direct_submit_not_confirmed"},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "submit",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "submit"
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "none"
    assert "nextScreenId" not in payload
    assert "screen" not in payload
    assert "actionTarget" not in payload


def test_ref_action_post_dispatch_device_loss_maps_to_observation_lost(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = None

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="device rpc failed",
                retryable=True,
                details={},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
                truth_lost_after_dispatch=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "tap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "POST_ACTION_OBSERVATION_LOST"
    assert payload["truth"]["executionOutcome"] == "dispatched"


def test_ref_action_post_dispatch_device_loss_ignores_stale_cached_screen(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-stale"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-stale"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="device rpc failed",
                retryable=True,
                details={},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
                truth_lost_after_dispatch=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "tap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "POST_ACTION_OBSERVATION_LOST"
    assert payload["sourceScreenId"] == "screen-1"
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert "screen" not in payload
    assert "nextScreenId" not in payload


def test_ref_action_long_tap_success_uses_public_semantic_command_name(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            return None

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "longTap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["command"] == "long-tap"


def test_ref_action_long_tap_post_dispatch_failure_uses_public_semantic_command_name(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-2"),
        compiled_screen=None,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-2"),
    )

    class _ActionExecutor:
        def execute(
            self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
        ) -> Any:
            del runtime, record, command, lifecycle_lease
            error = DaemonError(
                code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
                message="long tap did not reach the requested target",
                retryable=True,
                details={"ref": "n1"},
                http_status=200,
            )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=error,
                dispatch_attempted=True,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=_ActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=compile_ref_action_command(
            parse_command_request_payload(
                {
                    "kind": "longTap",
                    "ref": "n1",
                    "sourceScreenId": "screen-1",
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["command"] == "long-tap"
    assert payload["truth"]["executionOutcome"] == "dispatched"


def test_global_action_real_executor_post_dispatch_device_loss_drops_stale_truth(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.screen_state = ScreenState(
        public_screen=_make_screen("screen-stale"),
        compiled_screen=_make_compiled_screen(
            "screen-stale",
            fingerprint="fp-stale",
        ),
        artifacts=build_screen_artifacts(runtime, screen_id="screen-stale"),
    )

    class _Client:
        def action_perform(
            self, payload: Any, *, request_id: str
        ) -> ActionPerformResult:
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class _Settler:
        def settle(
            self,
            session: Any,
            client: Any,
            kind: Any,
            baseline_signature: Any,
            **kwargs: Any,
        ) -> Any:
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="device observation lost after dispatch",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        action_executor=ActionExecutor(
            device_client_factory=lambda runtime, *, lifecycle_lease=None: _Client(),
            screen_refresh=object(),
            settler=_Settler(),
            repairer=object(),
        ),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-1",
            )
        ),
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert "screen" not in payload
    assert "nextScreenId" not in payload


@pytest.mark.parametrize("action", ["back", "home", "recents", "notifications"])
def test_global_action_observation_lost_clears_runtime_get_current(
    tmp_path: Path,
    action: str,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    close_calls: list[str] = []
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    original_transport = runtime.transport
    _install_authoritative_screen(runtime, screen_id="screen-old")
    kernel = RuntimeKernel(runtime_store)

    class _Client:
        def action_perform(
            self, payload: Any, *, request_id: str
        ) -> ActionPerformResult:
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class _Settler:
        def settle(
            self,
            session: Any,
            client: Any,
            kind: Any,
            baseline_signature: Any,
            **kwargs: Any,
        ) -> Any:
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="device observation lost after dispatch",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=kernel,
        action_executor=ActionExecutor(
            device_client_factory=lambda runtime, *, lifecycle_lease=None: _Client(),
            screen_refresh=object(),
            settler=_Settler(),
            repairer=object(),
            runtime_kernel=kernel,
        ),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind=action,
                source_screen_id="screen-old",
            )
        ),
    )
    runtime_payload = _runtime_get_payload(runtime_store)["runtime"]

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is original_transport
    assert close_calls == []
    assert runtime.device_capabilities == DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    assert runtime_payload["status"] == "connected"
    assert "currentScreenId" not in runtime_payload


def test_global_action_post_dispatch_unauthorized_maps_to_device_unavailable(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    close_calls: list[str] = []
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    _install_authoritative_screen(runtime, screen_id="screen-old")
    kernel = RuntimeKernel(runtime_store)

    class _Client:
        def action_perform(
            self, payload: Any, *, request_id: str
        ) -> ActionPerformResult:
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class _Settler:
        def settle(
            self,
            session: Any,
            client: Any,
            kind: Any,
            baseline_signature: Any,
            **kwargs: Any,
        ) -> Any:
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
                message="device agent rejected request credentials",
                retryable=False,
                details={"status": 401},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=kernel,
        action_executor=ActionExecutor(
            device_client_factory=lambda runtime, *, lifecycle_lease=None: _Client(),
            screen_refresh=object(),
            settler=_Settler(),
            repairer=object(),
            runtime_kernel=kernel,
        ),
    )

    payload = handler.handle_global_action(
        command=compile_global_action_command(
            GlobalActionCommandPayload(
                kind="back",
                source_screen_id="screen-old",
            )
        ),
    )
    runtime_payload = _runtime_get_payload(runtime_store)["runtime"]

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.DEVICE_UNAVAILABLE
    assert payload["code"] != SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert payload["truth"]["observationQuality"] == "none"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert close_calls == ["closed"]
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.connection is None
    assert runtime.device_token is None
    assert runtime.transport is None
    assert runtime_payload["status"] == "broken"
    assert runtime_payload["status"] != "connected"
    assert "currentScreenId" not in runtime_payload


def test_open_observation_lost_clears_runtime_get_current(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["launchApp"],
    )
    close_calls: list[str] = []
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    original_transport = runtime.transport
    _install_authoritative_screen(runtime, screen_id="screen-old")
    kernel = RuntimeKernel(runtime_store)

    class _Client:
        def action_perform(
            self, payload: Any, *, request_id: str
        ) -> ActionPerformResult:
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class _Settler:
        def settle(
            self,
            session: Any,
            client: Any,
            kind: Any,
            baseline_signature: Any,
            **kwargs: Any,
        ) -> Any:
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="device observation lost after dispatch",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ActionCommandHandler(
        runtime_kernel=kernel,
        action_executor=ActionExecutor(
            device_client_factory=lambda runtime, *, lifecycle_lease=None: _Client(),
            screen_refresh=object(),
            settler=_Settler(),
            repairer=object(),
            runtime_kernel=kernel,
        ),
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload.model_validate(
                {
                    "kind": "open",
                    "target": {"kind": "app", "value": "com.android.settings"},
                }
            )
        )
    )
    runtime_payload = _runtime_get_payload(runtime_store)["runtime"]

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert payload["truth"]["executionOutcome"] == "dispatched"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is original_transport
    assert close_calls == []
    assert runtime.device_capabilities == DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["launchApp"],
    )
    assert runtime_payload["status"] == "connected"
    assert "currentScreenId" not in runtime_payload


def test_wait_handler_supports_gone_predicate_without_not_implemented(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_authoritative_screen(runtime, screen_id="screen-1", ref="n7")
    seen: dict[str, Any] = {}

    class _WaitRuntimeLoop:
        def run(
            self,
            *,
            session: Any,
            record: Any,
            command: Any,
            lifecycle_lease: Any,
        ) -> Any:
            del record, lifecycle_lease
            seen["command"] = command
            session.latest_snapshot = _make_snapshot(snapshot_id=2)
            session.screen_state = ScreenState(
                public_screen=_make_screen("screen-1"),
                compiled_screen=None,
                artifacts=session.screen_state.artifacts,
            )
            session.current_screen_id = "screen-1"
            return WaitMatchData(snapshot=session.latest_snapshot)

    handler = WaitCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        wait_runtime_loop=_WaitRuntimeLoop(),
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "gone",
                        "sourceScreenId": "screen-1",
                        "ref": "n7",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert seen["command"].kind.value == "wait"
    assert seen["command"].wait_kind.value == "gone"
    assert payload["ok"] is True
    assert payload["command"] == "wait"
    assert payload["category"] == "wait"
    assert payload["truth"]["continuityStatus"] == "stale"
    assert payload["nextScreenId"] == "screen-1"


def test_wait_handler_screen_change_uses_wait_runtime_loop(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    _install_authoritative_screen(runtime, screen_id="screen-1")
    seen: dict[str, Any] = {}

    class _WaitRuntimeLoop:
        def run(
            self,
            *,
            session: Any,
            record: Any,
            command: Any,
            lifecycle_lease: Any,
        ) -> Any:
            del record, lifecycle_lease
            seen["command"] = command
            session.latest_snapshot = _make_snapshot(snapshot_id=2)
            session.screen_state = ScreenState(
                public_screen=_make_screen("screen-2"),
                compiled_screen=None,
                artifacts=session.screen_state.artifacts,
            )
            session.current_screen_id = "screen-2"
            return WaitMatchData(snapshot=session.latest_snapshot)

    handler = WaitCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        wait_runtime_loop=_WaitRuntimeLoop(),
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-1",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert seen["command"].kind.value == "wait"
    assert seen["command"].wait_kind.value == "screen-change"
    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-2"
    assert payload["truth"]["changed"] is True


def test_wait_handler_uses_repaired_stable_continuity(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1)
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="fingerprint-source",
    )
    install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-source"),
    )
    next_snapshot = _make_snapshot(snapshot_id=2)
    next_compiled = _make_compiled_screen(
        "screen-repaired",
        source_snapshot_id=next_snapshot.snapshot_id,
        captured_at=next_snapshot.captured_at,
        fingerprint="fingerprint-repaired",
    )
    next_screen = next_compiled.to_public_screen()
    seen: dict[str, Any] = {}

    class _WaitRuntimeLoop:
        def run(
            self,
            *,
            session: Any,
            record: Any,
            command: Any,
            lifecycle_lease: Any,
        ) -> Any:
            del record, lifecycle_lease
            seen["command"] = command
            install_screen_state(
                session,
                snapshot=next_snapshot,
                public_screen=next_screen,
                compiled_screen=next_compiled,
                artifacts=build_screen_artifacts(session, screen_id="screen-repaired"),
            )
            return WaitMatchData(snapshot=next_snapshot)

    handler = WaitCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        wait_runtime_loop=_WaitRuntimeLoop(),
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-source",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert seen["command"].kind.value == "wait"
    assert seen["command"].wait_kind.value == "screen-change"
    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is True
    assert payload["nextScreenId"] == "screen-repaired"


def test_wait_handler_screen_change_waits_through_timeout_window(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _install_authoritative_screen(runtime, screen_id="screen-1")
    seen: dict[str, Any] = {}

    class _WaitRuntimeLoop:
        def run(
            self,
            *,
            session: Any,
            record: Any,
            command: Any,
            lifecycle_lease: Any,
        ) -> Any:
            del session, record, lifecycle_lease
            seen["command"] = command
            return WaitLoopTimedOut()

    handler = WaitCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        wait_runtime_loop=_WaitRuntimeLoop(),
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-1",
                    },
                    "timeoutMs": 120,
                }
            )
        )
    )

    assert seen["command"].kind.value == "wait"
    assert seen["command"].wait_kind.value == "screen-change"
    assert payload["ok"] is False
    assert payload["code"] == SemanticResultCode.WAIT_TIMEOUT
    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is False


def test_screenshot_handler_emits_retained_result_shape(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del payload
            return Path(
                build_artifact_path(
                    runtime,
                    stem="screenshot",
                    extension="png",
                    namespace="screenshots",
                )
            )

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is True
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert_retained_omits_semantic_fields(payload)
    assert payload["artifacts"]["screenshotPng"] == build_artifact_path(
        runtime,
        stem="screenshot",
        extension="png",
        namespace="screenshots",
    )
    assert "debug" not in payload
    assert "summary" not in payload
    assert "runtime" not in payload


def test_screenshot_handler_rebootstraps_no_transport_before_capability_gate(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=False,
        action_kinds=[],
    )
    factory_calls: list[str] = []

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    def rebootstrap_factory(runtime, *, lifecycle_lease=None):
        del lifecycle_lease
        factory_calls.append("factory")
        assert runtime.transport is None
        runtime.transport = RuntimeTransport(
            endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
            close=lambda: None,
        )
        runtime.device_capabilities = DeviceCapabilities(
            supports_events_poll=True,
            supports_screenshot=True,
            action_kinds=[],
        )
        return _DeviceClient()

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=rebootstrap_factory,
        artifact_writer=ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is True
    assert payload["command"] == "screenshot"
    assert factory_calls == ["factory"]
    screenshot_path = Path(payload["artifacts"]["screenshotPng"])
    assert screenshot_path.read_bytes() == _VALID_PNG_1X1


def test_screenshot_handler_rejects_invalid_base64_without_artifact(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    old_artifacts = ScreenArtifacts(
        screen_json=build_artifact_path(
            runtime,
            stem="screen-1",
            extension="json",
            namespace="screens",
        ),
        screenshot_png=build_artifact_path(
            runtime,
            stem="old-shot",
            extension="png",
            namespace="screenshots",
        ),
    )
    install_screen_state(
        runtime,
        public_screen=None,
        artifacts=old_artifacts,
    )

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = "not base64!"
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del runtime, payload
            raise AssertionError("invalid screenshot payload must not be written")

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "DEVICE_RPC_FAILED"
    assert payload["artifacts"] == {}
    assert_retained_omits_semantic_fields(payload)
    assert not (runtime.artifact_root / "screenshots").exists()
    assert runtime.screen_state is not None
    assert runtime.screen_state.artifacts == old_artifacts


def test_screenshot_handler_projects_artifact_write_failure(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    old_artifacts = ScreenArtifacts(
        screen_json=build_artifact_path(
            runtime,
            stem="screen-1",
            extension="json",
            namespace="screens",
        ),
        screenshot_png=build_artifact_path(
            runtime,
            stem="old-shot",
            extension="png",
            namespace="screenshots",
        ),
    )
    install_screen_state(
        runtime,
        public_screen=None,
        artifacts=old_artifacts,
    )

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del runtime, payload
            raise DaemonError(
                code=DaemonErrorCode.ARTIFACT_WRITE_FAILED,
                message="artifact write failed",
                details={
                    "reason": "permission-denied",
                    "path": "/repo/.androidctl/screenshots/shot-00001.png",
                    "body": "raw",
                    "params": {"mode": "debug"},
                },
            )

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "WORKSPACE_STATE_UNWRITABLE"
    assert payload["message"] == "artifact write failed"
    assert payload["details"] == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
        "reason": "permission-denied",
    }
    assert payload["artifacts"] == {}
    assert_retained_omits_semantic_fields(payload)
    assert runtime.screen_state is not None
    assert runtime.screen_state.artifacts == old_artifacts


def test_screenshot_handler_real_writer_filesystem_failure_is_retained(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    old_artifacts = ScreenArtifacts(
        screen_json=build_artifact_path(
            runtime,
            stem="screen-1",
            extension="json",
            namespace="screens",
        ),
        screenshot_png=build_artifact_path(
            runtime,
            stem="old-shot",
            extension="png",
            namespace="screenshots",
        ),
    )
    install_screen_state(
        runtime,
        public_screen=None,
        artifacts=old_artifacts,
    )
    runtime.artifact_root.write_text("not a directory")

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "WORKSPACE_STATE_UNWRITABLE"
    assert payload["details"] == {
        "sourceCode": "ARTIFACT_ROOT_UNWRITABLE",
        "sourceKind": "workspace",
        "reason": "namespace-create-failed",
    }
    assert payload["artifacts"] == {}
    assert runtime.screen_state is not None
    assert runtime.screen_state.artifacts == old_artifacts


def test_screenshot_service_real_writer_filesystem_failure_is_retained(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.artifact_root.write_text("not a directory")

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    service = CommandService(
        runtime_store,
        artifact_writer=ArtifactWriter(),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
    )
    daemon = DaemonService(
        runtime_store=runtime_store,
        command_service=service,
    )

    status, payload = daemon.handle(
        "POST",
        "/commands/run",
        {},
        b'{"command":{"kind":"screenshot"}}',
    )

    assert status == 200
    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "WORKSPACE_STATE_UNWRITABLE"
    assert payload["details"] == {
        "sourceCode": "ARTIFACT_ROOT_UNWRITABLE",
        "sourceKind": "workspace",
        "reason": "namespace-create-failed",
    }
    assert payload["artifacts"] == {}


def test_screenshot_handler_unlinks_orphan_when_attach_lifecycle_is_stale(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    orphan_path = runtime.artifact_root / "screenshots" / "shot-00001.png"

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    class _StaleAttachKernel(PassiveRuntimeKernel[Any]):
        def attach_screenshot_artifact(
            self,
            runtime: Any,
            lease: object,
            *,
            screenshot_png: str,
        ) -> object | None:
            del runtime, lease, screenshot_png
            return None

    handler = ScreenshotCommandHandler(
        runtime_kernel=_StaleAttachKernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=ArtifactWriter(),
    )

    with pytest.raises(RuntimeError, match="runtime lifecycle changed"):
        handler.handle(command=ScreenshotCommand())

    assert not orphan_path.exists()


def test_screenshot_handler_rejects_invalid_png_without_artifact(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = base64.b64encode(b"not a png").decode("ascii")
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del runtime, payload
            raise AssertionError("invalid screenshot payload must not be written")

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "DEVICE_RPC_FAILED"
    assert_retained_omits_semantic_fields(payload)
    assert not (runtime.artifact_root / "screenshots").exists()


def test_screenshot_handler_rejects_png_metadata_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 2
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del runtime, payload
            raise AssertionError("mismatched screenshot payload must not be written")

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "DEVICE_RPC_FAILED"
    assert_retained_omits_semantic_fields(payload)
    assert not (runtime.artifact_root / "screenshots").exists()


def test_screenshot_handler_rejects_ihdr_pixel_budget_without_artifact(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _png_header_base64(SCREENSHOT_MAX_OUTPUT_PIXELS + 1, 1)
                content_type = "image/png"
                width_px = SCREENSHOT_MAX_OUTPUT_PIXELS + 1
                height_px = 1

            return _Payload()

    class _ArtifactWriter:
        def write_screenshot_png(self, runtime: Any, payload: bytes) -> Path:
            del runtime, payload
            raise AssertionError("oversized screenshot payload must not be written")

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=_ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is False
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert payload["code"] == "DEVICE_RPC_FAILED"
    assert_retained_omits_semantic_fields(payload)
    assert not (runtime.artifact_root / "screenshots").exists()


def test_screenshot_handler_writes_valid_png_artifact_success(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    current_screen = _make_screen("screen-1")
    runtime.screen_state = ScreenState(
        public_screen=current_screen,
        artifacts=build_screen_artifacts(runtime, screen_id="screen-1"),
    )

    class _DeviceClient:
        def screenshot_capture(self, request_id: str) -> Any:
            del request_id

            class _Payload:
                body_base64 = _VALID_PNG_1X1_BASE64
                content_type = "image/png"
                width_px = 1
                height_px = 1

            return _Payload()

    handler = ScreenshotCommandHandler(
        runtime_kernel=_make_runtime_kernel(runtime),
        device_client_factory=lambda runtime, *, lifecycle_lease=None: _DeviceClient(),
        artifact_writer=ArtifactWriter(),
    )

    payload = handler.handle(command=ScreenshotCommand())

    assert payload["ok"] is True
    assert payload["command"] == "screenshot"
    assert payload["envelope"] == "artifact"
    assert_retained_omits_semantic_fields(payload)
    screenshot_path = Path(payload["artifacts"]["screenshotPng"])
    assert screenshot_path.read_bytes() == _VALID_PNG_1X1
    assert runtime.runtime_path.exists() is False
