import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import ui_client
from androidctl.cli_options import CliOptions
from androidctl.protocol import Fault, Result, completed
from androidctl.ui_commands import DEFAULT_SETTLE_MS, DEFAULT_TIMEOUT_MS
from ui_test_support import raw_read_data, tree, xml_metadata


@pytest.fixture
def captured_ui_calls(monkeypatch):
    calls = []

    async def call(self, command, payload):
        calls.append((command, payload))
        raise Fault("testRejected", "Stop at the client boundary")

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    monkeypatch.setattr(ui_client.UiClient, "action", call)
    return calls


@pytest.mark.parametrize(
    ("args", "field", "expected"),
    [
        (["snapshot", "--timeout", "1ms"], "timeoutMs", 1),
        (["snapshot", "--timeout", "1m"], "timeoutMs", 60000),
        (["click", "e1", "--settle", "0"], "settleMs", 0),
        (["click", "e1", "--settle", "60s"], "settleMs", 60000),
        (["swipe", "1", "2", "3", "4"], "durationMs", 400),
        (["swipe", "1", "2", "3", "4", "--duration", "50ms"], "durationMs", 50),
        (["swipe", "1", "2", "3", "4", "--duration", "5s"], "durationMs", 5000),
    ],
)
def test_duration_units_reach_ui_wire(captured_ui_calls, args, field, expected):
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2, result.output
    assert len(captured_ui_calls) == 1
    assert captured_ui_calls[0][1][field] == expected


def test_duration_error_in_real_cli_process():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "androidctl",
            "--json",
            "session",
            "open",
            "--idle",
            "1500ms",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 2, result.stderr
    value = json.loads(result.stdout)
    assert value["command"] == "session open"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"


@pytest.mark.parametrize("parent_file", [False, True])
def test_missing_or_nondirectory_image_parent_rejects_before_rpc(
    monkeypatch, tmp_path, parent_file
):
    parent = tmp_path / "parent"
    if parent_file:
        parent.write_text("ordinary file")

    async def call(*args):
        pytest.fail("Invalid image path reached RPC")

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    context = typer.Context(
        get_command(cli.app), obj=CliOptions(None, "default", False)
    )
    with pytest.raises(Fault) as rejected:
        ui_client.execute(
            context,
            "click",
            {"ref": "e1", "screenshot": True, "timeoutMs": 5000},
            ui_client.View(image_out=parent / "out.png"),
        )
    assert rejected.value.problem.code == "imagePath"


def test_print_and_json_reject_before_rpc(monkeypatch, tmp_path, capsys):
    async def call(*args):
        pytest.fail("Conflicting stdout formats reached RPC")

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    destination = tmp_path / "out.png"
    args = [
        "androidctl",
        "--json",
        "home",
        "--image-out",
        str(destination),
        "--print",
    ]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["command"] == "home"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"
    assert value["reason"] == "--print and --json cannot be used together"
    assert "imageError" not in value["data"]
    assert not destination.exists()


@pytest.mark.parametrize(
    ("args", "cached"),
    [
        (["snapshot"], False),
        (["--json", "snapshot"], False),
        (["--json", "snapshot", "--all", "--boxes"], False),
        (["snapshot", "--window", "12"], False),
        (["snapshot", "--cached", "--all"], True),
        (["snapshot", "e15"], True),
        (["find", "Pay"], True),
        (["--json", "find", "Pay"], True),
        (["find", "Pay", "--all", "--boxes"], True),
        (["--json", "find", "Pay", "--all", "--boxes"], True),
    ],
)
def test_collection_is_explicit_and_cache_is_identified(
    tmp_path, monkeypatch, args, cached
):
    monkeypatch.chdir(tmp_path)
    calls = []
    body = json.dumps(tree()).encode()

    async def call(self, command, parameters):
        calls.append((command, parameters))
        if command == "ui.read":
            return Result(
                command=command,
                result="done",
                data=raw_read_data(body, "c7"),
            )
        return Result(
            command=command,
            result="done",
            data={
                "capture": "c7",
                "snapshotCached": parameters["cached"],
                "snapshotSequence": 7,
                "snapshotSampleMs": 100,
                "snapshotAgeMs": 10,
                "snapshotScope": {"window": 12, "package": "example.shop"},
            },
        )

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    output = CliRunner().invoke(cli.app, args)
    assert output.exit_code == 0, output.output
    assert calls[0][1]["cached"] is cached
    (saved,) = tmp_path.glob(".androidctl/snapshots/*.xml")
    if cached and "--json" not in args:
        command = args[0]
        assert output.output.startswith(
            command + ": done; cached; sequence=7; sample=100ms; age=10ms;"
        )
    if "--json" in args:
        wire = json.loads(output.output)
        assert "snapshot" not in wire["data"]
        assert "snapshotFacts" not in wire["data"]
        if "find" in args:
            assert wire["data"]["matches"] == 1
    elif "find" in args:
        assert "matches=1;" in output.output
    contents = saved.read_text(encoding="utf-8")
    metadata = xml_metadata(contents)
    ET.fromstring(contents)
    assert metadata["snapshotFacts"]["sequence"] == 7
    render_args = metadata["args"]
    assert render_args.get("query") == ("Pay" if "find" in args else None)
    assert render_args.get("all") == (True if "--all" in args else None)
    assert render_args.get("boxes") == (True if "--boxes" in args else None)
    assert render_args.get("renderRef") == ("e15" if "e15" in args else None)
    assert not {"query", "all", "boxes"} & calls[0][1].keys()
    if "--json" not in args:
        assert "Sample:" not in output.output
        assert "observationWarning=" not in output.output
    assert metadata["source"] == {
        "capture": "c7",
        "snapshotAgeMs": 10,
        **({"snapshotCached": True} if cached else {}),
    }
    assert metadata["snapshotFacts"]["finishedMs"] == 100
    assert metadata["snapshotFacts"]["scope"] == {
        "window": 12,
        "package": "example.shop",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows alternate data streams")
@pytest.mark.parametrize(
    "args",
    [
        ["screenshot", "--image-out"],
        ["snapshot", "--out"],
        ["job", "output", "j1", "--out"],
    ],
)
def test_output_path_rejects_ads_before_request(tmp_path, args):
    original = tmp_path / "evidence.png"
    original.write_bytes(b"preserve")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "androidctl",
            "--json",
            *args,
            str(original) + ":capture",
        ],
        env={**os.environ, "LOCALAPPDATA": str(tmp_path / "host")},
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 2, result.stderr
    value = json.loads(result.stdout)
    assert value["command"] == ("job output" if args[0] == "job" else args[0])
    assert value["error"]["code"] == "filePath"
    assert original.read_bytes() == b"preserve"
    assert not (tmp_path / "host").exists()


