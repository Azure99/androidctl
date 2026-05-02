from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl.app import app
from androidctl.commands import setup as setup_command
from androidctl.setup import adb as setup_adb


@contextmanager
def _apk_context(path: Path) -> Iterator[Path]:
    yield path


def _record_accessibility_success(
    calls: list[str],
) -> Callable[
    ...,
    setup_command.setup_accessibility.AccessibilityEnableResult,
]:
    def record(
        *,
        serial: str,
    ) -> setup_command.setup_accessibility.AccessibilityEnableResult:
        calls.append(serial)
        return setup_command.setup_accessibility.AccessibilityEnableResult(
            changed_service_list=True,
            enabled_services=setup_command.setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
        )

    return record


def _record_verify_success(
    calls: list[tuple[str, str, Path | None]],
) -> Callable[..., setup_command.setup_verify.SetupVerificationResult]:
    def record(
        *,
        serial: str,
        token: str,
        workspace_root: Path | None,
    ) -> setup_command.setup_verify.SetupVerificationResult:
        calls.append((serial, token, workspace_root))
        return setup_command.setup_verify.SetupVerificationResult(
            command="connect",
            envelope="bootstrap",
        )

    return record


@pytest.fixture(autouse=True)
def _stub_force_stop_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "force_stop_app",
        lambda *, serial: None,
    )


def test_setup_dry_run_prints_human_progress_without_adb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_list_adb_devices() -> list[setup_adb.AdbDevice]:
        raise AssertionError("dry-run must not call adb")

    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        fail_list_adb_devices,
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--dry-run"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "<result" not in result.stderr
    assert "mode: dry-run" in result.stderr
    assert "androidctl-agent-0.1.0-release.apk" in result.stderr
    assert "status: dry-run complete" in result.stderr


def test_setup_dry_run_does_not_echo_override_apk_path(
    tmp_path: Path,
) -> None:
    apk_path = tmp_path / "agent.apk"

    result = CliRunner().invoke(
        app,
        ["setup", "--adb", "--dry-run", "--apk", str(apk_path)],
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "override APK path" in result.stderr
    assert str(tmp_path) not in result.stderr


def test_setup_requires_adb() -> None:
    result = CliRunner().invoke(app, ["setup", "--dry-run"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "SETUP_REQUIRES_ADB" in result.stderr
    assert "<errorResult" not in result.stderr


def test_setup_reports_no_eligible_adb_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [],
    )

    result = CliRunner().invoke(app, ["setup", "--adb"])

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "ADB/NO_ELIGIBLE_ADB_DEVICE" in result.stderr
    assert "no authorized ADB device" in result.stderr


def test_setup_requires_serial_for_multiple_adb_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [
            setup_adb.AdbDevice(serial="device-1", state="device"),
            setup_adb.AdbDevice(serial="device-2", state="device"),
        ],
    )

    result = CliRunner().invoke(app, ["setup", "--adb"])

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "ADB/MULTIPLE_ADB_DEVICES" in result.stderr
    assert "pass --serial" in result.stderr


def test_setup_selects_serial_installs_provisions_and_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apk_path = tmp_path / "agent.apk"
    apk_path.write_bytes(b"apk")
    installed: list[tuple[Path, str]] = []
    force_stops: list[str] = []
    setup_starts: list[tuple[str, dict[str, str] | None]] = []
    accessibility_calls: list[str] = []
    verify_calls: list[tuple[str, str, Path | None]] = []
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [
            setup_adb.AdbDevice(serial="device-1", state="device"),
            setup_adb.AdbDevice(serial="device-2", state="device"),
        ],
    )
    monkeypatch.setattr(
        setup_command,
        "packaged_agent_apk_path",
        lambda version: _apk_context(apk_path),
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "install_apk",
        lambda path, *, serial: installed.append((path, serial)),
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "force_stop_app",
        lambda *, serial: force_stops.append(serial),
    )

    def fake_start_setup_activity(
        *,
        serial: str,
        string_extras: dict[str, str] | None = None,
    ) -> None:
        setup_starts.append((serial, string_extras))

    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        fake_start_setup_activity,
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        _record_accessibility_success(accessibility_calls),
    )
    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        _record_verify_success(verify_calls),
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--serial", "device-2"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "ADB: selected requested authorized device" in result.stderr
    assert "install: APK installed" in result.stderr
    assert "launch: existing app process stopped" in result.stderr
    assert "token: provisioned host-generated device token" in result.stderr
    assert "accessibility: ADB settings write confirmed" in result.stderr
    assert "verify: daemon connect/readiness check succeeded" in result.stderr
    assert "status: setup complete" in result.stderr
    assert "secret-host-token" not in result.stderr
    assert installed == [(apk_path, "device-2")]
    assert force_stops == ["device-2"]
    assert setup_starts == [
        (
            "device-2",
            {
                setup_command.setup_pairing.SETUP_DEVICE_TOKEN_EXTRA: (
                    "secret-host-token"
                ),
            },
        )
    ]
    assert accessibility_calls == ["device-2"]
    assert verify_calls == [("device-2", "secret-host-token", None)]


