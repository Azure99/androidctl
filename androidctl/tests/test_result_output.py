import asyncio
import io
import json
import sys
import time
from urllib.parse import urlsplit

import pytest
import typer
from cryptography.hazmat.primitives.asymmetric import ec
from typer.main import get_command
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import job_commands, ui_client
from androidctl.cli_options import CliOptions
from androidctl.coordinator import Coordinator
from androidctl.output import Output, display_name
from androidctl.private_files import private_directory, write_json
from androidctl.protocol import (
    Result,
    completed,
)
from androidctl.state import HostState
from certificate_test_support import certificate
from pairing_test_support import Gateway, new_device_identity, paired, save_active
from ui_test_support import BrokenStream


def test_display_names_keep_unicode_without_terminal_or_bidi_controls():
    assert (
        display_name("\u7535\u8bdd\n\u202e\u2066\x7f\x9b")
        == '"\u7535\u8bdd\\n\\u202e\\u2066\\u007f\\u009b"'
    )


def test_device_listing_preserves_complete_ids_and_record_facts(capsys):
    ids = [
        "01234567-1111-4111-8111-111111111111",
        "01234567-2222-4222-8222-222222222222",
    ]
    devices = [
        {
            "deviceId": identifier,
            "alias": "phone\nnext",
            "endpoint": f"wss://phone-{index}:18843/androidctl",
            "selected": index == 0,
            "confirmed": index == 0,
            "pending": index == 1,
        }
        for index, identifier in enumerate(ids)
    ]
    devices.append({"path": "bad-record", "error": {"message": "unreadable"}})
    result = completed(
        "devices",
        {"devices": devices, "selectionError": {"message": "choose a device"}},
    )
    original = result.wire()
    with pytest.raises(typer.Exit) as stopped:
        cli.emit(result, render=cli.render_devices)
    assert stopped.value.exit_code == 0
    text = capsys.readouterr().out
    lines = text.splitlines()
    assert lines[0] == "devices: done"
    assert f'"phone\\nnext" [{ids[0]}] wss://phone-0:18843/androidctl selected' in lines
    assert (
        f'"phone\\nnext" [{ids[1]}] wss://phone-1:18843/androidctl '
        "unconfirmed pending confirmation" in lines
    )
    assert "bad-record: unavailable; unreadable\n" in text
    assert "Selection: choose a device\n" in text
    assert result.wire() == original
    with pytest.raises(typer.Exit):
        cli.emit(result, as_json=True)
    assert json.loads(capsys.readouterr().out) == original