@pytest.mark.parametrize(
    ("args", "timeout"),
    [
        (["screenshot"], 15000),
        (["screenshot", "--timeout", "5s"], 5000),
        (["screenshot", "--timeout=20s"], 20000),
        (["snapshot"], DEFAULT_TIMEOUT_MS),
        (["click", "e1"], DEFAULT_TIMEOUT_MS),
        (["click", "e1", "--screenshot"], DEFAULT_TIMEOUT_MS),
        (["home", "--screenshot"], DEFAULT_TIMEOUT_MS),
        (["key", "enter"], DEFAULT_TIMEOUT_MS),
        (["tap", "1", "2", "--screenshot"], DEFAULT_TIMEOUT_MS),
        (["swipe", "1", "2", "3", "4"], DEFAULT_TIMEOUT_MS),
    ],
)
def test_ui_budget_reaches_client(captured_ui_calls, tmp_path, args, timeout):
    calls = captured_ui_calls
    invocation = [*args, "--print"]
    if args[0] == "screenshot" or "--screenshot" in args:
        invocation += ["--image-out", str(tmp_path / "image.png")]
    result = CliRunner().invoke(cli.app, invocation)
    assert result.exit_code == 2, result.output
    assert len(calls) == 1
    command, payload = calls[0]
    assert command == args[0]
    assert payload["timeoutMs"] == timeout
    if "settleMs" in payload:
        assert payload["settleMs"] == DEFAULT_SETTLE_MS
    if "--screenshot" in args:
        assert payload["screenshot"] is True


@pytest.mark.parametrize("action", ["expand", "collapse", "dismiss"])
@pytest.mark.parametrize("by_ref", [False, True])
def test_named_action_uses_existing_target_and_observation_options(
    captured_ui_calls, action, by_ref
):
    calls = captured_ui_calls
    result = CliRunner().invoke(
        cli.app,
        [
            action,
            *(["e1"] if by_ref else ["--name", "Panel"]),
            "--in",
            "example.app",
            "--settle",
            "700ms",
        ],
    )
    assert result.exit_code == 2, result.output
    assert len(calls) == 1
    command, payload = calls[0]
    assert command == action
    assert payload["scope"] == {"package": "example.app"}
    assert payload["settleMs"] == 700
    if by_ref:
        assert payload["ref"] == "e1"
    else:
        assert payload["selector"] == {"name": "Panel"}


@pytest.mark.parametrize("key", ["enter", "tab", "del", "escape"])
@pytest.mark.parametrize("ref", [None, "e17"])
def test_key_preserves_requested_key_ref_and_ui_budget(monkeypatch, key, ref):
    calls = []

    async def call(self, command, args):
        calls.append((command, args))
        return completed(command, {"dispatchStarted": True})

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    monkeypatch.setattr(ui_client.UiClient, "action", call)
    args = ["--json", "-s", "task", "key", key]
    if ref is not None:
        args += [ref, "--window", "17"]
    result = CliRunner().invoke(cli.app, [*args, "--timeout", "900ms", "--settle", "0"])
    assert result.exit_code == 0, result.output
    wire = json.loads(result.output)
    assert wire["command"] == "key"
    assert wire["result"] == "done"
    assert wire["data"]["requested"] == {
        "action": "key",
        "key": key,
        "target": {"ref": ref} if ref else None,
    }
    command, payload = calls.pop()
    assert command == "key"
    assert payload["key"] == key
    assert payload.get("ref") == ref
    assert payload["scope"] == ({"window": 17} if ref else {})
    assert payload["timeoutMs"] == 900
    assert payload["settleMs"] == 0
    assert not calls