def test_setup_skip_install_provisions_token_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_stops: list[str] = []
    setup_starts: list[tuple[str, dict[str, str] | None]] = []
    accessibility_calls: list[str] = []
    verify_calls: list[tuple[str, str, Path | None]] = []
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "install_apk",
        lambda *args, **kwargs: pytest.fail("skip-install must not install APK"),
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "force_stop_app",
        lambda *, serial: force_stops.append(serial),
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: setup_starts.append(
            (serial, string_extras)
        ),
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        _record_accessibility_success(accessibility_calls),
    )
    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        _record_verify_success(verify_calls),
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--skip-install"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "install: skipped by --skip-install" in result.stderr
    assert "launch: existing app process stopped" in result.stderr
    assert "launch: setup activity started" in result.stderr
    assert "token: provisioned host-generated device token" in result.stderr
    assert "accessibility: ADB settings write confirmed" in result.stderr
    assert "verify: daemon connect/readiness check succeeded" in result.stderr
    assert "secret-host-token" not in result.stderr
    assert force_stops == ["device-1"]
    assert setup_starts == [
        (
            "device-1",
            {
                setup_command.setup_pairing.SETUP_DEVICE_TOKEN_EXTRA: (
                    "secret-host-token"
                ),
            },
        )
    ]
    assert accessibility_calls == ["device-1"]
    assert verify_calls == [("device-1", "secret-host-token", None)]


@pytest.mark.parametrize("workspace_option_position", ["top_level", "command"])
def test_setup_passes_workspace_root_to_connect_pipeline(
    workspace_option_position: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    setup_starts: list[tuple[str, dict[str, str] | None]] = []
    accessibility_calls: list[str] = []
    verify_calls: list[tuple[str, str, Path | None]] = []
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: setup_starts.append(
            (serial, string_extras)
        ),
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        _record_accessibility_success(accessibility_calls),
    )
    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        _record_verify_success(verify_calls),
    )

    if workspace_option_position == "top_level":
        args = [
            "--workspace-root",
            str(workspace_root),
            "setup",
            "--adb",
            "--skip-install",
        ]
    else:
        args = [
            "setup",
            "--adb",
            "--skip-install",
            "--workspace-root",
            str(workspace_root),
        ]

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "status: setup complete" in result.stderr
    assert "secret-host-token" not in result.stderr
    assert setup_starts == [
        (
            "device-1",
            {
                setup_command.setup_pairing.SETUP_DEVICE_TOKEN_EXTRA: (
                    "secret-host-token"
                ),
            },
        )
    ]
    assert accessibility_calls == ["device-1"]
    assert verify_calls == [("device-1", "secret-host-token", workspace_root)]


def test_setup_accessibility_failure_enters_manual_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "install_apk",
        lambda *args, **kwargs: pytest.fail("skip-install must not install APK"),
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: None,
    )

    def fail_enable_accessibility(
        *,
        serial: str,
    ) -> setup_command.setup_accessibility.AccessibilityEnableResult:
        assert serial == "device-1"
        raise setup_command.setup_accessibility.SetupAccessibilityError(
            "ACCESSIBILITY_ENABLE_NOT_CONFIRMED",
            "readback did not match",
        )

    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        fail_enable_accessibility,
    )
    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        _record_verify_success([]),
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--skip-install"])

    assert result.exit_code == 0
    assert "accessibility: ADB enable not confirmed" in result.stderr
    assert "accessibility: manual fallback required: readback did not match" in (
        result.stderr
    )
    assert "enable AndroidCtl Accessibility" in result.stderr
    assert "verify: daemon connect/readiness check succeeded" in result.stderr


def test_setup_manual_accessibility_skips_adb_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: None,
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        lambda *, serial: pytest.fail("manual path must not write ADB settings"),
    )
    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        _record_verify_success([]),
    )

    result = CliRunner().invoke(
        app,
        ["setup", "--adb", "--skip-install", "--manual-accessibility"],
    )

    assert result.exit_code == 0
    assert "accessibility: manual fallback required: manual enablement requested" in (
        result.stderr
    )
    assert "enable AndroidCtl Accessibility" in result.stderr
    assert "verify: daemon connect/readiness check succeeded" in result.stderr


def test_setup_readiness_failure_reports_layer_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: None,
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        _record_accessibility_success([]),
    )

    def fail_verify(
        *,
        serial: str,
        token: str,
        workspace_root: Path | None,
    ) -> setup_command.setup_verify.SetupVerificationResult:
        del serial, token, workspace_root
        raise setup_command.setup_verify.SetupVerificationError(
            code="ACCESSIBILITY_NOT_READY",
            layer="accessibility",
            message="accessibility runtime is not ready",
        )

    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        fail_verify,
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--skip-install"])

    assert result.exit_code == 1
    assert "accessibility/ACCESSIBILITY_NOT_READY" in result.stderr
    assert "accessibility runtime is not ready" in result.stderr
    assert "secret-host-token" not in result.stderr


def test_setup_workspace_busy_readiness_failure_keeps_daemon_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup_command.setup_adb,
        "list_adb_devices",
        lambda: [setup_adb.AdbDevice(serial="device-1", state="device")],
    )
    monkeypatch.setattr(
        setup_command.setup_pairing,
        "generate_host_token",
        lambda: "secret-host-token",
    )
    monkeypatch.setattr(
        setup_command.setup_adb,
        "start_setup_activity",
        lambda *, serial, string_extras=None: None,
    )
    monkeypatch.setattr(
        setup_command.setup_accessibility,
        "enable_agent_accessibility",
        _record_accessibility_success([]),
    )

    def fail_verify(
        *,
        serial: str,
        token: str,
        workspace_root: Path | None,
    ) -> setup_command.setup_verify.SetupVerificationResult:
        del serial, token, workspace_root
        raise setup_command.setup_verify.SetupVerificationError(
            code="WORKSPACE_BUSY",
            layer="daemon",
            message="workspace is controlled by another owner",
        )

    monkeypatch.setattr(
        setup_command.setup_verify,
        "verify_setup_readiness",
        fail_verify,
    )

    result = CliRunner().invoke(app, ["setup", "--adb", "--skip-install"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "daemon/WORKSPACE_BUSY" in result.stderr
    assert "workspace is controlled by another owner" in result.stderr
    assert "secret-host-token" not in result.stderr
