from __future__ import annotations

from subprocess import CompletedProcess, TimeoutExpired
from typing import Any

import pytest

from androidctl.setup import adb


class FakeAdbRun:
    def __init__(self, *results: CompletedProcess[str]) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if not self.results:
            raise AssertionError("unexpected adb call")
        return self.results.pop(0)


def test_parse_adb_devices_output_keeps_states_and_details() -> None:
    devices = adb.parse_adb_devices_output(
        "\n".join(
            [
                "List of devices attached",
                "emulator-5554 device product:sdk model:Pixel",
                "abc123 unauthorized",
                "offline-1 offline transport_id:2",
                "",
            ]
        )
    )

    assert devices == [
        adb.AdbDevice(
            serial="emulator-5554",
            state="device",
            details=("product:sdk", "model:Pixel"),
        ),
        adb.AdbDevice(serial="abc123", state="unauthorized"),
        adb.AdbDevice(
            serial="offline-1",
            state="offline",
            details=("transport_id:2",),
        ),
    ]


def test_select_eligible_device_selects_only_authorized_device() -> None:
    selected = adb.select_eligible_device(
        [
            adb.AdbDevice(serial="offline-1", state="offline"),
            adb.AdbDevice(serial="device-1", state="device"),
        ]
    )

    assert selected.serial == "device-1"


def test_select_eligible_device_requires_serial_for_multiple_devices() -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.select_eligible_device(
            [
                adb.AdbDevice(serial="device-1", state="device"),
                adb.AdbDevice(serial="device-2", state="device"),
            ]
        )

    assert exc_info.value.code == "MULTIPLE_ADB_DEVICES"
    assert "pass --serial" in exc_info.value.message


def test_select_eligible_device_reports_no_authorized_device() -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.select_eligible_device(
            [
                adb.AdbDevice(serial="offline-1", state="offline"),
                adb.AdbDevice(serial="abc123", state="unauthorized"),
            ]
        )

    assert exc_info.value.code == "NO_ELIGIBLE_ADB_DEVICE"
    assert "no authorized ADB device" in exc_info.value.message


def test_select_eligible_device_selects_requested_serial() -> None:
    selected = adb.select_eligible_device(
        [
            adb.AdbDevice(serial="device-1", state="device"),
            adb.AdbDevice(serial="device-2", state="device"),
        ],
        serial="device-2",
    )

    assert selected.serial == "device-2"


def test_select_eligible_device_rejects_requested_unauthorized_device() -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.select_eligible_device(
            [adb.AdbDevice(serial="abc123", state="unauthorized")],
            serial="abc123",
        )

    assert exc_info.value.code == "NO_ELIGIBLE_ADB_DEVICE"
    assert "requested ADB device" in exc_info.value.message
    assert "abc123" not in exc_info.value.message


def test_select_eligible_device_rejects_missing_requested_serial() -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.select_eligible_device(
            [adb.AdbDevice(serial="device-1", state="device")],
            serial="missing",
        )

    assert exc_info.value.code == "NO_ELIGIBLE_ADB_DEVICE"
    assert "requested ADB device was not found" in exc_info.value.message
    assert "missing" not in exc_info.value.message


def test_select_eligible_device_rejects_requested_offline_device() -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.select_eligible_device(
            [
                adb.AdbDevice(serial="device-1", state="device"),
                adb.AdbDevice(serial="offline-1", state="offline"),
            ],
            serial="offline-1",
        )

    assert exc_info.value.code == "NO_ELIGIBLE_ADB_DEVICE"
    assert "offline" in exc_info.value.message
    assert "offline-1" not in exc_info.value.message


def test_list_adb_devices_maps_missing_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        raise FileNotFoundError

    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.list_adb_devices()

    assert exc_info.value.code == "ADB_NOT_FOUND"


def test_list_adb_devices_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        raise TimeoutExpired(cmd=["adb", "devices"], timeout=5)

    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.list_adb_devices()

    assert exc_info.value.code == "ADB_TIMEOUT"


def test_list_adb_devices_maps_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        return CompletedProcess(
            args=["adb", "devices"],
            returncode=1,
            stdout="",
            stderr="daemon not running\n",
        )

    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.list_adb_devices()

    assert exc_info.value.code == "ADB_COMMAND_FAILED"
    assert "daemon not running" in exc_info.value.message


def test_build_adb_command_inserts_serial_before_args() -> None:
    assert adb.build_adb_command(
        ["shell", "echo", "ok"],
        adb_path="/opt/android/adb",
        serial="device-1",
    ) == ["/opt/android/adb", "-s", "device-1", "shell", "echo", "ok"]


