from __future__ import annotations

from pathlib import Path

from androidctl_contracts.daemon_api import ConnectCommandPayload
from androidctld.commands.command_models import ConnectCommand
from androidctld.commands.from_boundary import compile_connect_command
from androidctld.commands.service import CommandService
from androidctld.device.types import (
    BootstrapResult,
    ConnectionConfig,
    ConnectionSpec,
    DeviceCapabilities,
    DeviceEndpoint,
    MetaInfo,
    RuntimeTransport,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.snapshots.models import RawIme, RawNode, RawSnapshot

from ..support.runtime_store import runtime_store_for_workspace
from .support.retained import assert_retained_omits_semantic_fields


class _FakeHandle:
    def __init__(self) -> None:
        self.endpoint = DeviceEndpoint(host="127.0.0.1", port=17171)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeBootstrapper:
    def __init__(
        self,
        *,
        bootstrap_result: BootstrapResult | None = None,
        bootstrap_error: DaemonError | None = None,
    ) -> None:
        self._bootstrap_result = bootstrap_result
        self._bootstrap_error = bootstrap_error
        self.establish_calls = 0
        self.bootstrap_calls = 0
        self.establish_configs: list[ConnectionConfig] = []
        self.bootstrap_configs: list[ConnectionConfig] = []
        self.handle = _FakeHandle()

    def establish_transport(self, config: ConnectionConfig) -> _FakeHandle:
        self.establish_configs.append(config)
        self.establish_calls += 1
        return self.handle

    def bootstrap_runtime(
        self, handle: _FakeHandle, config: ConnectionConfig
    ) -> BootstrapResult:
        del handle
        self.bootstrap_configs.append(config)
        self.bootstrap_calls += 1
        if self._bootstrap_error is not None:
            raise self._bootstrap_error
        assert self._bootstrap_result is not None
        return self._bootstrap_result


class _FakeSnapshotService:
    def fetch(
        self,
        session: object,
        force_refresh: bool,
        *,
        lifecycle_lease: object | None = None,
    ) -> RawSnapshot:
        del session, force_refresh, lifecycle_lease
        return _snapshot()


def _connect_command(
    *, token: str = "device-token", serial: str | None = "emulator-5554"
) -> ConnectCommand:
    connection: dict[str, object] = {
        "mode": "adb",
        "token": token,
    }
    if serial is not None:
        connection["serial"] = serial
    return compile_connect_command(
        ConnectCommandPayload.model_validate(
            {
                "kind": "connect",
                "connection": connection,
            }
        )
    )


def _bootstrap_result() -> BootstrapResult:
    return BootstrapResult(
        connection=ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554"),
        transport=RuntimeTransport(
            endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
            close=lambda: None,
        ),
        meta=MetaInfo(
            service="androidctl-device-agent",
            version="test",
            capabilities=DeviceCapabilities(
                supports_events_poll=True,
                supports_screenshot=True,
                action_kinds=["tap"],
            ),
        ),
    )


def _accessibility_not_ready_error() -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.ACCESSIBILITY_NOT_READY,
        message="accessibility not ready",
        retryable=True,
        details={"reason": "service-disabled"},
        http_status=200,
    )


def _device_agent_unavailable_error() -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DEVICE_AGENT_UNAVAILABLE,
        message="device agent unavailable",
        retryable=True,
        details={},
        http_status=200,
    )


def _device_agent_unauthorized_error() -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
        message="device agent unauthorized",
        retryable=False,
        details={
            "reason": "wrong-token",
            "token": "Bearer secret",
            "serial": "emulator-5554",
            "details": {"phase": "handshake"},
        },
        http_status=200,
    )


def _device_agent_version_mismatch_error() -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DEVICE_AGENT_VERSION_MISMATCH,
        message=(
            "device agent release version mismatch: daemon=0.1.0 agent=0.1.1; "
            "install matching androidctld and Android agent/APK versions"
        ),
        retryable=False,
        details={
            "expectedReleaseVersion": "0.1.0",
            "actualReleaseVersion": "0.1.1",
            "token": "Bearer secret",
        },
        http_status=200,
    )