def test_prior_delivery_error_reaches_json_even_when_stderr_is_broken(
    monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stderr", BrokenStream())
    result = completed(
        "click",
        {"dispatchStarted": True, "deliveryError": "stdout: broken output"},
    )
    ui_client.print_delivery(result, observed=False, command="click", as_json=True)
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "done"
    assert value["data"]["dispatchStarted"] is True
    assert value["data"]["deliveryError"] == "stdout: broken output"
    assert result.exit_code() == 0


def test_session_names_are_escaped_only_in_human_output(capsys):
    facts = {"name": "task\u202e", "pairingName": "desk\u2066", "idleSeconds": 0}
    with pytest.raises(cli.typer.Exit) as result:
        cli.emit(Result(command="session open", result="done", data=facts))
    assert result.value.exit_code == 0
    assert 'name="task\\u202e"' in capsys.readouterr().out
    session = {**facts, "ui": 0, "shell": 0, "file": 0}
    value = Result(
        command="status",
        result="done",
        data={"device": "device", "worker": "worker", "session": session},
    )
    with pytest.raises(cli.typer.Exit):
        cli.emit(value, render=cli.render_status)
    assert 'pairing="desk\\u2066"' in capsys.readouterr().out
    with pytest.raises(cli.typer.Exit):
        cli.emit(value, as_json=True)
    assert json.loads(capsys.readouterr().out)["data"]["session"] == session


@pytest.mark.parametrize("command", ["status", "device select"])
@pytest.mark.parametrize(
    ("alias", "display"),
    [
        ("phone", "phone"),
        ("\u7535\u8bdd\u202e\u009b", "\u7535\u8bdd\\u202e\\u009b"),
        ('desk"\\name', 'desk\\"\\\\name'),
    ],
)
def test_device_alias_is_escaped_only_in_text(capsys, command, alias, display):
    value = Result(
        command=command,
        result="done",
        reason=alias if command == "device select" else None,
        data={
            "device": alias,
            "worker": "ready",
            "session": {"ui": 0, "shell": 0, "file": 0},
        },
    )
    original = value.wire()
    with pytest.raises(cli.typer.Exit) as stopped:
        cli.emit(
            value,
            render={
                "status": cli.render_status,
                "device select": cli.render_selection,
            }[command],
        )
    assert stopped.value.exit_code == 0
    text = capsys.readouterr().out
    if command == "device select":
        assert text == f"device select: done; {display}\n"
    else:
        assert f"device={display} connected worker=ready\n" in text
    assert value.wire() == original
    with pytest.raises(cli.typer.Exit):
        cli.emit(value, as_json=True)
    assert json.loads(capsys.readouterr().out) == original


@pytest.mark.parametrize("closed", [False, True])
def test_expected_write_errors_keep_known_result(monkeypatch, closed):
    stream = io.StringIO() if closed else BrokenStream()
    if closed:
        stream.close()
    monkeypatch.setattr(sys, "stdout", stream)
    result = Result(command="push", result="done", exit=3)
    with pytest.raises(typer.Exit) as stopped:
        cli.emit(result)
    assert stopped.value.exit_code == 3
    assert result.result == "done"
    assert "stdout:" in result.data["deliveryError"]


def test_early_job_output_error_follows_real_wait_to_new_result(monkeypatch, capsys):
    first = Result(command="shell", result="still running", data={"job": "j1"})
    final = completed("shell", {"job": "j1", "output": {"state": "expired"}})
    assert first is not final
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append(command)
        return first if command == "shell.start" else final

    monkeypatch.setattr(job_commands.JobClient, "call", call)
    monkeypatch.setattr(sys, "stderr", BrokenStream())
    context = typer.Context(
        get_command(cli.app).commands["shell"],
        info_name="shell",
        parent=typer.Context(get_command(cli.app)),
        obj=CliOptions(None, "default", True),
    )
    with pytest.raises(typer.Exit) as stopped:
        job_commands.run(context, "shell.start", {}, wait_ms=1000)
    value = json.loads(capsys.readouterr().out)
    assert stopped.value.exit_code == 0
    assert value["command"] == "shell"
    assert value["result"] == "done"
    assert "stderr:" in value["data"]["deliveryError"]
    assert "deliveryError" not in first.data
    assert calls == ["shell.start", "job.get"]


def test_output_does_not_swallow_program_value_error(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("not a closed stream")

    monkeypatch.setattr(typer, "echo", fail)
    with pytest.raises(ValueError, match="not a closed stream"):
        Output().echo("prepared text")


def test_terminal_fields_escape_controls_without_changing_json_or_bytes(capsys):
    label = "\u7535\u8bdd\nnext\r\x1b]0;title\x07\x9b\t\u202e"
    job = Result(
        command="job cancel",
        result="failed",
        exit=3,
        data={"signalErrors": [{"pid": 42, "uid": 2000, "errno": 1, "command": label}]},
    )
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(job, {}, False, Output(), device=None, session=None)
    assert stopped.value.exit_code == 3
    signal = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("pid=")
    )
    assert signal == (
        'pid=42 uid=2000 errno=1 "\u7535\u8bdd\\nnext\\r'
        '\\u001b]0;title\\u0007\\u009b\\t\\u202e"'
    )
    with pytest.raises(typer.Exit):
        job_commands.show(
            job, {"stdout": b"\0\x1b\xff"}, True, Output(), device=None, session=None
        )
    facts = json.loads(capsys.readouterr().out)["data"]
    assert facts["signalErrors"][0]["command"] == label
    assert facts["outputBytes"] == {"stdout": "ABv/"}


@pytest.mark.parametrize("reply_command", ["device select", "apps"])
def test_command_renderer_uses_leaf_command_and_ignores_unrelated_data_keys(
    monkeypatch, reply_command
):
    collision = completed(
        reply_command,
        {"apps": [], "devices": [], "session": {}, "pid": 4, "checks": [], "apk": {}},
    )

    async def request(self, method, args, **kwargs):
        assert method == "device.select"
        assert args == {"device": "phone"}
        return collision

    monkeypatch.setattr(cli.LocalCall, "request", request)
    selected = CliRunner().invoke(cli.app, ["device", "select", "phone"])
    assert selected.exit_code == 0
    assert len(selected.stdout.splitlines()) == 1
    assert selected.stdout == "device select: done\n"
    assert not any(
        key in selected.stdout
        for key in ("apps=", "devices=", "pid=", "checks=", "apk=")
    )


async def test_forget_renderer_keeps_failed_facts(tmp_path, monkeypatch):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    saved = paired(state, new_device_identity(), "phone")
    write_json(state.selection, {})
    coordinator = Coordinator(state, instance="forget-renderer")
    try:
        forgotten = await coordinator.forget("phone")
    finally:
        await coordinator.close()

    async def forget(self, method, args, **kwargs):
        assert method == "device.forget"
        assert args == {"device": "phone"}
        return forgotten

    monkeypatch.setattr(cli.LocalCall, "request", forget)
    stopped = await asyncio.to_thread(
        CliRunner().invoke, cli.app, ["device", "forget", "phone"]
    )
    assert stopped.exit_code == 1
    output = stopped.stdout
    assert output.startswith("device forget: failed; Local pairing removed;")
    assert "workspace=" not in output
    assert "session=" not in output
    assert f"forgotten={saved['deviceId']}\n" in output
    assert "selectionCleared=false\n" in output
    assert "discardedJobFacts=0\n" in output
    assert "Selection: " + forgotten.data["selectionError"]["message"] + "\n" in output
    assert "selectionError=" not in output
    assert forgotten.data["selectionError"]["code"] == "selectionInvalid"
    assert forgotten.data["selectionError"]["stage"] == "local.configuration"
    assert str(state.selection) in forgotten.data["selectionError"]["message"]
    response = await asyncio.to_thread(
        CliRunner().invoke, cli.app, ["--json", "device", "forget", "phone"]
    )
    assert response.exit_code == 1
    assert json.loads(response.stdout) == forgotten.wire()


@pytest.mark.parametrize("command", ["pair", "address", "trust"])
async def test_connection_cleanup_renderer_uses_the_producer_string(
    tmp_path, monkeypatch, command
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        save_active(
            state,
            {**gateway.identity, "alias": "phone", "credential": "old"},
        )
        coordinator = Coordinator(state, instance="cleanup-renderer")
        device, _ = await coordinator.device(gateway.identity["deviceId"])

        async def failed_close(**kwargs):
            raise OSError("old connection cleanup failed")

        close = device.close
        monkeypatch.setattr(device, "close", failed_close)
        try:
            if command == "pair":
                result = await coordinator.pair(
                    gateway.args(), deadline=time.monotonic() + 20
                )
                arguments = [
                    "pair",
                    urlsplit(gateway.identity["endpoint"]).netloc,
                    "000042",
                    "--name",
                    "phone",
                ]
            elif command == "address":
                result = await coordinator.address(
                    {
                        "device": "phone",
                        "endpoint": "wss://updated.example:443/androidctl",
                    }
                )
                arguments = ["device", "address", "phone", "updated.example:443"]
            else:
                pem, _ = certificate(
                    ec.generate_private_key(ec.SECP256R1()), device.record.name
                )
                path = tmp_path / "certificate.pem"
                path.write_text(pem, encoding="ascii")
                result = await coordinator.trust({"device": "phone", "pem": pem})
                arguments = ["device", "trust", "phone", str(path)]
        finally:
            monkeypatch.setattr(device, "close", close)
            await coordinator.close()

    async def request(self, method, args, **kwargs):
        assert method == ("pair" if command == "pair" else f"device.{command}")
        return result

    monkeypatch.setattr(cli.LocalCall, "request", request)
    response = await asyncio.to_thread(CliRunner().invoke, cli.app, arguments)
    assert response.exit_code == 3, response.output
    assert response.stdout.startswith(f"{result.command}: done")
    assert "cleanupError=old connection cleanup failed\n" in response.stdout
    if command == "pair":
        for fact in ("registered", "confirmed", "activated"):
            assert f"{fact}=true\n" in response.stdout
    response = await asyncio.to_thread(
        CliRunner().invoke, cli.app, ["--json", *arguments]
    )
    assert response.exit_code == 3, response.output
    assert json.loads(response.stdout) == result.wire()
    assert json.loads(response.stdout)["data"]["cleanupError"] == (
        "old connection cleanup failed"
    )


@pytest.mark.parametrize("done", [False, True])
def test_status_renders_failure_checks_or_successful_state(capsys, done):
    checks = [{"check": "workerStatus", "result": "done" if done else "failed"}]
    facts = {}
    if done:
        facts.update(
            device="phone",
            worker="ready",
            workerId="worker-1",
            session={
                "name": "held-task",
                "pairingName": "another-host",
                "idleSeconds": 12,
                "callerState": "none",
                "ui": 0,
                "shell": 0,
                "file": 0,
            },
            helpers={"pids": [42], "active": ["j1"]},
            diagnostics={"maintenance": {}},
            gateway={"state": "listening"},
        )
    else:
        facts["checks"] = checks
    with pytest.raises(typer.Exit):
        cli.emit(
            Result(command="status", result="done" if done else "failed", data=facts),
            render=cli.render_status,
        )
    output = capsys.readouterr().out
    assert ("workerStatus:" in output) is not done
    assert output.splitlines()[0] == ("status: " + ("done" if done else "failed"))
    assert ("device=phone connected worker=ready\n" in output) is done
    if done:
        assert 'session="held-task" pairing="another-host" idle=12s\n' in output
        assert "callerSession=none\n" in output
        assert "workerId=worker-1\n" in output
        assert 'gateway={"state": "listening"}\n' in output
        assert "ui=0 shell=0 file=0\n" in output
        assert "helper pids=42\njobs=j1\n" in output


@pytest.mark.parametrize(
    ("state", "override", "expected"),
    [
        ("done", None, 0),
        ("failed", None, 1),
        ("not sent", None, 2),
        ("UNKNOWN", None, 3),
        ("still running", None, 4),
        ("failed", 3, 3),
    ],
)
def test_visible_result_exit(state, override, expected):
    assert (
        Result(command="fixture", result=state, exit=override).exit_code() == expected
    )
