import json
import re
import sys

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call
from androidctl.protocol import (
    completed,
)


def run_main(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["androidctl", *args])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        *(
            ["session", "open", "--idle", value]
            for value in ["0", "999ms", "1500ms", "86401s"]
        ),
    ],
)
def test_duration_arguments_keep_leaf_command_without_connection(
    monkeypatch, capsys, args
):
    run_main(monkeypatch, ["--json", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == (" ".join(args[:2]) if args[0] == "session" else args[0])
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"


@pytest.mark.parametrize(
    ("value", "seconds"), [(None, 600), ("1s", 1), ("1000ms", 1), ("24h", 86400)]
)
def test_idle_duration_converts_once(monkeypatch, value, seconds):
    calls = []
    monkeypatch.setattr(
        cli, "device_call", lambda ctx, method, args: calls.append(args)
    )
    result = CliRunner().invoke(
        cli.app, ["session", "open", *(["--idle", value] if value else [])]
    )
    assert result.exit_code == 0, result.output
    assert calls == [{"idleSeconds": seconds}]


@pytest.mark.parametrize(
    ("command", "defaults", "options"),
    [
        (["session", "open"], ["10m"], ["--idle"]),
    ],
)
def test_duration_help_has_units_and_current_option_names(command, defaults, options):
    result = CliRunner().invoke(cli.app, [*command, "--help"], terminal_width=160)
    assert result.exit_code == 0, result.output
    for value in [*defaults, *options]:
        assert value in result.output


@pytest.fixture(autouse=True)
def forbid_local_connection(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached the local connection")

    monkeypatch.setattr(local_call, "connect_local", forbidden)


@pytest.mark.parametrize(
    ("args", "command"),
    [
        (["--json", "status", "--bad"], "status"),
        (["--json", "--bad", "status"], "arguments"),
        (["--json", "missing"], "arguments"),
        (["--json", "session", "missing"], "session"),
    ],
)
def test_argument_errors_are_json_without_connection(
    monkeypatch, capsys, args, command
):
    run_main(monkeypatch, args)
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "not sent"
    assert output["error"]["code"] == "arguments"
    assert output["command"] == command


@pytest.mark.parametrize(
    "args",
    [
        ["status", "--", "--json"],
        ["--device", "--json", "status", "--bad"],
        ["-s--json", "status", "--bad"],
        ["--session=--json", "status", "--bad"],
    ],
)
def test_format_probe_respects_values_and_double_dash(args):
    assert not cli.error_json(args)


def test_main_does_not_hide_program_value_error(monkeypatch):
    def application(**kwargs):
        raise ValueError("program failure")

    monkeypatch.setattr(cli, "app", application)
    with pytest.raises(ValueError, match="program failure"):
        cli.main()


def test_bare_session_shows_all_operations_without_connection():
    result = CliRunner().invoke(cli.app, ["session"])
    assert result.exit_code == 2
    for command in ("open", "close", "takeover"):
        assert re.search(rf"\b{command}\b", result.output), result.output


@pytest.mark.parametrize("command", ["open", "close", "takeover"])
def test_session_subcommand_selects_one_operation(monkeypatch, command):
    calls = []

    async def call(self, name, args, **kwargs):
        calls.append((name, args))
        return completed("session " + command, {"name": "task"})

    monkeypatch.setattr(cli.LocalCall, "call", call)
    result = CliRunner().invoke(
        cli.app, ["--json", "--device", "phone", "-s", "task", "session", command]
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "session." + command,
            {"idleSeconds": 600} if command == "open" else {},
        )
    ]
    assert json.loads(result.output)["command"] == "session " + command
    assert json.loads(result.output)["data"]["name"] == "task"


def test_takeover_requires_its_subcommand(monkeypatch, capsys):
    run_main(monkeypatch, ["--json", "session", "--takeover"])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "session"
    assert value["error"]["code"] == "arguments"
    assert "No such option" in value["reason"]


def test_pair_syntax_is_checked_before_daemon_and_preserves_leading_zeros(
    monkeypatch, capsys
):
    invoked = []

    async def request(self, method, args, **kwargs):
        assert method == "pair"
        invoked.append(args)
        return completed("pair")

    monkeypatch.setattr(cli.LocalCall, "request", request)
    runner = CliRunner()
    for code in ("42", "0000042", "00-042", "１２３４５６", "١٢٣٤٥٦", " 000042"):
        run_main(monkeypatch, ["--json", "pair", "host:18843", code, "--name", "phone"])
        result = json.loads(capsys.readouterr().out)
        assert result["command"] == "pair"
        assert result["result"] == "not sent"
    for address in (
        "host",
        "host:0",
        "user@host:443",
        "host:443/ipc",
        "host:443?x=1",
        "host:99999",
    ):
        run_main(monkeypatch, ["--json", "pair", address, "000042", "--name", "phone"])
        result = json.loads(capsys.readouterr().out)
        assert result["command"] == "pair"
        assert result["result"] == "not sent"
    assert invoked == []
    for address in (
        "127.0.0.1:443",
        "192.168.1.2:443",
        "8.8.8.8:443",
        "public.example:443",
        "[2001:4860::1]:443",
    ):
        result = runner.invoke(cli.app, ["pair", address, "000042", "--name", "phone"])
        assert result.exit_code == 0
        assert invoked[-1] == {
            "endpoint": f"wss://{address}/androidctl",
            "code": "000042",
            "name": "phone",
        }


@pytest.mark.parametrize(
    "address", ["phone:18843", "127.0.0.1:1", "[::1]:65535", "[fe80::1%eth0]:18843"]
)
def test_device_address_syntax_is_shared(monkeypatch, address):
    calls = []

    async def request(self, method, args, **kwargs):
        calls.append((method, args))
        return completed(method)

    monkeypatch.setattr(cli.LocalCall, "request", request)
    args = ["pair", address, "012345", "--name", "phone"]
    result = CliRunner().invoke(cli.app, ["--device", "phone", *args])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    method, parameters = calls[0]
    assert method == "pair"
    assert parameters["endpoint"] == f"wss://{address}/androidctl"


@pytest.mark.parametrize(
    "address",
    [
        "phone",
        "phone:0",
        "phone:65536",
        "phone:bad",
        "[::1:18843",
        "wss://phone:18843/androidctl",
    ],
)
def test_bad_device_address_rejects_before_connection(monkeypatch, capsys, address):
    args = ["pair", address, "012345", "--name", "phone"]
    run_main(monkeypatch, ["--json", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["command"] == "pair"
    assert value["error"]["code"] == "endpoint"