def _snapshot() -> RawSnapshot:
    return RawSnapshot(
        snapshot_id=1,
        captured_at="2026-04-13T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(
            RawNode(
                rid="w1:0",
                window_id="w1",
                parent_rid=None,
                child_rids=(),
                class_name="android.widget.Button",
                resource_id="android:id/button1",
                text="Wi-Fi",
                content_desc=None,
                hint_text=None,
                state_description=None,
                pane_title=None,
                package_name="com.android.settings",
                bounds=(10, 20, 90, 60),
                visible_to_user=True,
                important_for_accessibility=True,
                clickable=True,
                enabled=True,
                editable=False,
                focusable=True,
                focused=False,
                checkable=False,
                checked=False,
                selected=False,
                scrollable=False,
                password=False,
                actions=("click",),
            ),
        ),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )


def test_connect_success_updates_runtime_without_ledger_side_effects(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(bootstrap_result=_bootstrap_result())
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        snapshot_service=_FakeSnapshotService(),  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command())

    runtime = runtime_store.get_runtime()
    assert payload["ok"] is True
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["artifacts"] == {}
    assert payload["details"] == {}
    assert_retained_omits_semantic_fields(payload)
    assert "summary" not in payload
    assert "runtime" not in payload
    assert runtime.status is RuntimeStatus.READY
    assert runtime.connection is not None
    assert runtime.connection.serial == "emulator-5554"
    assert runtime.current_screen_id is not None
    assert runtime.current_screen_id.startswith("screen-")
    assert runtime.lifecycle_revision == 0
    assert bootstrapper.establish_calls == 1
    assert bootstrapper.bootstrap_calls == 1
    assert bootstrapper.establish_configs[0].serial == "emulator-5554"
    assert bootstrapper.bootstrap_configs[0].serial == "emulator-5554"


def test_connect_omitted_serial_stores_bootstrap_resolved_serial(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(bootstrap_result=_bootstrap_result())
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        snapshot_service=_FakeSnapshotService(),  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command(serial=None))

    runtime = runtime_store.get_runtime()
    assert payload["ok"] is True
    assert runtime.connection is not None
    assert runtime.connection.serial == "emulator-5554"
    assert bootstrapper.establish_configs[0].serial is None
    assert bootstrapper.bootstrap_configs[0].serial is None


def test_connect_failure_returns_retained_failure_and_marks_runtime_broken(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(bootstrap_error=_accessibility_not_ready_error())
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command())

    runtime = runtime_store.get_runtime()
    assert payload["ok"] is False
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["code"] == "ACCESSIBILITY_NOT_READY"
    assert payload["message"] == "accessibility not ready"
    assert payload["details"] == {"reason": "service-disabled"}
    assert_retained_omits_semantic_fields(payload)
    assert runtime.status is RuntimeStatus.BROKEN
    assert bootstrapper.establish_calls == 1
    assert bootstrapper.bootstrap_calls == 1


def test_connect_availability_failure_returns_retained_bootstrap_failure(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(bootstrap_error=_device_agent_unavailable_error())
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command())

    runtime = runtime_store.get_runtime()
    assert payload["ok"] is False
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["code"] == "DEVICE_AGENT_UNAVAILABLE"
    assert payload["message"] == "device agent unavailable"
    assert payload["details"] == {}
    assert_retained_omits_semantic_fields(payload)
    assert runtime.status is RuntimeStatus.BROKEN
    assert bootstrapper.establish_calls == 1
    assert bootstrapper.bootstrap_calls == 1


def test_connect_unauthorized_failure_returns_projected_retained_failure(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(bootstrap_error=_device_agent_unauthorized_error())
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command())

    assert payload["ok"] is False
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["code"] == "DEVICE_AGENT_UNAUTHORIZED"
    assert payload["message"] == "device agent unauthorized"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_UNAUTHORIZED",
        "sourceKind": "device",
        "reason": "wrong-token",
    }
    assert_retained_omits_semantic_fields(payload)


def test_connect_version_mismatch_returns_projected_retained_failure(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    bootstrapper = _FakeBootstrapper(
        bootstrap_error=_device_agent_version_mismatch_error()
    )
    service = CommandService(
        runtime_store,
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
    )

    payload = service.run(command=_connect_command())

    assert payload["ok"] is False
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["code"] == "DEVICE_AGENT_VERSION_MISMATCH"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
        "expectedReleaseVersion": "0.1.0",
        "actualReleaseVersion": "0.1.1",
    }
    assert_retained_omits_semantic_fields(payload)


def test_close_runtime_returns_unified_close_result(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = CommandService(runtime_store)
    payload = service.close_runtime()

    runtime = runtime_store.get_runtime()
    assert payload == {
        "ok": True,
        "command": "close",
        "envelope": "lifecycle",
        "artifacts": {},
        "details": {},
    }
    assert runtime.status is RuntimeStatus.CLOSED
