from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from unittest.mock import patch

import pytest

import androidctld.device.bootstrap as bootstrap_module
from androidctld import __version__ as ANDROIDCTLD_VERSION
from androidctld.commands.command_models import ListAppsCommand, ObserveCommand
from androidctld.commands.handlers.list_apps import (
    ANDROID_APPS_LIST_METHOD,
    ListAppsCommandHandler,
    build_list_apps_result,
)
from androidctld.commands.models import CommandStatus
from androidctld.commands.orchestration import (
    CommandRunOrchestrator,
    current_command_record,
)
from androidctld.daemon.envelope import error_envelope, success_envelope
from androidctld.device.connectors import ConnectorHandle
from androidctld.device.errors import (
    device_agent_unauthorized,
    device_agent_unavailable,
    device_rpc_transport_reset,
    version_mismatch,
)
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
from androidctld.protocol import CommandKind, ConnectionMode, RuntimeStatus
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.store import RuntimeSerialCommandBusyError
from androidctld.runtime_policy import DEVICE_RPC_REQUEST_ID_LIST_APPS

from ..support.runtime_store import runtime_store_for_workspace


class FakeRpcClient:
    def __init__(
        self,
        *,
        payload: object | None = None,
        error: DaemonError | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.payload = {"apps": []} if payload is None else payload
        self.error = error
        self.on_call = on_call
        self.calls: list[dict[str, object]] = []

    def call_result_payload(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: str,
    ) -> object:
        self.calls.append(
            {"method": method, "params": params, "request_id": request_id}
        )
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.payload


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body)
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc, tb
        return False


class RecordingClientFactory:
    def __init__(self, client: FakeRpcClient) -> None:
        self.client = client
        self.calls: list[dict[str, object]] = []

    def __call__(self, endpoint: DeviceEndpoint, token: str) -> FakeRpcClient:
        self.calls.append({"endpoint": endpoint, "token": token})
        return self.client


class RecordingBootstrapper:
    def __init__(
        self,
        *,
        error: DaemonError | None = None,
        endpoint: DeviceEndpoint | None = None,
    ) -> None:
        self.error = error
        self.endpoint = endpoint or DeviceEndpoint(port=18181)
        self.rpc_only_calls: list[ConnectionConfig] = []
        self.full_bootstrap_calls: list[ConnectionConfig] = []
        self.closed = False

    def bootstrap_rpc_only(self, config: ConnectionConfig) -> BootstrapResult:
        self.rpc_only_calls.append(config)
        if self.error is not None:
            raise self.error
        return BootstrapResult(
            connection=ConnectionSpec.from_config(config),
            transport=RuntimeTransport(
                endpoint=self.endpoint,
                close=self._close,
            ),
            meta=MetaInfo(
                service="androidctl-device-agent",
                version=ANDROIDCTLD_VERSION,
                capabilities=DeviceCapabilities(
                    supports_events_poll=False,
                    supports_screenshot=False,
                    action_kinds=[],
                ),
            ),
        )

    def bootstrap(self, config: ConnectionConfig) -> BootstrapResult:
        self.full_bootstrap_calls.append(config)
        raise AssertionError("full bootstrap must not be used for listApps")

    def _close(self) -> None:
        self.closed = True


def test_build_list_apps_result_validates_and_hides_launchable() -> None:
    result = build_list_apps_result(
        {
            "apps": [
                {
                    "packageName": " com.android.settings ",
                    "appLabel": " Settings ",
                    "launchable": True,
                    "activityName": ".Settings",
                }
            ],
            "nextPage": None,
        }
    )

    dumped = result.model_dump(by_alias=True, mode="json")
    assert dumped == {
        "ok": True,
        "command": "list-apps",
        "apps": [
            {
                "packageName": "com.android.settings",
                "appLabel": "Settings",
            }
        ],
    }
    assert "launchable" not in dumped["apps"][0]