def test_run_adb_passes_timeout_and_redacts_failure_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=1,
            stdout="token=host-secret\n",
            stderr="device device-1 failed with Bearer other-secret\n",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.run_adb(
            ["shell", "echo", "ok"],
            serial="device-1",
            timeout_s=3.5,
            operation="probe",
            sensitive_values=("host-secret",),
        )

    assert exc_info.value.code == "ADB_COMMAND_FAILED"
    assert "probe failed" in exc_info.value.message
    assert "device-1" not in exc_info.value.message
    assert "host-secret" not in exc_info.value.message
    assert "other-secret" not in exc_info.value.message
    assert "<redacted>" in exc_info.value.message
    assert fake_run.calls == [
        (
            ["adb", "-s", "device-1", "shell", "echo", "ok"],
            {
                "capture_output": True,
                "text": True,
                "check": False,
                "timeout": 3.5,
            },
        )
    ]


def test_run_adb_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        raise TimeoutExpired(cmd=["adb", "shell"], timeout=9)

    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.run_adb(["shell", "echo", "ok"], timeout_s=9, operation="probe")

    assert exc_info.value.code == "ADB_TIMEOUT"
    assert "probe timed out" in exc_info.value.message


def test_install_apk_uses_reinstall_without_destructive_flags(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk_path = tmp_path / "agent.apk"
    apk_path.write_bytes(b"apk")
    fake_run = FakeAdbRun(
        CompletedProcess(args=[], returncode=0, stdout="Success\n", stderr="")
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    result = adb.install_apk(apk_path, serial="device-1", timeout_s=123)

    assert result.stdout == "Success\n"
    command = fake_run.calls[0][0]
    assert command == ["adb", "-s", "device-1", "install", "-r", str(apk_path)]
    assert "-d" not in command
    assert "uninstall" not in command
    assert "clear" not in command
    assert fake_run.calls[0][1]["timeout"] == 123


def test_install_apk_requires_existing_file(tmp_path) -> None:
    missing_path = tmp_path / "missing.apk"

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.install_apk(missing_path, serial="device-1")

    assert exc_info.value.code == "APK_NOT_FOUND"
    assert str(missing_path) not in exc_info.value.message


def test_install_apk_classifies_signature_mismatch_and_keeps_data(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk_path = tmp_path / "agent.apk"
    apk_path.write_bytes(b"apk")
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=(
                "device-1 Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: "
                f"Package signatures differ for {apk_path}]\n"
            ),
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.install_apk(apk_path, serial="device-1")

    assert exc_info.value.code == "ADB_INSTALL_SIGNATURE_MISMATCH"
    assert "will not uninstall or clear app data automatically" in (
        exc_info.value.message
    )
    assert "device-1" not in exc_info.value.message
    assert str(apk_path) not in exc_info.value.message


def test_install_apk_classifies_downgrade(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk_path = tmp_path / "agent.apk"
    apk_path.write_bytes(b"apk")
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=1,
            stdout="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]\n",
            stderr="",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.install_apk(apk_path, serial="device-1")

    assert exc_info.value.code == "ADB_INSTALL_DOWNGRADE"
    assert "will not downgrade or clear app data automatically" in (
        exc_info.value.message
    )


def test_force_stop_app_uses_am_force_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run = FakeAdbRun(CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    adb.force_stop_app(serial="device-1", timeout_s=8)

    assert fake_run.calls[0][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "am",
        "force-stop",
        adb.ANDROIDCTL_PACKAGE,
    ]
    assert fake_run.calls[0][1]["timeout"] == 8


def test_start_setup_activity_uses_component_and_redacts_extra_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="bad setup token host-token\n",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.start_setup_activity(
            serial="device-1",
            string_extras={"setupToken": "host-token"},
        )

    assert fake_run.calls[0][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "am",
        "start",
        "-n",
        adb.ANDROIDCTL_SETUP_ACTIVITY,
        "-a",
        adb.ANDROIDCTL_SETUP_ACTION,
        "--es",
        "setupToken",
        "host-token",
    ]
    assert exc_info.value.code == "ADB_LAUNCH_FAILED"
    assert "host-token" not in exc_info.value.message


def test_forward_agent_port_parses_dynamic_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(args=[], returncode=0, stdout="45678\n", stderr="")
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    port = adb.forward_agent_port(serial="device-1")

    assert port == 45678
    assert fake_run.calls[0][0] == [
        "adb",
        "-s",
        "device-1",
        "forward",
        "tcp:0",
        f"tcp:{adb.ANDROIDCTL_AGENT_PORT}",
    ]


def test_forward_agent_port_returns_requested_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    assert adb.forward_agent_port(serial="device-1", local_port=18181) == 18181
    assert fake_run.calls[0][0][-2:] == ["tcp:18181", "tcp:17171"]


def test_forward_agent_port_treats_zero_as_dynamic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(args=[], returncode=0, stdout="45678\n", stderr="")
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    assert adb.forward_agent_port(serial="device-1", local_port=0) == 45678
    assert fake_run.calls[0][0][-2:] == ["tcp:0", "tcp:17171"]


@pytest.mark.parametrize(
    ("local_port", "remote_port"),
    [
        (-1, adb.ANDROIDCTL_AGENT_PORT),
        (65536, adb.ANDROIDCTL_AGENT_PORT),
        (None, 0),
        (None, 65536),
    ],
)
def test_forward_agent_port_rejects_invalid_ports(
    local_port: int | None,
    remote_port: int,
) -> None:
    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.forward_agent_port(
            serial="device-1",
            local_port=local_port,
            remote_port=remote_port,
        )

    assert exc_info.value.code == "ADB_INVALID_PORT"


def test_forward_agent_port_rejects_missing_dynamic_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.forward_agent_port(serial="device-1")

    assert exc_info.value.code == "ADB_FORWARD_FAILED"


def test_secure_settings_get_and_put(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(args=[], returncode=0, stdout="enabled\n", stderr=""),
        CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    assert adb.get_secure_setting("enabled_accessibility_services", serial="device-1")
    adb.put_secure_setting(
        "enabled_accessibility_services",
        adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
        serial="device-1",
    )

    assert fake_run.calls[0][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "settings",
        "get",
        "secure",
        "enabled_accessibility_services",
    ]
    assert fake_run.calls[1][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "settings",
        "put",
        "secure",
        "enabled_accessibility_services",
        adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
    ]


def test_package_queries_and_pm_path_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=0,
            stdout="Package [androidctl]\n",
            stderr="",
        ),
        CompletedProcess(
            args=[],
            returncode=0,
            stdout="package:/data/app/base.apk\npackage:/data/app/split.apk\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    assert adb.get_package_dump(serial="device-1") == "Package [androidctl]\n"
    assert adb.get_package_paths(serial="device-1") == (
        "/data/app/base.apk",
        "/data/app/split.apk",
    )

    assert fake_run.calls[0][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "dumpsys",
        "package",
        adb.ANDROIDCTL_PACKAGE,
    ]
    assert fake_run.calls[1][0] == [
        "adb",
        "-s",
        "device-1",
        "shell",
        "pm",
        "path",
        adb.ANDROIDCTL_PACKAGE,
    ]


def test_parse_pm_path_output_accepts_package_prefix_and_plain_paths() -> None:
    assert adb.parse_pm_path_output(
        "\n".join(
            [
                "package:/data/app/base.apk",
                "/data/app/plain.apk",
                "",
            ]
        )
    ) == ("/data/app/base.apk", "/data/app/plain.apk")


def test_wireless_endpoint_validation() -> None:
    assert (
        adb.validate_wireless_endpoint(
            " 192.168.1.20:5555 ",
            label="connect endpoint",
        )
        == "192.168.1.20:5555"
    )

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.validate_wireless_endpoint("192.168.1.20", label="connect endpoint")

    assert exc_info.value.code == "ADB_INVALID_WIRELESS_ENDPOINT"

    with pytest.raises(adb.SetupAdbError) as port_exc_info:
        adb.validate_wireless_endpoint("192.168.1.20:70000", label="connect endpoint")

    assert port_exc_info.value.code == "ADB_INVALID_WIRELESS_ENDPOINT"
    assert "between 1 and 65535" in port_exc_info.value.message


def test_pair_wireless_device_uses_adb_pair_and_redacts_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="failed pairing with code 123456\n",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.pair_wireless_device(pair_endpoint="192.168.1.20:37199", code="123456")

    assert fake_run.calls[0][0] == [
        "adb",
        "pair",
        "192.168.1.20:37199",
        "123456",
    ]
    assert exc_info.value.code == "ADB_PAIR_FAILED"
    assert "123456" not in exc_info.value.message
    assert "<redacted>" in exc_info.value.message


def test_pair_wireless_device_requires_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(args=[], returncode=0, stdout="Pairing started\n", stderr="")
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.pair_wireless_device(pair_endpoint="192.168.1.20:37199", code="123456")

    assert exc_info.value.code == "ADB_PAIR_FAILED"


def test_pair_wireless_device_strips_timeout_command_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        raise TimeoutExpired(
            cmd=["adb", "pair", "192.168.1.20:37199", "123456"],
            timeout=30,
        )

    monkeypatch.setattr(adb.subprocess, "run", timeout_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.pair_wireless_device(pair_endpoint="192.168.1.20:37199", code="123456")

    assert exc_info.value.code == "ADB_TIMEOUT"
    assert "123456" not in exc_info.value.message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_connect_wireless_device_accepts_connected_or_already_connected_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=0,
            stdout="already connected to 192.168.1.20:5555\n",
            stderr="",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    adb.connect_wireless_device(connect_endpoint="192.168.1.20:5555")

    assert fake_run.calls[0][0] == ["adb", "connect", "192.168.1.20:5555"]


def test_connect_wireless_device_requires_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = FakeAdbRun(
        CompletedProcess(
            args=[],
            returncode=0,
            stdout="failed to connect to 192.168.1.20:5555\n",
            stderr="",
        )
    )
    monkeypatch.setattr(adb.subprocess, "run", fake_run)

    with pytest.raises(adb.SetupAdbError) as exc_info:
        adb.connect_wireless_device(connect_endpoint="192.168.1.20:5555")

    assert exc_info.value.code == "ADB_CONNECT_FAILED"
