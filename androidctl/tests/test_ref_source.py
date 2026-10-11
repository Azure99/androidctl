import asyncio
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import history, local_call, ref_source, ui_client
from androidctl.cli_options import CliOptions, call_context
from androidctl.local_call import BoundDevice
from androidctl.protocol import Fault, Result, completed, rejected
from androidctl.transport import Peer
from connection_test_support import connections, local_server
from pairing_test_support import Gateway
from ui_test_support import node, raw_read_data, tree


def ref_rejection(code="staleRef", **facts):
    result = rejected("click", Fault(code, "Target was rebuilt"))
    result.data.update(facts)
    return result


def observation():
    value = tree()
    value.update(refContext="worker:3", connection=5, partial=False)
    value["nodes"] = [
        node(
            ref,
            name=name,
            packageName="example.shop",
            windowId=12,
            sourceId=str(index),
            resourceId="example.shop:id/pay",
            className="android.widget.Button",
            bounds=[0, index * 20, 100, index * 20 + 20],
            row={"complete": True, "label": None, "chain": []},
        )
        for index, (ref, name) in enumerate(
            (("e15", "Pay"), ("e16", "Other"), ("e17", "Hidden")), start=1
        )
    ]
    value["nodes"][2]["visible"] = False
    return value


def save_source(workspace, *, device="phone", session="task", source=None, ref="e15"):
    value = observation() if source is None else source
    body = {
        "refSource": {
            "deviceId": device,
            "session": session,
            "capture": "worker:8",
            "context": value["refContext"],
        },
        "refSignatures": ref_source.signatures(value, {ref}),
    }
    history.save(
        workspace,
        None,
        None,
        app="example.shop",
        title="Cart",
        deadline=time.monotonic() + 3,
        source_body=json.dumps(body),
    )
    return body


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("command", ["fill", "check"])
def test_delivered_source_uses_public_refs_and_complete_tree_facts(
    tmp_path, capsys, raw, command
):
    value = observation()
    value["nodes"].append(node("e18", name="Unexposed", visible=False))
    result = completed(
        command,
        {
            "capture": "worker:8",
            "snapshotCached": True,
            "snapshotSequence": 7,
            "snapshotSampleMs": 100,
            "snapshotAgeMs": 10,
            "image": {"width": 1, "height": 1},
            "context": {"deviceId": "phone", "session": "task"},
            "requested": {"target": {"ref": "old-only"}},
            # Compact targets obtain their complete signature from this tree.
            "resolved": {"method": "signature", "target": {"ref": "e16"}},
            "focus": completed(
                "focus", {"selected": {"action": "focus", "target": {"ref": "e17"}}}
            ).wire(),
            "fill": completed(
                "fill",
                {"selected": {"action": "fill", "target": {"ref": "disappeared"}}},
            ).wire(),
        },
    )
    if command == "check":
        value["nodes"][2]["visible"] = True
        result.data.pop("focus")
        result.data.pop("fill")
        result.data.update(
            resolved={"method": "signature", "target": {"ref": "e15"}},
            selected={"action": "click", "target": {"ref": "e17"}},
            verificationTarget={"ref": "e16"},
        )
    body_path, image_path = tmp_path / "body.xml", tmp_path / "image.png"
    result.data["context"] = call_context(
        CliOptions("other", "unrelated", False, tmp_path), BoundDevice("other", "args")
    )
    ui_client.deliver(
        result,
        value,
        b"PNG",
        ui_client.View(
            out=body_path, image_out=image_path, raw=raw, query=None if raw else "Pay"
        ),
        time.monotonic() + 3,
        command=command,
        invocation={"command": command},
        context=CliOptions("phone", "task", False, workspace_root=tmp_path),
        binding=BoundDevice("phone", "args"),
        paths=ui_client.DeliveryPaths(snapshot=body_path, image=image_path),
    )
    capsys.readouterr()
    assert body_path.exists()
    assert image_path.read_bytes() == b"PNG"
    (saved,) = (tmp_path / ".androidctl/snapshots").glob("*.source")
    provenance = json.loads(saved.read_text(encoding="utf-8"))
    assert provenance["refSource"] == {
        "deviceId": "phone",
        "session": "task",
        "capture": "worker:8",
        "context": "worker:3",
    }
    assert set(provenance["refSignatures"]) == (
        {"e15", "e16", "e17", "e18"} if raw else {"e15", "e16", "e17"}
    )
    assert provenance["refSignatures"]["e17"]["view"]["name"] == "Hidden"
    assert provenance["refSignatures"]["e17"]["sourceId"] == "3"
    assert "refSignatures" not in body_path.read_text(encoding="utf-8")
    assert "sourcePath" not in result.data