def test_build_list_apps_result_accepts_empty_app_list() -> None:
    assert build_list_apps_result({"apps": []}).apps == []


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (None, "result"),
        ({}, "result.apps"),
        ({"apps": "not-a-list"}, "result.apps"),
        ({"apps": ["com.android.settings"]}, "result.apps[0]"),
        (
            {"apps": [{"appLabel": "Settings", "launchable": True}]},
            "result.apps[0].packageName",
        ),
        (
            {
                "apps": [
                    {
                        "packageName": "",
                        "appLabel": "Settings",
                        "launchable": True,
                    }
                ]
            },
            "result.apps[0].packageName",
        ),
        (
            {"apps": [{"packageName": "com.android.settings", "launchable": True}]},
            "result.apps[0].appLabel",
        ),
        (
            {
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                    }
                ]
            },
            "result.apps[0].launchable",
        ),
        (
            {
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                        "launchable": False,
                    }
                ]
            },
            "result.apps[0].launchable",
        ),
        (
            {
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                        "launchable": "true",
                    }
                ]
            },
            "result.apps[0].launchable",
        ),
    ],
)
def test_build_list_apps_result_rejects_malformed_payload(
    payload: object,
    field: str,
) -> None:
    with pytest.raises(DaemonError) as error:
        build_list_apps_result(payload)

    assert error.value.code is DaemonErrorCode.DEVICE_RPC_FAILED
    assert error.value.message == "apps.list returned malformed payload"
    assert error.value.retryable is False
    assert error.value.details == {"field": field, "reason": "invalid_payload"}


def test_list_apps_handler_uses_existing_transport_without_bootstrap(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = _connected_runtime(store)
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(port=17172),
        close=lambda: None,
    )
    client = FakeRpcClient(
        payload={
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                    "launchable": True,
                }
            ]
        }
    )
    client_factory = RecordingClientFactory(client)
    bootstrapper = RecordingBootstrapper()

    payload = ListAppsCommandHandler(
        runtime_kernel=RuntimeKernel(store),
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        rpc_client_factory=client_factory,
    ).handle(command=ListAppsCommand())

    assert payload == {
        "ok": True,
        "command": "list-apps",
        "apps": [
            {
                "packageName": "com.android.settings",
                "appLabel": "Settings",
            }
        ],
    }
    assert client.calls == [
        {
            "method": ANDROID_APPS_LIST_METHOD,
            "params": {},
            "request_id": DEVICE_RPC_REQUEST_ID_LIST_APPS,
        }
    ]
    assert client_factory.calls == [
        {"endpoint": DeviceEndpoint(port=17172), "token": "device-token"}
    ]
    assert bootstrapper.rpc_only_calls == []
    assert bootstrapper.full_bootstrap_calls == []


def test_list_apps_handler_rebuilds_transport_without_full_bootstrap(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    _connected_runtime(store)
    client = FakeRpcClient(payload={"apps": []})
    client_factory = RecordingClientFactory(client)
    bootstrapper = RecordingBootstrapper(endpoint=DeviceEndpoint(port=18181))

    payload = ListAppsCommandHandler(
        runtime_kernel=RuntimeKernel(store),
        bootstrapper=bootstrapper,  # type: ignore[arg-type]
        rpc_client_factory=client_factory,
    ).handle(command=ListAppsCommand())

    assert payload == {"ok": True, "command": "list-apps", "apps": []}
    assert len(bootstrapper.rpc_only_calls) == 1
    assert bootstrapper.full_bootstrap_calls == []
    assert client_factory.calls == [
        {"endpoint": DeviceEndpoint(port=18181), "token": "device-token"}
    ]


@pytest.mark.parametrize(
    "daemon_error",
    [
        version_mismatch("bad version", {"reason": "unit-test"}),
        device_agent_unauthorized("bad token", {"reason": "unit-test"}),
    ],
)
def test_list_apps_handler_propagates_rebootstrap_outer_errors(
    tmp_path: Path,
    daemon_error: DaemonError,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    _connected_runtime(store)

    with pytest.raises(DaemonError) as error:
        ListAppsCommandHandler(
            runtime_kernel=RuntimeKernel(store),
            bootstrapper=RecordingBootstrapper(error=daemon_error),  # type: ignore[arg-type]
            rpc_client_factory=RecordingClientFactory(FakeRpcClient()),
        ).handle(command=ListAppsCommand())

    assert error.value is daemon_error


@pytest.mark.parametrize(
    "rpc_error",
    [
        device_agent_unauthorized("bad token", {"reason": "unit-test"}),
        device_agent_unavailable("agent unavailable", {"reason": "unit-test"}),
        device_rpc_transport_reset("transport reset", {"reason": "unit-test"}),
        DaemonError(
            code=DaemonErrorCode.DEVICE_RPC_FAILED,
            message="unsupported method",
            retryable=False,
            details={"deviceCode": "INVALID_REQUEST"},
            http_status=200,
        ),
    ],
)
def test_list_apps_handler_propagates_rpc_outer_errors(
    tmp_path: Path,
    rpc_error: DaemonError,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = _connected_runtime(store)
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(port=17172),
        close=lambda: None,
    )

    with pytest.raises(DaemonError) as error:
        ListAppsCommandHandler(
            runtime_kernel=RuntimeKernel(store),
            bootstrapper=RecordingBootstrapper(),  # type: ignore[arg-type]
            rpc_client_factory=RecordingClientFactory(FakeRpcClient(error=rpc_error)),
        ).handle(command=ListAppsCommand())

    assert error.value is rpc_error


def test_list_apps_handler_validates_real_rpc_top_level_non_object_result(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = _connected_runtime(store)
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(port=17172),
        close=lambda: None,
    )

    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse({"ok": True, "result": ["not-object"]}),
        ),
        pytest.raises(DaemonError) as error,
    ):
        ListAppsCommandHandler(
            runtime_kernel=RuntimeKernel(store),
            bootstrapper=RecordingBootstrapper(),  # type: ignore[arg-type]
        ).handle(command=ListAppsCommand())

    assert error.value.code is DaemonErrorCode.DEVICE_RPC_FAILED
    assert error.value.message == "apps.list returned malformed payload"
    assert error.value.details == {"field": "result", "reason": "invalid_payload"}


