from __future__ import annotations

import pytest
from typer.testing import CliRunner

from androidctl.app import app
from androidctl.commands import adb_wireless
from androidctl.setup import adb as setup_adb


def test_adb_pair_requires_pairing_code() -> None:
    result = CliRunner().invoke(app, ["adb-pair", "--pair", "192.168.1.20:37199"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "ADB_PAIR_CODE_REQUIRED" in result.stderr
    assert "Wireless debugging" in result.stderr


def test_adb_pair_invokes_adb_without_echoing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        adb_wireless.setup_adb,
        "pair_wireless_device",
        lambda *, pair_endpoint, code: calls.append((pair_endpoint, code)),
    )

    result = CliRunner().invoke(
        app,
        ["adb-pair", "--pair", "192.168.1.20:37199", "--code", "123456"],
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "wireless ADB: paired device" in result.stderr
    assert "123456" not in result.stderr
    assert calls == [("192.168.1.20:37199", "123456")]


def test_adb_pair_maps_adb_failure_without_echoing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pair(*, pair_endpoint: str, code: str) -> None:
        del pair_endpoint, code
        raise setup_adb.SetupAdbError("ADB_PAIR_FAILED", "pair failed: <redacted>")

    monkeypatch.setattr(adb_wireless.setup_adb, "pair_wireless_device", fail_pair)

    result = CliRunner().invoke(
        app,
        ["adb-pair", "--pair", "192.168.1.20:37199", "--code", "123456"],
    )

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "ADB_PAIR_FAILED" in result.stderr
    assert "123456" not in result.stderr


def test_adb_connect_invokes_adb_and_guides_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        adb_wireless.setup_adb,
        "connect_wireless_device",
        lambda *, connect_endpoint: calls.append(connect_endpoint),
    )

    result = CliRunner().invoke(app, ["adb-connect", "192.168.1.20:5555"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "wireless ADB: connected device" in result.stderr
    assert "--serial 192.168.1.20:5555" in result.stderr
    assert calls == ["192.168.1.20:5555"]


@pytest.mark.parametrize("endpoint", ["192.168.1.20", "192.168.1.20:70000"])
def test_adb_connect_rejects_invalid_endpoint(endpoint: str) -> None:
    result = CliRunner().invoke(app, ["adb-connect", endpoint])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "ADB_INVALID_WIRELESS_ENDPOINT" in result.stderr


def test_wireless_helpers_accept_workspace_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_calls: list[tuple[str, str]] = []
    connect_calls: list[str] = []
    monkeypatch.setattr(
        adb_wireless.setup_adb,
        "pair_wireless_device",
        lambda *, pair_endpoint, code: pair_calls.append((pair_endpoint, code)),
    )
    monkeypatch.setattr(
        adb_wireless.setup_adb,
        "connect_wireless_device",
        lambda *, connect_endpoint: connect_calls.append(connect_endpoint),
    )

    pair_result = CliRunner().invoke(
        app,
        [
            "adb-pair",
            "--pair",
            "192.168.1.20:37199",
            "--code",
            "123456",
            "--workspace-root",
            str(tmp_path),
        ],
    )
    connect_result = CliRunner().invoke(
        app,
        ["adb-connect", "192.168.1.20:5555", "--workspace-root", str(tmp_path)],
    )

    assert pair_result.exit_code == 0
    assert connect_result.exit_code == 0
    assert pair_calls == [("192.168.1.20:37199", "123456")]
    assert connect_calls == ["192.168.1.20:5555"]


def test_wireless_helpers_mark_workspace_root_as_ignored() -> None:
    runner = CliRunner()

    pair_help = runner.invoke(app, ["adb-pair", "--help"])
    connect_help = runner.invoke(app, ["adb-connect", "--help"])

    assert pair_help.exit_code == 0
    assert connect_help.exit_code == 0
    assert "ignored" in pair_help.stdout
    assert "wireless ADB helpers" in pair_help.stdout
    assert "ignored" in connect_help.stdout
    assert "wireless ADB helpers" in connect_help.stdout