def test_unconfirmed_binding_keeps_artifacts_without_inventing_source(tmp_path, capsys):
    result = completed(
        "snapshot",
        {
            "capture": "worker:8",
            "context": {"deviceId": "old-device", "session": "old-task"},
        },
    )
    result.data["context"] = call_context(
        CliOptions("old-device", "old-task", False, tmp_path),
        BoundDevice("old-device", "args"),
    )
    ui_client.deliver(
        result,
        observation(),
        None,
        ui_client.View(),
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot"},
        context=CliOptions(None, "task", False, workspace_root=tmp_path),
        binding=None,
        paths=ui_client.DeliveryPaths(),
    )
    capsys.readouterr()
    assert result.result == "done"
    assert Path(result.data["snapshotPath"]).is_file()
    assert "device binding was not confirmed" in result.data["historyError"]
    assert not list(tmp_path.rglob("*.source"))


def test_source_write_failure_keeps_delivered_artifacts(tmp_path, monkeypatch, capsys):
    original = history.create

    def create(directory, name, content):
        if name.endswith(".source"):
            raise OSError("controlled source write failure")
        return original(directory, name, content)

    monkeypatch.setattr(history, "create", create)
    result = completed(
        "snapshot",
        {
            "capture": "worker:8",
            "image": {"width": 1, "height": 1},
            "context": {"deviceId": "phone", "session": "task"},
        },
    )
    result.data["context"] = call_context(
        CliOptions("phone", "task", False, tmp_path), BoundDevice("phone", "args")
    )
    ui_client.deliver(
        result,
        observation(),
        b"PNG",
        ui_client.View(),
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot"},
        context=CliOptions("phone", "task", False, workspace_root=tmp_path),
        binding=BoundDevice("phone", "args"),
        paths=ui_client.DeliveryPaths(),
    )
    capsys.readouterr()
    assert result.result == "done"
    assert Path(result.data["snapshotPath"]).is_file()
    assert Path(result.data["imagePath"]).read_bytes() == b"PNG"
    assert "controlled source write failure" in result.data["historyError"]


def test_failed_explicit_body_does_not_publish_a_source(tmp_path, monkeypatch, capsys):
    create = ui_client.create

    def fail_body(directory, name, content):
        if name.endswith(".xml"):
            raise OSError("controlled body failure")
        return create(directory, name, content)

    monkeypatch.setattr(ui_client, "create", fail_body)
    result = completed(
        "click",
        {
            "capture": "worker:8",
            "dispatchStarted": True,
            "image": {"width": 1, "height": 1},
        },
    )
    result.data["context"] = call_context(
        CliOptions("phone", "task", False, tmp_path), BoundDevice("phone", "args")
    )
    ui_client.deliver(
        result,
        observation(),
        b"PNG",
        ui_client.View(out=tmp_path / "body.xml", image_out=tmp_path / "image.png"),
        time.monotonic() + 3,
        command="click",
        invocation={"command": "click"},
        context=CliOptions("phone", "task", False, workspace_root=tmp_path),
        binding=BoundDevice("phone", "args"),
        paths=ui_client.DeliveryPaths(
            snapshot=tmp_path / "body.xml", image=tmp_path / "image.png"
        ),
    )
    capsys.readouterr()
    assert result.result == "done"
    assert result.data["dispatchStarted"] is True
    assert "controlled body failure" in result.data["snapshotError"]
    assert Path(result.data["imagePath"]).read_bytes() == b"PNG"
    assert not list(tmp_path.rglob("*.source"))


