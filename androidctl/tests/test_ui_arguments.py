import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import ui_client
from androidctl.protocol import Fault, Result
from androidctl.ui_commands import DEFAULT_TIMEOUT_MS
from ui_test_support import raw_read_data, tree, xml_metadata


@pytest.fixture
def captured_ui_calls(monkeypatch):
    calls = []

    async def call(self, command, payload):
        calls.append((command, payload))
        raise Fault("testRejected", "Stop at the client boundary")

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    return calls


@pytest.mark.parametrize(
    ("args", "field", "expected"),
    [
        (["snapshot", "--timeout", "1ms"], "timeoutMs", 1),
        (["snapshot", "--timeout", "1m"], "timeoutMs", 60000),
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


def test_print_and_json_reject_before_rpc(monkeypatch, tmp_path, capsys):
    async def call(*args):
        pytest.fail("Conflicting stdout formats reached RPC")

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    destination = tmp_path / "body.xml"
    args = [
        "androidctl",
        "--json",
        "snapshot",
        "--out",
        str(destination),
        "--print",
    ]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["command"] == "snapshot"
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
                "snapshotCached": cached,
                "snapshotSequence": 7,
                "snapshotSampleMs": 100,
                "snapshotAgeMs": 10,
                "snapshotScope": {"window": 12, "package": "example.shop"},
            },
        )

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    output = CliRunner().invoke(cli.app, args)
    assert output.exit_code == 0, output.output
    (saved,) = tmp_path.glob(".androidctl/snapshots/*.xml")
    if "--json" in args:
        wire = json.loads(output.output)
        assert "snapshot" not in wire["data"]
        assert "snapshotFacts" not in wire["data"]
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
        (["snapshot"], DEFAULT_TIMEOUT_MS),
    ],
)
def test_ui_budget_reaches_client(captured_ui_calls, tmp_path, args, timeout):
    calls = captured_ui_calls
    invocation = [*args, "--print"]
    result = CliRunner().invoke(cli.app, invocation)
    assert result.exit_code == 2, result.output
    assert len(calls) == 1
    command, payload = calls[0]
    assert command == args[0]
    assert payload["timeoutMs"] == timeout
