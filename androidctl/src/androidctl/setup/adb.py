from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ANDROIDCTL_PACKAGE = "com.rainng.androidctl"
ANDROIDCTL_MAIN_ACTIVITY = f"{ANDROIDCTL_PACKAGE}/.MainActivity"
ANDROIDCTL_SETUP_ACTIVITY = f"{ANDROIDCTL_PACKAGE}/.SetupActivity"
ANDROIDCTL_SETUP_ACTION = "com.rainng.androidctl.action.SETUP"
ANDROIDCTL_ACCESSIBILITY_SERVICE_CLASS = (
    f"{ANDROIDCTL_PACKAGE}.agent.service.DeviceAccessibilityService"
)
ANDROIDCTL_ACCESSIBILITY_SERVICE = (
    f"{ANDROIDCTL_PACKAGE}/{ANDROIDCTL_ACCESSIBILITY_SERVICE_CLASS}"
)
ANDROIDCTL_AGENT_PORT = 17171

_MAX_ERROR_OUTPUT_CHARS = 400
_TOKEN_PATTERNS = (
    re.compile(r"(?i)\b(token\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)\b(bearer\s+)(\S+)"),
)


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    details: tuple[str, ...] = ()

    @property
    def is_eligible(self) -> bool:
        return self.state == "device"


class SetupAdbError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.layer = "ADB"


@dataclass(frozen=True)
class AdbCommandResult:
    stdout: str = ""
    stderr: str = ""


def build_adb_command(
    args: Sequence[str],
    *,
    adb_path: str = "adb",
    serial: str | None = None,
) -> list[str]:
    command = [adb_path]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    return command


def run_adb(
    args: Sequence[str],
    *,
    adb_path: str = "adb",
    serial: str | None = None,
    timeout_s: float = 10.0,
    operation: str = "adb command",
    failure_code: str = "ADB_COMMAND_FAILED",
    sensitive_values: Iterable[str] = (),
) -> AdbCommandResult:
    result = _run_adb_process(
        args,
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation=operation,
    )
    _raise_for_nonzero(
        result,
        code=failure_code,
        operation=operation,
        sensitive_values=_sensitive_values(serial, sensitive_values),
    )
    return AdbCommandResult(stdout=result.stdout, stderr=result.stderr)


def list_adb_devices(
    *,
    adb_path: str = "adb",
    timeout_s: float = 5.0,
) -> list[AdbDevice]:
    result = run_adb(
        ["devices"],
        adb_path=adb_path,
        timeout_s=timeout_s,
        operation="adb devices",
        failure_code="ADB_COMMAND_FAILED",
    )
    return parse_adb_devices_output(result.stdout)