def test_source_read_uses_latest_valid_record_in_device_session(tmp_path):
    assert ref_source.load("e15", "phone", "task", tmp_path) is None
    save_source(tmp_path)
    changed = observation()
    changed["refContext"] = "worker:4"
    save_source(tmp_path, device="other", source=changed)
    save_source(tmp_path, session="other", source=changed)
    signature = ref_source.load("e15", "phone", "task", tmp_path)
    assert signature["sourceContext"] == "worker:3"
    assert ref_source.load("e99", "phone", "task", tmp_path) is None
    save_source(tmp_path, source=changed)
    signature = ref_source.load("e15", "phone", "task", tmp_path)
    assert signature["sourceContext"] == "worker:4"
    (tmp_path / ".androidctl/shots").rmdir()
    latest = max((tmp_path / ".androidctl/snapshots").glob("*.source"))
    latest.write_text("{broken", encoding="utf-8")
    signature = ref_source.load("e15", "phone", "task", tmp_path)
    assert signature["sourceContext"] == "worker:3"


def test_three_file_groups_count_actual_source_bytes_and_reclaim_together(
    tmp_path, monkeypatch
):
    files_per_group = 3
    retained_groups = history.MAX_FILES // files_per_group
    for index in range(retained_groups + 1):
        saved = history.save(
            tmp_path,
            "x",
            b"p",
            app="app",
            title=str(index),
            deadline=time.monotonic() + 3,
            source_body="源",
        )
    groups = history.scan(tmp_path / ".androidctl")
    assert len(groups) == retained_groups
    assert sum(map(len, groups.values())) == retained_groups * files_per_group
    assert min(key[0] for key in groups) == 2
    group = groups[max(groups)]
    assert {path.suffix for path in group} == {".xml", ".png", ".source"}
    assert sum(group.values()) == 5
    assert all(path.stem == saved.snapshot_path.stem for path in group)
    monkeypatch.setattr(history, "MAX_BYTES", 7)
    with pytest.raises(Fault, match="size limit"):
        history.save(
            tmp_path,
            "x",
            b"p",
            app="app",
            title="big",
            deadline=time.monotonic() + 3,
            source_body="源源",
        )


async def test_repair_and_attachment_keep_bound_device_session_and_frozen_source(
    tmp_path, monkeypatch
):
    received = []

    async def dispatch(method, args):
        received.append((method, args))
        if method == "ui.read":
            return completed(method, raw_read_data(b"{}", "worker:9")).wire()
        if len(received) == 1:
            # Later source files cannot replace the signature frozen for this command.
            changed = observation()
            changed["refContext"] = "worker:4"
            save_source(tmp_path, device=material["deviceId"], source=changed)
            record = coordinator.state.record(material["deviceId"])
            record.active.alias = "renamed"
            coordinator.state.save_record(record)
            return ref_rejection(dispatchStarted=False).wire()
        return completed(method, {"dispatchStarted": True}).wire()

    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        save_source(tmp_path, device=material["deviceId"])
        monkeypatch.setattr(local_call, "connect_local", connect)
        client = ui_client.UiClient(
            CliOptions("phone", "task", False, workspace_root=tmp_path),
            time.monotonic() + 3,
        )
        try:
            result = await client.action("click", {"ref": "e15"})
            assert result.result == "done"
            assert result.data["attempts"] == 2
            assert (await client.blob("worker:9", "tree"))[0] == b"{}"
            assert client.local.device_id == material["deviceId"]
        finally:
            await client.close()
    assert [method for method, _ in received] == ["click", "click", "ui.read"]
    first, second = received[0][1], received[1][1]
    assert first["sourceSignature"] == second["sourceSignature"]
    assert first["sourceSignature"]["sourceContext"] == "worker:3"
    assert "repairRef" not in first
    assert second["repairRef"] is True


