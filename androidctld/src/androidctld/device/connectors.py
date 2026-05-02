"""Transport connectors for reaching the Android device agent."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from androidctld.config import DEFAULT_HOST
from androidctld.device.errors import device_agent_unavailable
from androidctld.device.types import ConnectionConfig, ConnectionSpec, DeviceEndpoint
from androidctld.protocol import ConnectionMode
from androidctld.runtime_policy import ADB_COMMAND_TIMEOUT_SECONDS


@dataclass
class ConnectorHandle:
    endpoint: DeviceEndpoint
    close: Callable[[], None]
    connection: ConnectionSpec


class LanConnector:
    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        if not config.host:
            raise device_agent_unavailable(
                "LAN connection requires host", {"mode": config.mode.value}
            )
        return ConnectorHandle(
            endpoint=DeviceEndpoint(host=config.host, port=config.port),
            close=lambda: None,
            connection=ConnectionSpec.from_config(config),
        )


class AdbConnector:
    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._runner = runner

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        serial = config.serial or self._select_serial()
        local_port = self._forward(serial, config.port)
        endpoint = DeviceEndpoint(host=DEFAULT_HOST, port=local_port)

        def close() -> None:
            self._remove_forward(serial, local_port)

        return ConnectorHandle(
            endpoint=endpoint,
            close=close,
            connection=ConnectionSpec(
                mode=ConnectionMode.ADB,
                serial=serial,
                port=config.port,
                host=None,
            ),
        )

    def _select_serial(self) -> str:
        completed = self._run_adb(
            ["adb", "devices"],
            operation="devices",
        )
        if completed.returncode != 0:
            raise device_agent_unavailable(
                "adb devices failed",
                {
                    "reason": "adb_devices_failed",
                    "stderr": completed.stderr.strip(),
                },
            )
        rows = _parse_adb_devices(completed.stdout)
        eligible_serials = [row.serial for row in rows if row.state == "device"]
        if len(eligible_serials) == 1:
            return eligible_serials[0]
        if not eligible_serials:
            raise device_agent_unavailable(
                "no eligible ADB devices found",
                {
                    "reason": "no_eligible_adb_device",
                    "deviceStates": _state_counts(rows),
                },
            )
        raise device_agent_unavailable(
            "multiple eligible ADB devices found; pass explicit --serial",
            {
                "reason": "multiple_eligible_adb_devices",
                "eligibleSerials": eligible_serials,
                "hint": "pass explicit --serial",
            },
        )

    def _forward(self, serial: str, remote_port: int) -> int:
        command = [
            "adb",
            "-s",
            serial,
            "forward",
            "tcp:0",
            f"tcp:{remote_port}",
        ]
        completed = self._run_adb(
            command,
            operation="forward",
            serial=serial,
        )
        if completed.returncode != 0:
            raise device_agent_unavailable(
                "adb forward failed",
                {
                    "serial": serial,
                    "stderr": completed.stderr.strip(),
                },
            )
        output = completed.stdout.strip()
        if not re.match(r"^\d+$", output):
            raise device_agent_unavailable(
                "adb forward did not return a local port",
                {
                    "serial": serial,
                    "stdout": output,
                },
            )
        return int(output)

    def _remove_forward(self, serial: str, local_port: int) -> None:
        command = [
            "adb",
            "-s",
            serial,
            "forward",
            "--remove",
            f"tcp:{local_port}",
        ]
        self._run_adb(
            command,
            operation="forward_remove",
            serial=serial,
            suppress_timeout=True,
        )

    def _run_adb(
        self,
        command: list[str],
        *,
        operation: str,
        serial: str | None = None,
        suppress_timeout: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=ADB_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            if suppress_timeout:
                stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                return subprocess.CompletedProcess(
                    exc.cmd,
                    124,
                    stdout=stdout,
                    stderr=stderr,
                )
            details: dict[str, object] = {
                "reason": "adb_command_timeout",
                "operation": operation,
                "timeoutSeconds": ADB_COMMAND_TIMEOUT_SECONDS,
            }
            if serial is not None:
                details["serial"] = serial
            raise device_agent_unavailable("ADB command timed out", details) from exc


@dataclass(frozen=True)
class _AdbDeviceRow:
    serial: str
    state: str


def _parse_adb_devices(output: str) -> list[_AdbDeviceRow]:
    rows: list[_AdbDeviceRow] = []
    saw_header = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "List of devices attached":
            saw_header = True
            continue
        if not saw_header:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        rows.append(_AdbDeviceRow(serial=serial, state=state))
    return rows


def _state_counts(rows: list[_AdbDeviceRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
    return counts


class DeviceConnectorFactory:
    def __init__(
        self,
        adb_connector: AdbConnector | None = None,
        lan_connector: LanConnector | None = None,
    ) -> None:
        self._adb_connector = adb_connector or AdbConnector()
        self._lan_connector = lan_connector or LanConnector()

    def connect(self, config: ConnectionConfig) -> ConnectorHandle:
        if config.mode is ConnectionMode.ADB:
            return self._adb_connector.connect(config)
        if config.mode is ConnectionMode.LAN:
            return self._lan_connector.connect(config)
        raise device_agent_unavailable(
            "unsupported connection mode", {"mode": config.mode.value}
        )
