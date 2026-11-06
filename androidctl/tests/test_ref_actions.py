import asyncio
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import history, local_call, ui_client
from androidctl.cli_options import CliOptions
from androidctl.protocol import Fault, completed, rejected
from androidctl.transport import Peer
from connection_test_support import connections, local_server
from pairing_test_support import Gateway
from ui_test_support import tree

REF = "e15:12345678-1234-4321-9876-123456789abc"


@pytest.mark.parametrize("stale", [False, True])
async def test_ref_action_is_one_call_independent_of_history(
    tmp_path, monkeypatch, stale
):
    calls = []
    business_args = []

    async def dispatch(method, args):
        calls.append((method, args))
        if stale:
            result = rejected(method, Fault("staleRef", "Target was rebuilt"))
            result.data["dispatchStarted"] = False
            return result.wire()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        for legacy_source in (False, True):
            if legacy_source:
                directory = tmp_path / ".androidctl/snapshots"
                directory.mkdir(parents=True, exist_ok=True)
                legacy = directory / "000001-20261106T010000000000Z-app-title.source"
                legacy.write_text(
                    json.dumps(
                        {
                            "refSource": {
                                "deviceId": material["deviceId"],
                                "session": "task",
                                "capture": "worker:8",
                                "context": "worker:3",
                            },
                            "refSignatures": {REF: {"ref": REF}},
                        }
                    ),
                    encoding="utf-8",
                )
            calls.clear()
            output = await asyncio.to_thread(
                CliRunner().invoke,
                cli.app,
                [
                    "--json",
                    "--workspace-root",
                    str(tmp_path),
                    "-s",
                    "task",
                    "click",
                    REF,
                ],
            )
            assert output.exit_code == (2 if stale else 0), output.output
            facts = json.loads(output.stdout)["data"]
            assert "attempts" not in facts
            assert facts["requested"] == {"target": {"ref": REF}, "action": "click"}
            assert len(calls) == 1
            assert calls[0][0] == "click"
            assert calls[0][1]["ref"] == REF
            assert not {"sourceSignature", "repairRef"} & calls[0][1].keys()
            business_args.append(
                {key: value for key, value in calls[0][1].items() if key != "timeoutMs"}
            )
    assert business_args[0] == business_args[1]


@pytest.mark.parametrize("raw", [False, True])
def test_observation_delivery_creates_only_the_requested_artifacts(
    tmp_path, capsys, raw
):
    result = completed("snapshot")
    ui_client.deliver(
        result,
        tree(),
        None,
        ui_client.View(raw=raw),
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot"},
        context=CliOptions(None, "task", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    capsys.readouterr()
    assert result.result == "done"
    assert Path(result.data["snapshotPath"]).is_file()
    assert "historyError" not in result.data
    assert not list(tmp_path.rglob("*.source"))


@pytest.mark.parametrize(("quota", "maximum"), [("MAX_FILES", 3), ("MAX_BYTES", 9)])
def test_existing_source_files_still_count_towards_history_cleanup(
    tmp_path, monkeypatch, quota, maximum
):
    first = history.save(
        tmp_path, "old", None, app="app", title="title", deadline=time.monotonic() + 3
    )
    legacy = first.snapshot_path.with_suffix(".source")
    legacy.write_bytes(b"old provenance")
    monkeypatch.setattr(history, quota, maximum)
    history.save(
        tmp_path, "new", b"PNG", app="app", title="title", deadline=time.monotonic() + 3
    )
    assert not first.snapshot_path.exists()
    assert not legacy.exists()
    groups = history.scan(tmp_path / ".androidctl")
    assert {path.suffix for group in groups.values() for path in group} == {
        ".xml",
        ".png",
    }


async def test_lost_device_fill_reply_keeps_only_host_facts(tmp_path, monkeypatch):
    calls = []

    async def dispatch(method, args):
        calls.append((method, args))
        return completed(
            method, {"dispatchStarted": True, "fill": {"result": "done"}}
        ).wire()

    reply = Gateway.reply

    async def lose_fill_reply(self, connection, value):
        if value.get("result", {}).get("command") == "fill":
            await connection.close()
            return
        await reply(self, connection, value)

    monkeypatch.setattr(Gateway, "reply", lose_fill_reply)
    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        output = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--workspace-root",
                str(tmp_path),
                "-s",
                "task",
                "fill",
                REF,
                "hello",
                "--submit",
                "--timeout",
                "3s",
            ],
        )
    assert output.exit_code == 3, output.output
    facts = json.loads(output.stdout)["data"]
    assert facts["requested"] == {
        "target": {"ref": REF},
        "action": "fill",
        "submit": True,
    }
    assert "attempts" not in facts
    assert (
        not {
            "focus",
            "fill",
            "submit",
            "selected",
            "sent",
            "dispatchStarted",
            "resolved",
        }
        & facts.keys()
    )
    assert [method for method, _ in calls] == ["fill"]


async def test_lost_local_action_reply_keeps_only_host_facts(tmp_path, monkeypatch):
    calls = []
    release = asyncio.Event()

    async def dispatch(method, args, deadline):
        calls.append(method)
        await release.wait()
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):

        async def connect(*, deadline, start=True):
            return await Peer.open(material, deadline=deadline, source="local")

        monkeypatch.setattr(local_call, "connect_local", connect)
        try:
            output = await asyncio.to_thread(
                CliRunner().invoke,
                cli.app,
                [
                    "--json",
                    "--workspace-root",
                    str(tmp_path),
                    "-s",
                    "task",
                    "click",
                    "e15",
                    "--timeout",
                    "350ms",
                ],
            )
        finally:
            release.set()
    assert output.exit_code == 3, output.output
    result = json.loads(output.stdout)
    data = result["data"]
    assert data["requested"] == {"target": {"ref": "e15"}, "action": "click"}
    assert "attempts" not in data
    assert not {"selected", "sent", "dispatchStarted", "resolved"} & data.keys()
    assert calls == ["call"]
    assert result["error"]["code"] == "replyUnavailable"
