import json
import os
import re
import sys
from pathlib import Path

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
            ["snapshot", "--timeout", value]
            for value in ["5", "00", "0", "60001ms", "1h", "1.5s", "-1s", "1d"]
        ),
        ["shell", "--run", "0", "--", "true"],
        ["shell", "--run", "600001ms", "--", "true"],
        ["job", "get", "j1", "--wait", "60001ms"],
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
    assert value["command"] == (
        " ".join(args[:2]) if args[0] in ("session", "job") else args[0]
    )
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
        (["screenshot"], ["15s"], ["--timeout"]),
        (["snapshot"], ["5s"], ["--timeout"]),
        (["session", "open"], ["10m"], ["--idle"]),
        (["push"], ["10m", "10s"], ["--run", "--wait"]),
        (["shell"], ["10s", "10m"], ["--run", "--wait"]),
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


@pytest.mark.parametrize("root_kind", ["missing", "file"])
@pytest.mark.parametrize(
    ("command", "leaf"),
    [
        (["snapshot"], "snapshot"),
    ],
)
def test_invalid_workspace_keeps_leaf_command_and_absolute_error(
    tmp_path, monkeypatch, capsys, root_kind, command, leaf
):
    monkeypatch.chdir(tmp_path)
    if root_kind == "file":
        (tmp_path / "workspace").write_text("occupied")
    run_main(monkeypatch, ["--json", "--workspace-root", "workspace", *command])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == leaf
    assert value["error"]["code"] == "workspace"
    assert str(tmp_path / "workspace") in value["reason"]
    assert not (tmp_path / "workspace/.androidctl").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "certificate",
        "script",
        "job-parent",
    ],
)
def test_relative_input_and_output_errors_report_absolute_paths(
    tmp_path, monkeypatch, capsys, kind
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    (tmp_path / "directory").mkdir()
    command, expected = {
        "certificate": (
            ["device", "trust", "phone", "missing.pem"],
            tmp_path / "missing.pem",
        ),
        "script": (["shell", "--script-file", "missing.sh"], tmp_path / "missing.sh"),
        "job-parent": (
            ["job", "output", "j1", "--out", "missing/out.bin"],
            tmp_path / "missing",
        ),
    }[kind]
    run_main(monkeypatch, ["--json", "--workspace-root", "workspace", *command])
    value = json.loads(capsys.readouterr().out)
    assert str(expected) in value["reason"]
    assert value["result"] == "not sent"


@pytest.mark.parametrize(
    "kind",
    [
        "workspace",
        "trust",
    ],
)
def test_path_stat_permission_error_keeps_structured_absolute_result(
    tmp_path, monkeypatch, capsys, kind
):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "blocked"
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == target:
            raise PermissionError(13, "Permission denied", str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    args, command, code = {
        "workspace": (
            ["--workspace-root", "blocked", "snapshot"],
            "snapshot",
            "workspace",
        ),
        "trust": (
            ["device", "trust", "phone", "blocked"],
            "device trust",
            "certificate",
        ),
    }[kind]
    run_main(monkeypatch, ["--json", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == command
    assert value["result"] == "not sent"
    assert value["error"]["code"] == code
    assert str(target) in value["reason"]
    assert "Permission denied" in value["reason"]


def test_unreadable_relative_script_reaches_absolute_io_boundary(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "script.sh"
    target.write_text("echo test")
    original_access = os.access
    original_open = Path.open

    def denied_access(path, mode, *args, **kwargs):
        if Path(path).absolute() == target:
            return False
        return original_access(path, mode, *args, **kwargs)

    def denied_open(path, *args, **kwargs):
        if path == target:
            raise PermissionError(13, "Permission denied", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "access", denied_access)
    monkeypatch.setattr(Path, "open", denied_open)
    run_main(monkeypatch, ["--json", "shell", "--script-file", "script.sh"])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "shell"
    assert value["error"]["code"] == "scriptFile"
    assert str(target) in value["reason"]
    assert "Permission denied" in value["reason"]


def test_certificate_read_error_preserves_device_trust_command(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "certificate.pem"
    path.write_bytes(b"\xff")
    run_main(monkeypatch, ["--json", "device", "trust", "phone", str(path)])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "device trust"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "certificate"


@pytest.mark.parametrize("both", [False, True])
def test_shell_requires_exactly_one_source_before_connection(
    tmp_path, monkeypatch, capsys, both
):
    path = tmp_path / "script.sh"
    path.write_bytes(b"echo test\n")
    args = ["--script-file", str(path), "--", "echo other"] if both else []
    run_main(monkeypatch, ["--json", "shell", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["command"] == "shell"
    assert value["error"]["code"] == "arguments"


@pytest.mark.parametrize("kind", ["missing", "directory", "utf8", "nul", "size"])
def test_bad_script_file_is_not_sent_before_connection(
    tmp_path, monkeypatch, capsys, kind
):
    path = tmp_path / "script.sh"
    if kind == "directory":
        path.mkdir()
    elif kind != "missing":
        path.write_bytes(
            {"utf8": b"\xff", "nul": b"echo\0", "size": b"x" * 65537}[kind]
        )
    run_main(monkeypatch, ["--json", "shell", "--script-file", str(path)])
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["command"] == "shell"
    assert value["error"]["code"] == "scriptFile"


@pytest.mark.parametrize("from_file", [False, True])
@pytest.mark.parametrize(
    "script", ["echo first\r\necho second", "printf 'inside\r\nquotes'"]
)
def test_crlf_in_either_script_source_is_not_sent_before_connection(
    tmp_path, monkeypatch, capsys, from_file, script
):
    path = tmp_path / "script.sh"
    path.write_bytes(script.encode("utf-8"))
    args = ["--script-file", str(path)] if from_file else ["--", script]
    run_main(monkeypatch, ["--json", "shell", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["command"] == "shell"
    assert value["error"]["code"] == "script"
    assert "CRLF" in value["reason"]
    assert "LF" in value["reason"]


@pytest.mark.parametrize(
    ("args", "command"),
    [
        (["--json", "snapshot", "--bad"], "snapshot"),
        (["--json", "snapshot", "--json", "--timeout", "bad"], "snapshot"),
        (["--json", "snapshot", "--timeout", "bad"], "snapshot"),
        (["--json", "--bad", "snapshot"], "arguments"),
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
        ["snapshot", "--json"],
        ["snapshot", "--json=value"],
        ["--", "snapshot", "--json"],
    ],
)
def test_local_json_is_rejected_as_misplaced_global_option(monkeypatch, capsys, args):
    run_main(monkeypatch, args)
    output = capsys.readouterr().out
    assert output.startswith(args[0 if args[0] != "--" else 1] + ": not sent")
    assert "Place global option '--json' before the command" in output


@pytest.mark.parametrize(
    "args",
    [
        ["shell", "--", "--json"],
        ["snapshot", "--", "--json"],
        ["--device", "--json", "snapshot", "--bad"],
        ["-s--json", "snapshot", "--bad"],
        ["--session=--json", "snapshot", "--bad"],
        ["snapshot", "--in", "--json", "--bad"],
    ],
)
def test_format_probe_respects_values_and_double_dash(args):
    assert not cli.error_json(args)


@pytest.mark.parametrize(
    "args",
    [
        ["--device", "--json", "snapshot", "--bad"],
        ["-s--json", "snapshot", "--bad"],
        ["--session=--json", "snapshot", "--bad"],
        ["snapshot", "--in", "--json", "--bad"],
    ],
)
def test_json_option_value_keeps_argument_errors_in_text(monkeypatch, capsys, args):
    run_main(monkeypatch, args)
    output = capsys.readouterr().out
    assert output.startswith("snapshot: not sent")
    assert "before the command" not in output


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


@pytest.mark.parametrize(
    "args",
    [
        ["select"],
        ["forget"],
        ["address"],
        ["trust"],
        ["address", "phone:18843"],
        ["trust", "certificate.pem"],
    ],
)
def test_device_management_requires_positionals_without_connection(
    monkeypatch, capsys, args
):
    monkeypatch.setenv("ANDROIDCTL_DEVICE", "environment-device")
    run_main(monkeypatch, ["--json", "--device", "default-device", "device", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "device " + args[0]
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"
    assert "Missing argument" in value["reason"]


@pytest.mark.parametrize("command", ["select", "forget", "address", "trust"])
def test_device_management_help_requires_device(command):
    response = CliRunner().invoke(cli.app, ["device", command, "--help"])
    assert response.exit_code == 0, response.output
    assert "DEVICE" in response.output
    assert "[required]" in response.output


@pytest.mark.parametrize("command", ["pair", "address"])
@pytest.mark.parametrize(
    "address", ["phone:18843", "127.0.0.1:1", "[::1]:65535", "[fe80::1%eth0]:18843"]
)
def test_device_address_syntax_is_shared(monkeypatch, command, address):
    calls = []

    async def request(self, method, args, **kwargs):
        calls.append((method, args))
        return completed(method)

    monkeypatch.setattr(cli.LocalCall, "request", request)
    args = (
        ["pair", address, "012345", "--name", "phone"]
        if command == "pair"
        else ["device", "address", "phone", address]
    )
    result = CliRunner().invoke(cli.app, ["--device", "phone", *args])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    method, parameters = calls[0]
    assert method == ("pair" if command == "pair" else "device.address")
    assert parameters["endpoint"] == f"wss://{address}/androidctl"


@pytest.mark.parametrize("command", ["pair", "address"])
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
def test_bad_device_address_rejects_before_connection(
    monkeypatch, capsys, command, address
):
    args = (
        ["pair", address, "012345", "--name", "phone"]
        if command == "pair"
        else ["device", "address", "phone", address]
    )
    run_main(monkeypatch, ["--json", *args])
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["command"] == ("pair" if command == "pair" else "device address")
    assert value["error"]["code"] == "endpoint"


def test_pair_syntax_is_checked_before_daemon_and_preserves_leading_zeros(
    monkeypatch, capsys
):
    invoked = []

    async def request(self, method, args, **kwargs):
        assert method == "pair"
        invoked.append(args)
        return cli.completed("pair")

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
