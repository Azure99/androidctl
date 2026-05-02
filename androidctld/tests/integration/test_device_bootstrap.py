from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from androidctl_contracts.daemon_api import ConnectCommandPayload
from androidctld import __version__ as ANDROIDCTLD_VERSION
from androidctld.commands.from_boundary import compile_connect_command
from androidctld.commands.service import CommandService
from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.connectors import (
    AdbConnector,
    ConnectorHandle,
    DeviceConnectorFactory,
)
from androidctld.device.errors import DeviceBootstrapError
from androidctld.device.types import ConnectionConfig, ConnectionSpec, DeviceEndpoint
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.runtime_policy import ADB_COMMAND_TIMEOUT_SECONDS

from ..support.runtime_store import runtime_store_for_workspace


class FakeDeviceAgentServer:
    def __init__(self) -> None:
        self.meta_version = ANDROIDCTLD_VERSION
        self.meta_extra_fields: dict[str, object] = {}
        self.supports_events_poll = True
        self.supports_screenshot = True
        self.action_kinds = [
            "tap",
            "type",
            "global",
            "launchApp",
        ]
        self.snapshot_error_code = None
        self.snapshot_error_message = "not ready"
        self.snapshot_node_class_name = "android.widget.TextView"
        self.snapshot_window_package_name = "com.android.settings"
        self.snapshot_node_package_name = "com.android.settings"
        self.http_auth_status: int | None = None
        self.unauthorized_details: dict[str, object] = {}
        self.methods: list[str] = []
        self._server = None
        self._thread = None

    def start(self) -> DeviceEndpoint:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                method = payload["method"]
                outer.methods.append(method)
                if self.headers.get("Authorization") != "Bearer test-token":
                    if outer.http_auth_status is not None:
                        self._write(outer.http_auth_status, {"error": "bad token"})
                        return
                    body = _rpc_error(
                        "UNAUTHORIZED",
                        "bad token",
                        False,
                        outer.unauthorized_details,
                    )
                    self._write(200, body)
                    return
                if method == "meta.get":
                    result = {
                        "service": "androidctl-device-agent",
                        "version": outer.meta_version,
                        "capabilities": {
                            "supportsEventsPoll": outer.supports_events_poll,
                            "supportsScreenshot": outer.supports_screenshot,
                            "actionKinds": outer.action_kinds,
                        },
                    }
                    result.update(outer.meta_extra_fields)
                    body = {
                        "id": payload["id"],
                        "ok": True,
                        "result": result,
                    }
                    self._write(200, body)
                    return
                if method == "snapshot.get":
                    if outer.snapshot_error_code:
                        body = _rpc_error(
                            outer.snapshot_error_code,
                            outer.snapshot_error_message,
                            True,
                            {},
                            request_id=payload["id"],
                        )
                        self._write(200, body)
                        return
                    body = {
                        "id": payload["id"],
                        "ok": True,
                        "result": {
                            "snapshotId": 1,
                            "capturedAt": "2026-03-17T00:00:00Z",
                            "packageName": "com.android.settings",
                            "activityName": None,
                            "display": {
                                "widthPx": 1,
                                "heightPx": 1,
                                "densityDpi": 1,
                                "rotation": 0,
                            },
                            "ime": {"visible": False, "windowId": None},
                            "windows": [
                                {
                                    "windowId": "w1",
                                    "type": "application",
                                    "layer": 0,
                                    "packageName": outer.snapshot_window_package_name,
                                    "bounds": [0, 0, 1, 1],
                                    "rootRid": "w1:0",
                                }
                            ],
                            "nodes": [
                                {
                                    "rid": "w1:0",
                                    "windowId": "w1",
                                    "parentRid": None,
                                    "childRids": [],
                                    "className": outer.snapshot_node_class_name,
                                    "resourceId": None,
                                    "text": "Settings",
                                    "contentDesc": None,
                                    "hintText": None,
                                    "stateDescription": None,
                                    "paneTitle": None,
                                    "packageName": outer.snapshot_node_package_name,
                                    "bounds": [0, 0, 1, 1],
                                    "visibleToUser": True,
                                    "importantForAccessibility": True,
                                    "clickable": False,
                                    "enabled": True,
                                    "editable": False,
                                    "focusable": False,
                                    "focused": False,
                                    "checkable": False,
                                    "checked": False,
                                    "selected": False,
                                    "scrollable": False,
                                    "password": False,
                                    "actions": [],
                                }
                            ],
                        },
                    }
                    self._write(200, body)
                    return
                self._write(
                    200,
                    _rpc_error(
                        "INVALID_REQUEST",
                        "unknown method",
                        False,
                        {},
                        request_id=payload["id"],
                    ),
                )

            def log_message(self, format, *args):
                return

            def _write(self, status, payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return DeviceEndpoint(host=host, port=port)

    def stop(self) -> None:
        if self._server is None:
            return
        assert self._thread is not None
        self._server.shutdown()
        self._thread.join(timeout=2.0)
        assert (
            not self._thread.is_alive()
        ), "fake device agent server thread did not stop"
        self._server.server_close()
        self._server = None
        self._thread = None


def _rpc_error(code, message, retryable, details, request_id="androidctld-bootstrap"):
    return {
        "id": request_id,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        },
    }