def parse_adb_devices_output(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line == "List of devices attached" or line.startswith("* "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        devices.append(
            AdbDevice(
                serial=parts[0],
                state=parts[1],
                details=tuple(parts[2:]),
            )
        )
    return devices


def select_eligible_device(
    devices: list[AdbDevice],
    *,
    serial: str | None = None,
) -> AdbDevice:
    if serial is not None:
        for device in devices:
            if device.serial != serial:
                continue
            if device.is_eligible:
                return device
            raise SetupAdbError(
                "NO_ELIGIBLE_ADB_DEVICE",
                f"requested ADB device is {device.state!r}, expected 'device'",
            )
        raise SetupAdbError(
            "NO_ELIGIBLE_ADB_DEVICE",
            "requested ADB device was not found",
        )

    eligible_devices = [device for device in devices if device.is_eligible]
    if not eligible_devices:
        raise SetupAdbError(
            "NO_ELIGIBLE_ADB_DEVICE",
            "no authorized ADB device is in 'device' state",
        )
    if len(eligible_devices) > 1:
        raise SetupAdbError(
            "MULTIPLE_ADB_DEVICES",
            "multiple authorized ADB devices found; pass --serial",
        )
    return eligible_devices[0]


def install_apk(
    apk_path: Path,
    *,
    serial: str,
    adb_path: str = "adb",
    timeout_s: float = 120.0,
) -> AdbCommandResult:
    if not apk_path.is_file():
        raise SetupAdbError("APK_NOT_FOUND", "APK file was not found")

    operation = "adb install"
    result = _run_adb_process(
        ["install", "-r", str(apk_path)],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation=operation,
    )
    output = _combined_output(result)
    if result.returncode != 0:
        code = classify_install_failure(output)
        message = _install_failure_message(code)
        _raise_for_nonzero(
            result,
            code=code,
            operation=operation,
            sensitive_values=_sensitive_values(serial, (str(apk_path),)),
            message_prefix=message,
        )
    return AdbCommandResult(stdout=result.stdout, stderr=result.stderr)


def classify_install_failure(output: str) -> str:
    if "INSTALL_FAILED_VERSION_DOWNGRADE" in output:
        return "ADB_INSTALL_DOWNGRADE"
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output:
        return "ADB_INSTALL_SIGNATURE_MISMATCH"
    if "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE" in output:
        return "ADB_INSTALL_SIGNATURE_MISMATCH"
    return "ADB_INSTALL_FAILED"


def force_stop_app(
    *,
    serial: str,
    adb_path: str = "adb",
    package: str = ANDROIDCTL_PACKAGE,
    timeout_s: float = 10.0,
) -> AdbCommandResult:
    return run_adb(
        ["shell", "am", "force-stop", package],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb force-stop app",
        failure_code="ADB_FORCE_STOP_FAILED",
    )


def start_setup_activity(
    *,
    serial: str,
    adb_path: str = "adb",
    component: str = ANDROIDCTL_SETUP_ACTIVITY,
    action: str = ANDROIDCTL_SETUP_ACTION,
    string_extras: Mapping[str, str] | None = None,
    timeout_s: float = 10.0,
) -> AdbCommandResult:
    args = ["shell", "am", "start", "-n", component, "-a", action]
    sensitive_values: list[str] = []
    for key, value in (string_extras or {}).items():
        args.extend(["--es", key, value])
        sensitive_values.append(value)
    return run_adb(
        args,
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb start setup activity",
        failure_code="ADB_LAUNCH_FAILED",
        sensitive_values=sensitive_values,
    )


def forward_agent_port(
    *,
    serial: str,
    adb_path: str = "adb",
    local_port: int | None = None,
    remote_port: int = ANDROIDCTL_AGENT_PORT,
    timeout_s: float = 10.0,
) -> int:
    dynamic_local_port = local_port is None or local_port == 0
    if not dynamic_local_port:
        assert local_port is not None
        _validate_tcp_port(local_port, label="local port")
    _validate_tcp_port(remote_port, label="remote port")

    local_spec = "tcp:0" if dynamic_local_port else f"tcp:{local_port}"
    result = run_adb(
        ["forward", local_spec, f"tcp:{remote_port}"],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb forward",
        failure_code="ADB_FORWARD_FAILED",
    )
    if not dynamic_local_port:
        assert local_port is not None
        return local_port

    allocated_port = result.stdout.strip().splitlines()[0] if result.stdout else ""
    try:
        return int(allocated_port)
    except ValueError as exc:
        raise SetupAdbError(
            "ADB_FORWARD_FAILED",
            "adb forward did not report an allocated local port",
        ) from exc


def get_secure_setting(
    key: str,
    *,
    serial: str,
    adb_path: str = "adb",
    timeout_s: float = 10.0,
) -> str:
    result = run_adb(
        ["shell", "settings", "get", "secure", key],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb settings get secure",
        failure_code="ADB_SETTINGS_FAILED",
    )
    return result.stdout.strip()


def put_secure_setting(
    key: str,
    value: str,
    *,
    serial: str,
    adb_path: str = "adb",
    timeout_s: float = 10.0,
) -> AdbCommandResult:
    return run_adb(
        ["shell", "settings", "put", "secure", key, value],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb settings put secure",
        failure_code="ADB_SETTINGS_FAILED",
        sensitive_values=(value,),
    )


def get_package_dump(
    package: str = ANDROIDCTL_PACKAGE,
    *,
    serial: str,
    adb_path: str = "adb",
    timeout_s: float = 10.0,
) -> str:
    result = run_adb(
        ["shell", "dumpsys", "package", package],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb dumpsys package",
        failure_code="ADB_PACKAGE_QUERY_FAILED",
    )
    return result.stdout


def get_package_paths(
    package: str = ANDROIDCTL_PACKAGE,
    *,
    serial: str,
    adb_path: str = "adb",
    timeout_s: float = 10.0,
) -> tuple[str, ...]:
    result = run_adb(
        ["shell", "pm", "path", package],
        adb_path=adb_path,
        serial=serial,
        timeout_s=timeout_s,
        operation="adb pm path",
        failure_code="ADB_PACKAGE_QUERY_FAILED",
    )
    return parse_pm_path_output(result.stdout)


def parse_pm_path_output(output: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("package:"):
            paths.append(line.removeprefix("package:"))
        else:
            paths.append(line)
    return tuple(paths)


def pair_wireless_device(
    *,
    pair_endpoint: str,
    code: str,
    adb_path: str = "adb",
    timeout_s: float = 30.0,
) -> AdbCommandResult:
    endpoint = validate_wireless_endpoint(pair_endpoint, label="pair endpoint")
    normalized_code = code.strip()
    if not normalized_code:
        raise SetupAdbError(
            "ADB_PAIR_CODE_REQUIRED",
            "pairing code is required; open Android Wireless debugging to get it",
        )
    result: AdbCommandResult | None = None
    adb_error: SetupAdbError | None = None
    try:
        result = run_adb(
            ["pair", endpoint, normalized_code],
            adb_path=adb_path,
            timeout_s=timeout_s,
            operation="adb pair",
            failure_code="ADB_PAIR_FAILED",
            sensitive_values=(normalized_code,),
        )
    except SetupAdbError as error:
        adb_error = SetupAdbError(error.code, error.message)
    if adb_error is not None:
        raise adb_error
    assert result is not None
    if not parse_adb_pair_success(_combined_output_result(result)):
        raise SetupAdbError("ADB_PAIR_FAILED", "adb pair did not report success")
    return result


def connect_wireless_device(
    *,
    connect_endpoint: str,
    adb_path: str = "adb",
    timeout_s: float = 30.0,
) -> AdbCommandResult:
    endpoint = validate_wireless_endpoint(connect_endpoint, label="connect endpoint")
    result = run_adb(
        ["connect", endpoint],
        adb_path=adb_path,
        timeout_s=timeout_s,
        operation="adb connect",
        failure_code="ADB_CONNECT_FAILED",
    )
    if not parse_adb_connect_success(
        _combined_output_result(result),
        endpoint=endpoint,
    ):
        raise SetupAdbError("ADB_CONNECT_FAILED", "adb connect did not report success")
    return result


def validate_wireless_endpoint(endpoint: str, *, label: str) -> str:
    normalized = endpoint.strip()
    host, separator, port_text = normalized.rpartition(":")
    if not normalized or not separator or not host or not port_text:
        raise SetupAdbError(
            "ADB_INVALID_WIRELESS_ENDPOINT",
            f"{label} must be HOST:PORT",
        )
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SetupAdbError(
            "ADB_INVALID_WIRELESS_ENDPOINT",
            f"{label} port must be numeric",
        ) from exc
    try:
        _validate_tcp_port(port, label=f"{label} port")
    except SetupAdbError as exc:
        raise SetupAdbError(
            "ADB_INVALID_WIRELESS_ENDPOINT",
            f"{label} port must be between 1 and 65535",
        ) from exc
    return normalized


def parse_adb_pair_success(output: str) -> bool:
    return "successfully paired" in output.lower()


def parse_adb_connect_success(output: str, *, endpoint: str | None = None) -> bool:
    normalized = output.lower()
    success = "connected to " in normalized or "already connected to " in normalized
    if not success or endpoint is None:
        return success
    return endpoint.lower() in normalized


def redact_adb_output(
    text: str,
    *,
    sensitive_values: Iterable[str] = (),
) -> str:
    redacted = text
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "<redacted>")
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


def _run_adb_process(
    args: Sequence[str],
    *,
    adb_path: str,
    serial: str | None,
    timeout_s: float,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            build_adb_command(args, adb_path=adb_path, serial=serial),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise SetupAdbError(
            "ADB_NOT_FOUND",
            f"adb executable not found: {adb_path}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupAdbError("ADB_TIMEOUT", f"{operation} timed out") from exc


def _raise_for_nonzero(
    result: subprocess.CompletedProcess[str],
    *,
    code: str,
    operation: str,
    sensitive_values: Iterable[str],
    message_prefix: str | None = None,
) -> None:
    if result.returncode == 0:
        return

    output = _summarize_process_output(result, sensitive_values=sensitive_values)
    message = message_prefix or f"{operation} failed"
    if output:
        message = f"{message}: {output}"
    raise SetupAdbError(code, message)


def _summarize_process_output(
    result: subprocess.CompletedProcess[str],
    *,
    sensitive_values: Iterable[str],
) -> str:
    parts: list[str] = []
    stderr = _normalize_output(
        redact_adb_output(result.stderr, sensitive_values=sensitive_values)
    )
    stdout = _normalize_output(
        redact_adb_output(result.stdout, sensitive_values=sensitive_values)
    )
    if stderr:
        parts.append(f"stderr={stderr}")
    if stdout:
        parts.append(f"stdout={stdout}")
    return _clip_output("; ".join(parts))


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join((result.stdout, result.stderr))


def _combined_output_result(result: AdbCommandResult) -> str:
    return "\n".join((result.stdout, result.stderr))


def _sensitive_values(
    serial: str | None,
    values: Iterable[str],
) -> tuple[str, ...]:
    sensitive = [value for value in values if value]
    if serial:
        sensitive.append(serial)
    return tuple(sensitive)


def _normalize_output(output: str) -> str:
    return "\n".join(line.rstrip() for line in output.splitlines()).strip()


def _clip_output(output: str) -> str:
    if len(output) <= _MAX_ERROR_OUTPUT_CHARS:
        return output
    return f"{output[: _MAX_ERROR_OUTPUT_CHARS - 3]}..."


def _validate_tcp_port(port: int, *, label: str) -> None:
    if port < 1 or port > 65535:
        raise SetupAdbError("ADB_INVALID_PORT", f"{label} must be between 1 and 65535")


def _install_failure_message(code: str) -> str:
    if code == "ADB_INSTALL_SIGNATURE_MISMATCH":
        return (
            "adb install failed because an installed app appears to use a "
            "different signing key; setup will not uninstall or clear app data "
            "automatically"
        )
    if code == "ADB_INSTALL_DOWNGRADE":
        return (
            "adb install failed because the target APK is older than the "
            "installed app; setup will not downgrade or clear app data "
            "automatically"
        )
    return "adb install failed"
