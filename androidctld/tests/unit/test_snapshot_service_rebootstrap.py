from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from androidctld.commands.assembly import assemble_command_service
from androidctld.device.types import (
    BootstrapResult,
    ConnectionConfig,
    ConnectionSpec,
    DeviceCapabilities,
    DeviceEndpoint,
    MetaInfo,
    RuntimeTransport,
)
from androidctld.errors import DaemonError
from androidctld.protocol import ConnectionMode
from androidctld.runtime import RuntimeKernel
from androidctld.snapshots.service import SnapshotService

from ..support.runtime_store import runtime_store_for_workspace
from .support.runtime import build_connected_runtime


class _CloseRecorder:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeBootstrapper:
    def __init__(
        self,
        result: BootstrapResult,
        *,
        on_bootstrap: Callable[[], None] | None = None,
    ) -> None:
        self.result = result
        self._on_bootstrap = on_bootstrap
        self.configs: list[ConnectionConfig] = []

    def bootstrap(self, config: ConnectionConfig) -> BootstrapResult:
        self.configs.append(config)
        if self._on_bootstrap is not None:
            self._on_bootstrap()
        return self.result


def _bootstrap_result(
    *,
    serial: str = "emulator-5554",
    transport: RuntimeTransport | None = None,
    supports_screenshot: bool = True,
) -> BootstrapResult:
    return BootstrapResult(
        connection=ConnectionSpec(mode=ConnectionMode.ADB, serial=serial),
        transport=transport
        or RuntimeTransport(
            endpoint=DeviceEndpoint(host="127.0.0.1", port=20001),
            close=lambda: None,
        ),
        meta=MetaInfo(
            service="androidctl-device-agent",
            version="test",
            capabilities=DeviceCapabilities(
                supports_events_poll=True,
                supports_screenshot=supports_screenshot,
                action_kinds=["tap"],
            ),
        ),
    )


def test_ensure_transport_returns_existing_transport_without_bootstrap(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = build_connected_runtime(tmp_path, device_token="device-token")
    existing_transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=20000),
        close=lambda: None,
    )
    runtime.transport = existing_transport
    bootstrapper = _FakeBootstrapper(_bootstrap_result())
    service = SnapshotService(
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        runtime_kernel=kernel,
    )

    transport = service.ensure_transport(runtime)

    assert transport is existing_transport
    assert bootstrapper.configs == []


def test_ensure_transport_rejects_stale_lease_before_existing_transport(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = build_connected_runtime(tmp_path, device_token="device-token")
    existing_transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=20000),
        close=lambda: None,
    )
    runtime.transport = existing_transport
    stale_lease = kernel.capture_lifecycle_lease(runtime)
    with runtime.lock:
        runtime.lifecycle_revision += 1
    bootstrapper = _FakeBootstrapper(_bootstrap_result())
    service = SnapshotService(
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        runtime_kernel=kernel,
    )

    with pytest.raises(DaemonError) as error:
        service.ensure_transport(runtime, lifecycle_lease=stale_lease)

    assert error.value.code == "RUNTIME_NOT_CONNECTED"
    assert bootstrapper.configs == []
    assert runtime.transport is existing_transport


def test_ensure_transport_rebootstrap_reuses_selected_adb_serial(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = build_connected_runtime(
        tmp_path,
        serial="emulator-5554",
        device_token="device-token",
    )
    runtime.transport = None
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=False,
        action_kinds=["tap"],
    )
    result = _bootstrap_result(serial="emulator-5554", supports_screenshot=True)
    bootstrapper = _FakeBootstrapper(result)
    service = SnapshotService(
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        runtime_kernel=kernel,
    )

    transport = service.ensure_transport(runtime)

    assert transport is result.transport
    assert cast(Any, runtime).transport is result.transport
    assert cast(Any, runtime).connection == result.connection
    assert runtime.device_capabilities == result.meta.capabilities
    assert len(bootstrapper.configs) == 1
    assert bootstrapper.configs[0].mode is ConnectionMode.ADB
    assert bootstrapper.configs[0].serial == "emulator-5554"
    assert bootstrapper.configs[0].token == "device-token"
    persisted = json.loads(runtime.runtime_path.read_text())
    assert "connection" not in persisted
    assert "deviceToken" not in persisted


def test_ensure_transport_stale_lifecycle_closes_bootstrapped_transport(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = build_connected_runtime(
        tmp_path,
        serial="emulator-5554",
        device_token="device-token",
    )
    close_recorder = _CloseRecorder()
    result = _bootstrap_result(
        transport=RuntimeTransport(
            endpoint=DeviceEndpoint(host="127.0.0.1", port=20001),
            close=close_recorder.close,
        )
    )

    def invalidate_runtime() -> None:
        kernel.invalidate_runtime(runtime)

    bootstrapper = _FakeBootstrapper(
        result,
        on_bootstrap=invalidate_runtime,
    )
    service = SnapshotService(
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        runtime_kernel=kernel,
    )

    with pytest.raises(DaemonError) as error:
        service.ensure_transport(runtime)

    assert error.value.code == "RUNTIME_NOT_CONNECTED"
    assert close_recorder.close_calls == 1
    assert runtime.transport is None
    assert runtime.connection is None


def test_default_assembly_device_client_factory_propagates_lifecycle_lease(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    seen: list[object | None] = []
    fake_client = object()

    class _RecordingSnapshotService:
        def device_client(
            self,
            session: Any,
            *,
            lifecycle_lease: object | None = None,
        ) -> Any:
            seen.append(lifecycle_lease)
            return fake_client

    snapshot_service = _RecordingSnapshotService()
    assembly = assemble_command_service(
        runtime_store=runtime_store,
        snapshot_service=snapshot_service,  # type: ignore[arg-type]
    )
    lifecycle_lease = assembly.runtime_kernel.capture_lifecycle_lease(runtime)

    client = assembly.device_client_factory(
        runtime,
        lifecycle_lease=lifecycle_lease,
    )

    assert client is fake_client
    assert seen == [lifecycle_lease]


def test_explicit_device_client_factory_receives_lifecycle_lease(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    seen: list[tuple[object, object | None]] = []
    fake_client = object()

    def _device_client_factory(
        session: object,
        *,
        lifecycle_lease: object | None = None,
    ) -> Any:
        seen.append((session, lifecycle_lease))
        return fake_client

    assembly = assemble_command_service(
        runtime_store=runtime_store,
        device_client_factory=_device_client_factory,
    )
    lifecycle_lease = assembly.runtime_kernel.capture_lifecycle_lease(runtime)

    client = assembly.device_client_factory(
        runtime,
        lifecycle_lease=lifecycle_lease,
    )

    assert client is fake_client
    assert seen == [(runtime, lifecycle_lease)]