def test_list_apps_handler_returns_outer_error_on_malformed_payload(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = _connected_runtime(store)
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(port=17172),
        close=lambda: None,
    )

    with pytest.raises(DaemonError) as error:
        ListAppsCommandHandler(
            runtime_kernel=RuntimeKernel(store),
            bootstrapper=RecordingBootstrapper(),  # type: ignore[arg-type]
            rpc_client_factory=RecordingClientFactory(
                FakeRpcClient(payload={"apps": [{}]})
            ),
        ).handle(command=ListAppsCommand())

    assert error.value.code is DaemonErrorCode.DEVICE_RPC_FAILED
    assert error.value.details == {
        "field": "result.apps[0].packageName",
        "reason": "invalid_payload",
    }


def test_list_apps_handler_does_not_return_success_after_runtime_close_race(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime_kernel = RuntimeKernel(store)
    runtime = _connected_runtime(store)
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(port=17172),
        close=lambda: None,
    )

    with pytest.raises(DaemonError) as error:
        ListAppsCommandHandler(
            runtime_kernel=runtime_kernel,
            bootstrapper=RecordingBootstrapper(),  # type: ignore[arg-type]
            rpc_client_factory=RecordingClientFactory(
                FakeRpcClient(
                    payload={"apps": []},
                    on_call=lambda: runtime_kernel.close_runtime(runtime),
                )
            ),
        ).handle(command=ListAppsCommand())

    assert error.value.code is DaemonErrorCode.RUNTIME_NOT_CONNECTED
    assert error.value.details["reason"] == "runtime_lifecycle_changed"


def test_device_bootstrapper_rpc_only_skips_capability_gate_and_snapshot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeDeviceRpcClient:
        def __init__(self, endpoint: DeviceEndpoint, token: str) -> None:
            assert endpoint == DeviceEndpoint(port=18181)
            assert token == "device-token"

        def meta_get(self) -> MetaInfo:
            calls.append("meta.get")
            return MetaInfo(
                service="androidctl-device-agent",
                version=ANDROIDCTLD_VERSION,
                capabilities=DeviceCapabilities(
                    supports_events_poll=False,
                    supports_screenshot=False,
                    action_kinds=[],
                ),
            )

        def snapshot_get(self) -> object:
            calls.append("snapshot.get")
            raise AssertionError("snapshot.get must not be called")

    monkeypatch.setattr(bootstrap_module, "DeviceRpcClient", FakeDeviceRpcClient)
    closed: list[bool] = []
    bootstrapper = bootstrap_module.DeviceBootstrapper(
        connector_factory=_StaticConnectorFactory(
            endpoint=DeviceEndpoint(port=18181),
            close=lambda: closed.append(True),
        )
    )

    result = bootstrapper.bootstrap_rpc_only(
        ConnectionConfig(
            mode=ConnectionMode.ADB,
            token="device-token",
            serial="emulator-5554",
        )
    )

    assert calls == ["meta.get"]
    assert result.transport.endpoint == DeviceEndpoint(port=18181)
    assert closed == []