@pytest.mark.parametrize(
    ("first", "expected_attempts"),
    [
        (Result(command="click", result="UNKNOWN", data={"dispatchStarted": False}), 1),
        (Result(command="click", result="not sent"), 1),
        (
            Result(command="click", result="not sent", data={"dispatchStarted": False}),
            1,
        ),
        (
            ref_rejection(
                dispatchStarted=True,
                focus={"result": "done", "data": {"sent": True}},
                fill={"result": "not sent", "data": {"sent": False}},
            ),
            1,
        ),
        (ref_rejection("arguments", dispatchStarted=False), 1),
        (ref_rejection(dispatchStarted=False), 2),
    ],
)
async def test_repair_requires_a_whole_known_not_dispatched_rejection(
    tmp_path, monkeypatch, first, expected_attempts
):
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return first.wire()

    async with connections(tmp_path, dispatch) as (_, material, connect):
        save_source(tmp_path, device=material["deviceId"])
        monkeypatch.setattr(local_call, "connect_local", connect)
        client = ui_client.UiClient(
            CliOptions(None, "task", False, tmp_path), time.monotonic() + 3
        )
        try:
            assert (await client.action("click", {"ref": "e15"})).data[
                "attempts"
            ] == expected_attempts
        finally:
            await client.close()
    assert calls == ["click"] * expected_attempts


async def test_missing_source_preserves_ordinary_ref_call_without_repair(
    tmp_path, monkeypatch
):
    calls = []

    async def dispatch(method, args):
        calls.append(args)
        return ref_rejection(dispatchStarted=False).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        client = ui_client.UiClient(
            CliOptions(None, "task", False, tmp_path), time.monotonic() + 3
        )
        try:
            result = await client.action("click", {"ref": "e15"})
            assert result.data["attempts"] == 1
            assert result.reason == "Target was rebuilt"
        finally:
            await client.close()
    assert len(calls) == 1
    assert calls[0]["ref"] == "e15"
    assert not {"sourceSignature", "repairRef"} & calls[0].keys()


async def test_full_source_signature_obeys_the_existing_complete_request_limit(
    tmp_path, monkeypatch
):
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, connect):
        source = observation()
        source["nodes"][0]["view"]["name"] = "n" * 64000
        save_source(tmp_path, device=material["deviceId"], source=source)
        monkeypatch.setattr(local_call, "connect_local", connect)
        client = ui_client.UiClient(
            CliOptions(None, "task", False, tmp_path), time.monotonic() + 3
        )
        try:
            result = await client.action("fill", {"ref": "e15", "value": "v" * 64000})
            assert result.result == "not sent"
            assert result.error.code == "jsonLimit"
            assert result.data["attempts"] == 1
        finally:
            await client.close()
    assert calls == []


async def test_lost_reply_keeps_original_request_without_previous_device_facts(
    tmp_path, monkeypatch
):
    calls = []
    release = asyncio.Event()
    save_source(tmp_path)

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
    assert output.exit_code == 2, output.output
    result = json.loads(output.stdout)
    data = result["data"]
    assert data["requested"] == {"target": {"ref": "e15"}, "action": "click"}
    assert data["attempts"] == 0
    assert not {"selected", "sent", "dispatchStarted", "resolved"} & data.keys()
    assert calls == ["devices"]
    assert result["error"]["code"] == "replyUnavailable"


@pytest.mark.parametrize("null_signature", [False, True])
async def test_invalid_optional_source_does_not_block_original_ref(
    tmp_path, monkeypatch, null_signature
):
    calls = []

    async def dispatch(method, args):
        calls.append((method, args))
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, connect):
        body = save_source(tmp_path, device=material["deviceId"])
        body["refSignatures"]["e15"] = None
        (next((tmp_path / ".androidctl/snapshots").glob("*.source"))).write_text(
            json.dumps(body) if null_signature else "[]"
        )
        monkeypatch.setattr(local_call, "connect_local", connect)
        output = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "--workspace-root", str(tmp_path), "-s", "task", "click", "e15"],
        )
        assert output.exit_code == 0, output.output
        result = json.loads(output.stdout)
        assert result["result"] == "done"
        assert result["data"]["attempts"] == 1
        assert result["data"]["requested"] == {
            "target": {"ref": "e15"},
            "action": "click",
        }
    assert len(calls) == 1
    assert calls[0][0] == "click"
    assert calls[0][1]["ref"] == "e15"
    assert not {"sourceSignature", "repairRef"} & calls[0][1].keys()