class StaticConnectorFactory(DeviceConnectorFactory):
    def __init__(self, endpoint: DeviceEndpoint) -> None:
        self._endpoint = endpoint
        super().__init__()

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        return ConnectorHandle(
            endpoint=self._endpoint,
            close=lambda: None,
            connection=ConnectionSpec.from_config(config),
        )


class CountingConnectorFactory(DeviceConnectorFactory):
    def __init__(self, endpoint: DeviceEndpoint) -> None:
        self._endpoint = endpoint
        self.close_calls = 0
        super().__init__()

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        return ConnectorHandle(
            endpoint=self._endpoint,
            close=self._close,
            connection=ConnectionSpec.from_config(config),
        )

    def _close(self) -> None:
        self.close_calls += 1


class ResolvedConnectorFactory(DeviceConnectorFactory):
    def __init__(self, endpoint: DeviceEndpoint, connection: ConnectionSpec) -> None:
        self._endpoint = endpoint
        self._connection = connection
        super().__init__()

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        del config
        return ConnectorHandle(
            endpoint=self._endpoint,
            close=lambda: None,
            connection=self._connection,
        )


def _lan_config(
    endpoint: DeviceEndpoint, *, token: str = "test-token"
) -> ConnectionConfig:
    return ConnectionConfig(
        mode="lan",
        host=endpoint.host,
        port=endpoint.port,
        token=token,
    )


def _adb_config(
    *, serial: str | None, token: str = "t", port: int = 17171
) -> ConnectionConfig:
    return ConnectionConfig(
        mode=ConnectionMode.ADB,
        serial=serial,
        token=token,
        port=port,
    )


@pytest.fixture
def fake_device_agent_server() -> (
    Iterator[tuple[FakeDeviceAgentServer, DeviceEndpoint]]
):
    server = FakeDeviceAgentServer()
    endpoint = server.start()
    try:
        yield server, endpoint
    finally:
        server.stop()