def test_device_bootstrapper_rpc_only_closes_transport_on_meta_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDeviceRpcClient:
        def __init__(self, endpoint: DeviceEndpoint, token: str) -> None:
            del endpoint, token

        def meta_get(self) -> MetaInfo:
            raise device_agent_unauthorized("bad token")

    monkeypatch.setattr(bootstrap_module, "DeviceRpcClient", FakeDeviceRpcClient)
    closed: list[bool] = []
    bootstrapper = bootstrap_module.DeviceBootstrapper(
        connector_factory=_StaticConnectorFactory(
            endpoint=DeviceEndpoint(port=18181),
            close=lambda: closed.append(True),
        )
    )

    with pytest.raises(DaemonError) as error:
        bootstrapper.bootstrap_rpc_only(
            ConnectionConfig(
                mode=ConnectionMode.ADB,
                token="device-token",
                serial="emulator-5554",
            )
        )

    assert error.value.code is DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED
    assert closed == [True]


def test_orchestrator_finalizes_and_records_list_apps_result(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator()
    seen_records = []

    def execute() -> dict[str, object]:
        record = current_command_record(
            kind=CommandKind.LIST_APPS,
            result_command="list-apps",
        )
        seen_records.append(record)
        return {
            "ok": True,
            "command": "list-apps",
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                }
            ],
        }

    payload = orchestrator.run(
        runtime=store.ensure_runtime(),
        command=ListAppsCommand(),
        execute=execute,
    )

    assert payload["command"] == "list-apps"
    assert payload["apps"] == [
        {"packageName": "com.android.settings", "appLabel": "Settings"}
    ]
    record = seen_records[0]
    assert record.status is CommandStatus.SUCCEEDED
    assert record.result is not None
    assert record.result.command == "list-apps"


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"ok": True, "command": "observe", "apps": []},
        {"ok": True, "command": "list-apps", "category": "observe"},
        {"ok": True, "command": "screenshot", "envelope": "artifact"},
    ],
)
def test_orchestrator_rejects_non_list_apps_result_for_list_apps(
    tmp_path: Path,
    bad_payload: dict[str, object],
) -> None:
    with pytest.raises((ValueError, DaemonError)):
        CommandRunOrchestrator().run(
            runtime=runtime_store_for_workspace(tmp_path).ensure_runtime(),
            command=ListAppsCommand(),
            execute=lambda: bad_payload,
        )


def test_orchestrator_serial_busy_for_list_apps_is_outer_error(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(
        serial_admission=lambda _: _raise_serial_busy()
    )

    with pytest.raises(DaemonError) as error:
        orchestrator.run(
            runtime=store.ensure_runtime(),
            command=ListAppsCommand(),
            execute=lambda: {"ok": True, "command": "list-apps", "apps": []},
        )

    assert error.value.code is DaemonErrorCode.RUNTIME_BUSY


def test_daemon_outer_envelopes_keep_list_apps_failures_out_of_result() -> None:
    success = success_envelope({"ok": True, "command": "list-apps", "apps": []})
    failure = error_envelope(
        DaemonError(
            code=DaemonErrorCode.DEVICE_RPC_FAILED,
            message="apps.list returned malformed payload",
            retryable=False,
            details={"field": "result.apps", "reason": "invalid_payload"},
            http_status=200,
        )
    )

    assert success["ok"] is True
    assert success["result"] == {"ok": True, "command": "list-apps", "apps": []}
    assert failure["ok"] is False
    assert "result" not in failure
    assert "apps" not in failure


def test_execute_list_apps_rejects_wrong_internal_command(tmp_path: Path) -> None:
    from androidctld.commands.assembly import assemble_command_service

    dispatch = assemble_command_service(
        runtime_store=runtime_store_for_workspace(tmp_path)
    ).dispatch

    with pytest.raises(TypeError, match="list-apps handler received"):
        dispatch.execute_list_apps(command=ObserveCommand())


class _StaticConnectorFactory:
    def __init__(self, *, endpoint: DeviceEndpoint, close: Callable[[], None]) -> None:
        self.endpoint = endpoint
        self.close = close

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        return ConnectorHandle(
            endpoint=self.endpoint,
            close=self.close,
            connection=ConnectionSpec.from_config(config),
        )


def _connected_runtime(store: Any) -> Any:
    runtime = store.ensure_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    return runtime


def _raise_serial_busy() -> object:
    raise RuntimeSerialCommandBusyError("overlapping control requests are not allowed")