@pytest.mark.parametrize("lost", [1, 2])
async def test_lost_device_tls_fill_reply_keeps_only_host_facts(
    tmp_path, monkeypatch, lost
):
    target = {
        "ref": "e7",
        "role": "textbox",
        "name": "Search",
        "bounds": [0, 20, 100, 40],
        "windowId": 12,
    }
    stale = {"code": "staleRef", "message": "Original field is stale"}
    first_reply = {
        "command": "fill",
        "result": "not sent",
        "reason": "focus=done; fill=done; submit=not sent",
        "error": stale,
        "data": {
            "dispatchStarted": False,
            "resolved": {"method": "original", "target": target},
            "focus": {
                "command": "focus",
                "result": "done",
                "reason": None,
                "data": {
                    "sent": False,
                    "selected": {"action": "focus", "target": target},
                },
            },
            "fill": {
                "command": "fill",
                "result": "done",
                "reason": None,
                "data": {
                    "sent": False,
                    "selected": {"action": "fill", "target": target},
                    "verification": "matches",
                    "verificationTarget": {"ref": "e7"},
                },
            },
            "submit": {
                "command": "submit",
                "result": "not sent",
                "reason": "Original field is stale",
                "error": stale,
                "data": {"sent": False},
            },
        },
    }
    dispatched_reply = {
        "command": "fill",
        "result": "done",
        "reason": "focus=done; fill=done; submit=done",
        "data": {
            "dispatchStarted": True,
            "resolved": {"method": "original", "target": target},
            "focus": {
                "command": "focus",
                "result": "done",
                "reason": None,
                "data": {
                    "sent": True,
                    "selected": {"action": "focus", "target": target},
                },
            },
            "fill": {
                "command": "fill",
                "result": "done",
                "reason": None,
                "data": {
                    "sent": True,
                    "selected": {"action": "fill", "target": target},
                    "verification": "matches",
                    "verificationTarget": {"ref": "e7"},
                },
            },
            "submit": {
                "command": "submit",
                "result": "done",
                "reason": None,
                "data": {
                    "sent": True,
                    "selected": {"action": "ime", "target": target},
                },
            },
        },
    }
    calls = []

    async def dispatch(method, args):
        calls.append((method, args))
        assert args["ref"] == "e7"
        assert args["value"] == "hello"
        return first_reply if lost == 2 and len(calls) == 1 else dispatched_reply

    reply = Gateway.reply
    replies = 0

    async def lose_reply(self, connection, value):
        nonlocal replies
        if value.get("result", {}).get("command") == "fill":
            replies += 1
            if replies == lost:
                await connection.close()
                return
        await reply(self, connection, value)

    monkeypatch.setattr(Gateway, "reply", lose_reply)
    async with connections(tmp_path, dispatch) as (_, material, connect):
        source = observation()
        source["nodes"][0]["ref"] = "e7"
        source["nodes"][0]["view"].update(role="textbox", name="Search")
        source["nodes"][0].update(
            className="android.widget.EditText", editable=True, actions=["fill", "ime"]
        )
        save_source(tmp_path, device=material["deviceId"], source=source, ref="e7")
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
                "e7",
                "hello",
                "--submit",
                "--timeout",
                "3s",
            ],
        )
    assert output.exit_code == 3, output.output
    facts = json.loads(output.stdout)["data"]
    assert facts["requested"] == {
        "target": {"ref": "e7"},
        "action": "fill",
        "submit": True,
    }
    assert facts["attempts"] == lost
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
    assert [method for method, _ in calls] == ["fill"] * lost
    if lost == 2:
        assert calls[1][1]["repairRef"] is True