def test_bootstrap_returns_meta_and_endpoint(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    result = bootstrapper.bootstrap(_lan_config(endpoint))
    assert endpoint == result.transport.endpoint
    assert str(result.connection.mode) == "lan"
    assert result.meta.capabilities.supports_events_poll
    assert server.methods == ["meta.get", "snapshot.get"]
    result.transport.close()


def test_establish_transport_and_bootstrap_runtime_support_staged_connect(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    config = _lan_config(endpoint)

    handle = bootstrapper.establish_transport(config)
    result = bootstrapper.bootstrap_runtime(handle, config)

    assert endpoint == result.transport.endpoint
    assert server.methods == ["meta.get", "snapshot.get"]
    result.transport.close()


def test_bootstrap_runtime_uses_resolved_handle_connection(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    _, endpoint = fake_device_agent_server
    resolved = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    bootstrapper = DeviceBootstrapper(
        connector_factory=ResolvedConnectorFactory(endpoint, resolved)
    )
    config = _adb_config(serial=None, token="test-token")

    handle = bootstrapper.establish_transport(config)
    result = bootstrapper.bootstrap_runtime(handle, config)

    assert result.connection == resolved
    assert result.connection.serial == "emulator-5554"
    result.transport.close()


def test_bootstrap_wrapper_closes_transport_on_unexpected_exception(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    _, endpoint = fake_device_agent_server
    connector_factory = CountingConnectorFactory(endpoint)

    class UnexpectedFailureBootstrapper(DeviceBootstrapper):
        def bootstrap_runtime(self, handle, config):
            raise RuntimeError("boom")

    bootstrapper = UnexpectedFailureBootstrapper(connector_factory=connector_factory)

    with pytest.raises(RuntimeError):
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert connector_factory.close_calls == 1


def test_bootstrap_maps_version_mismatch(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.meta_version = "0.1.1"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))
    assert excinfo.value.code == "DEVICE_AGENT_VERSION_MISMATCH"
    assert excinfo.value.details == {
        "expectedReleaseVersion": ANDROIDCTLD_VERSION,
        "actualReleaseVersion": "0.1.1",
    }
    assert "release version mismatch" in excinfo.value.message
    assert server.methods == ["meta.get"]


def test_bootstrap_rpc_version_extra_field_stops_before_release_mismatch_and_readiness(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.meta_version = "0.1.1"
    server.meta_extra_fields = {"rpcVersion": 1}
    server.snapshot_error_code = "ACCESSIBILITY_DISABLED"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert excinfo.value.code == "DEVICE_AGENT_VERSION_MISMATCH"
    assert excinfo.value.details == {"reason": "legacy_rpc_version_field"}
    assert server.methods == ["meta.get"]


def test_bootstrap_remaps_rpc_version_extra_field_to_version_mismatch(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.meta_extra_fields = {"rpcVersion": 1}
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert excinfo.value.code == "DEVICE_AGENT_VERSION_MISMATCH"
    assert excinfo.value.details == {"reason": "legacy_rpc_version_field"}
    assert "install matching androidctld and Android agent/APK versions" in (
        excinfo.value.message
    )
    assert server.methods == ["meta.get"]


def test_bootstrap_keeps_unknown_extra_meta_field_as_generic_schema_failure(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.meta_version = "0.1.1"
    server.meta_extra_fields = {"extraField": True}
    server.snapshot_error_code = "ACCESSIBILITY_DISABLED"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert excinfo.value.code == "DEVICE_RPC_FAILED"
    assert excinfo.value.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["extraField"],
    }
    assert server.methods == ["meta.get"]


def test_bootstrap_does_not_remap_rpc_version_when_other_unknown_fields_exist(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.meta_version = "0.1.1"
    server.meta_extra_fields = {"extraField": True, "rpcVersion": 1}
    server.snapshot_error_code = "ACCESSIBILITY_DISABLED"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert excinfo.value.code == "DEVICE_RPC_FAILED"
    assert excinfo.value.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["extraField", "rpcVersion"],
    }
    assert server.methods == ["meta.get"]


def test_bootstrap_maps_accessibility_not_ready(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.snapshot_error_code = "ACCESSIBILITY_DISABLED"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))
    assert excinfo.value.code == "ACCESSIBILITY_NOT_READY"


def test_bootstrap_maps_device_agent_unauthorized(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    _, endpoint = fake_device_agent_server
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint, token="wrong-token"))
    assert excinfo.value.code == "DEVICE_AGENT_UNAUTHORIZED"


def test_bootstrap_maps_http_auth_rejection_to_device_agent_unauthorized(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.http_auth_status = 401
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint, token="wrong-token"))
    assert excinfo.value.code == "DEVICE_AGENT_UNAUTHORIZED"


def test_connect_wrong_device_token_returns_retained_bootstrap_failure(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
    tmp_path: Path,
) -> None:
    server, endpoint = fake_device_agent_server
    server.unauthorized_details = {
        "reason": "wrong-token",
        "token": "Bearer device-secret",
        "serial": "emulator-5554",
        "endpoint": endpoint.base_url,
        "raw": {"Authorization": "Bearer device-secret"},
    }
    connector_factory = CountingConnectorFactory(endpoint)
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = CommandService(
        runtime_store,
        bootstrapper=DeviceBootstrapper(connector_factory=connector_factory),
    )
    command = compile_connect_command(
        ConnectCommandPayload.model_validate(
            {
                "kind": "connect",
                "connection": {
                    "mode": "lan",
                    "host": endpoint.host,
                    "port": endpoint.port,
                    "token": "wrong-token",
                },
            }
        )
    )

    payload = service.run(command=command)

    runtime = runtime_store.get_runtime()
    assert payload["ok"] is False
    assert payload["command"] == "connect"
    assert payload["envelope"] == "bootstrap"
    assert payload["code"] == "DEVICE_AGENT_UNAUTHORIZED"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_UNAUTHORIZED",
        "sourceKind": "device",
    }
    assert "message" in payload
    serialized_payload = json.dumps(payload, sort_keys=True)
    for unsafe in (
        "wrong-token",
        "Bearer",
        "device-secret",
        "emulator-5554",
        endpoint.base_url,
        "raw",
        "Authorization",
    ):
        assert unsafe not in serialized_payload
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.connection is None
    assert runtime.device_token is None
    assert runtime.device_capabilities is None
    assert runtime.transport is None
    assert server.methods == ["meta.get"]
    assert connector_factory.close_calls == 1


def test_bootstrap_rejects_missing_supports_events_poll(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.supports_events_poll = False
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))
    assert excinfo.value.code == "DEVICE_AGENT_CAPABILITY_MISMATCH"
    assert excinfo.value.details["missingCapabilities"] == ["supportsEventsPoll"]


def test_bootstrap_accepts_missing_optional_capabilities(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.supports_screenshot = False
    server.action_kinds = ["tap", "type", "global", "launchApp"]
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    result = bootstrapper.bootstrap(_lan_config(endpoint))
    assert not result.meta.capabilities.supports_screenshot
    result.transport.close()


def test_bootstrap_accepts_normalized_fallback_view_class_name(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.snapshot_node_class_name = "android.view.View"
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    result = bootstrapper.bootstrap(_lan_config(endpoint))

    assert server.methods[-1] == "snapshot.get"
    result.transport.close()


def test_bootstrap_accepts_nullable_nested_snapshot_package_names(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.snapshot_window_package_name = None
    server.snapshot_node_package_name = None
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    result = bootstrapper.bootstrap(_lan_config(endpoint))

    assert server.methods[-1] == "snapshot.get"
    result.transport.close()


def test_bootstrap_rejects_null_snapshot_node_class_name(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.snapshot_node_class_name = None
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))

    assert excinfo.value.code == "DEVICE_RPC_FAILED"
    assert excinfo.value.details["field"] == "result.nodes[0].className"


def test_bootstrap_rejects_missing_required_action_kinds(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    server, endpoint = fake_device_agent_server
    server.action_kinds = ["tap", "type", "launchApp"]
    bootstrapper = DeviceBootstrapper(
        connector_factory=StaticConnectorFactory(endpoint)
    )
    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_lan_config(endpoint))
    assert excinfo.value.code == "DEVICE_AGENT_CAPABILITY_MISMATCH"
    assert "global" in excinfo.value.details["missingActionKinds"]


def test_adb_connector_uses_forward_and_remove() -> None:
    calls: list[tuple[list[str], float]] = []

    def runner(command, check, capture_output, text, timeout):
        calls.append((command, timeout))

        class Completed:
            returncode = 0
            stdout = "20001\n"
            stderr = ""

        return Completed()

    connector = AdbConnector(runner=runner)
    handle = connector.connect(_adb_config(serial="emulator-5554"))
    assert handle.endpoint.host == "127.0.0.1"
    assert handle.endpoint.port == 20001
    assert handle.connection.serial == "emulator-5554"
    handle.close()
    assert calls == [
        (
            ["adb", "-s", "emulator-5554", "forward", "tcp:0", "tcp:17171"],
            ADB_COMMAND_TIMEOUT_SECONDS,
        ),
        (
            ["adb", "-s", "emulator-5554", "forward", "--remove", "tcp:20001"],
            ADB_COMMAND_TIMEOUT_SECONDS,
        ),
    ]


def test_adb_connector_auto_selects_one_eligible_device() -> None:
    calls: list[tuple[list[str], float]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        calls.append((command, timeout))
        if command == ["adb", "devices"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="List of devices attached\nemulator-5554\tdevice\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="20001\n", stderr="")

    connector = AdbConnector(runner=runner)
    handle = connector.connect(_adb_config(serial=None))

    assert handle.endpoint.port == 20001
    assert handle.connection.serial == "emulator-5554"
    handle.close()
    assert calls == [
        (["adb", "devices"], ADB_COMMAND_TIMEOUT_SECONDS),
        (
            ["adb", "-s", "emulator-5554", "forward", "tcp:0", "tcp:17171"],
            ADB_COMMAND_TIMEOUT_SECONDS,
        ),
        (
            ["adb", "-s", "emulator-5554", "forward", "--remove", "tcp:20001"],
            ADB_COMMAND_TIMEOUT_SECONDS,
        ),
    ]


def test_adb_connector_auto_select_ignores_extra_device_columns() -> None:
    calls: list[list[str]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        assert timeout == ADB_COMMAND_TIMEOUT_SECONDS
        calls.append(command)
        if command == ["adb", "devices"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "List of devices attached\n"
                    "R5CT12345\tdevice product:x model:y device:z\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="20001\n", stderr="")

    connector = AdbConnector(runner=runner)
    handle = connector.connect(_adb_config(serial=None))

    assert handle.connection.serial == "R5CT12345"
    assert calls[1] == ["adb", "-s", "R5CT12345", "forward", "tcp:0", "tcp:17171"]


@pytest.mark.parametrize(
    "devices_output",
    [
        "List of devices attached\n",
        "adb device chatter\nList of devices attached\n",
        "not-a-real-row device\n",
        "List of devices attached\nemulator-5554\toffline\nR5CT12345\tunauthorized\n",
    ],
)
def test_adb_connector_auto_select_fails_with_zero_eligible_devices(
    devices_output: str,
) -> None:
    calls: list[list[str]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        assert timeout == ADB_COMMAND_TIMEOUT_SECONDS
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=devices_output, stderr="")

    connector = AdbConnector(runner=runner)

    with pytest.raises(DeviceBootstrapError) as excinfo:
        connector.connect(_adb_config(serial=None))

    assert excinfo.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert excinfo.value.details["reason"] == "no_eligible_adb_device"
    assert calls == [["adb", "devices"]]


def test_adb_connector_auto_select_fails_with_multiple_eligible_devices() -> None:
    calls: list[list[str]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        assert timeout == ADB_COMMAND_TIMEOUT_SECONDS
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "List of devices attached\n"
                "emulator-5554\tdevice\n"
                "R5CT12345\tdevice product:x\n"
            ),
            stderr="",
        )

    connector = AdbConnector(runner=runner)

    with pytest.raises(DeviceBootstrapError) as excinfo:
        connector.connect(_adb_config(serial=None))

    assert excinfo.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert "explicit --serial" in excinfo.value.message
    assert excinfo.value.details["reason"] == "multiple_eligible_adb_devices"
    assert excinfo.value.details["hint"] == "pass explicit --serial"
    assert excinfo.value.details["eligibleSerials"] == ["emulator-5554", "R5CT12345"]
    assert calls == [["adb", "devices"]]


def test_adb_connector_auto_select_fails_when_adb_devices_fails() -> None:
    calls: list[list[str]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        assert timeout == ADB_COMMAND_TIMEOUT_SECONDS
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="adb failed")

    connector = AdbConnector(runner=runner)

    with pytest.raises(DeviceBootstrapError) as excinfo:
        connector.connect(_adb_config(serial=None))

    assert excinfo.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert excinfo.value.details["reason"] == "adb_devices_failed"
    assert excinfo.value.details["stderr"] == "adb failed"
    assert calls == [["adb", "devices"]]


def test_adb_connector_auto_select_timeout_maps_to_device_agent_unavailable() -> None:
    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        raise subprocess.TimeoutExpired(command, timeout)

    connector = AdbConnector(runner=runner)

    with pytest.raises(DeviceBootstrapError) as excinfo:
        connector.connect(_adb_config(serial=None))

    assert excinfo.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert excinfo.value.retryable
    assert excinfo.value.details == {
        "reason": "adb_command_timeout",
        "operation": "devices",
        "timeoutSeconds": ADB_COMMAND_TIMEOUT_SECONDS,
    }


def test_adb_connector_forward_timeout_maps_to_device_agent_unavailable() -> None:
    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        raise subprocess.TimeoutExpired(command, timeout)

    connector = AdbConnector(runner=runner)

    with pytest.raises(DeviceBootstrapError) as excinfo:
        connector.connect(_adb_config(serial="emulator-5554"))

    assert excinfo.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert excinfo.value.retryable
    assert excinfo.value.details == {
        "reason": "adb_command_timeout",
        "operation": "forward",
        "timeoutSeconds": ADB_COMMAND_TIMEOUT_SECONDS,
        "serial": "emulator-5554",
    }


def test_adb_connector_remove_forward_timeout_is_best_effort() -> None:
    calls: list[list[str]] = []

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        assert timeout == ADB_COMMAND_TIMEOUT_SECONDS
        calls.append(command)
        if command == [
            "adb",
            "-s",
            "emulator-5554",
            "forward",
            "--remove",
            "tcp:20001",
        ]:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, stdout="20001\n", stderr="")

    connector = AdbConnector(runner=runner)
    handle = connector.connect(_adb_config(serial="emulator-5554"))

    handle.close()

    assert calls == [
        ["adb", "-s", "emulator-5554", "forward", "tcp:0", "tcp:17171"],
        ["adb", "-s", "emulator-5554", "forward", "--remove", "tcp:20001"],
    ]


def test_bootstrap_failure_is_not_masked_by_remove_forward_timeout(
    fake_device_agent_server: tuple[FakeDeviceAgentServer, DeviceEndpoint],
) -> None:
    _, endpoint = fake_device_agent_server

    def runner(command, check, capture_output, text, timeout):
        del check, capture_output, text
        if command == [
            "adb",
            "-s",
            "emulator-5554",
            "forward",
            "--remove",
            f"tcp:{endpoint.port}",
        ]:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{endpoint.port}\n", stderr=""
        )

    bootstrapper = DeviceBootstrapper(
        connector_factory=DeviceConnectorFactory(
            adb_connector=AdbConnector(runner=runner)
        )
    )

    with pytest.raises(DeviceBootstrapError) as excinfo:
        bootstrapper.bootstrap(_adb_config(serial="emulator-5554", token="wrong-token"))

    assert excinfo.value.code == "DEVICE_AGENT_UNAUTHORIZED"
