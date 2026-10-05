import asyncio
import base64
import hashlib
import json
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import history, ui_client, ui_view
from androidctl.cli_options import CliOptions
from androidctl.local_call import BoundDevice
from androidctl.private_files import private_directory
from androidctl.protocol import CHUNK_BYTES, Fault, Result, completed
from androidctl.ui_client import View, deliver
from ui_test_support import BrokenStream, node, tree, xml_metadata


def deliver_snapshot(result, source, view, *, as_json=False, session="default"):
    ui_client.deliver(
        result,
        source,
        None,
        view,
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot", "args": {}},
        context=CliOptions(None, session, as_json, workspace_root=Path.cwd()),
        paths=ui_client.DeliveryPaths(),
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_observation_files_preserve_body_source_and_missing_facts(
    monkeypatch, tmp_path, capsys, as_json
):
    monkeypatch.chdir(tmp_path)
    facts = {
        "capture": "worker:9",
        "snapshotSequence": 7,
        "snapshotSampleMs": 100,
        "snapshotScope": {"window": 12, "package": "example.shop"},
        "snapshotBeforeAction": False,
        "snapshotCached": False,
        "snapshotAgeMs": 10,
    }
    result = completed("snapshot", dict(facts))
    view = ui_client.View()
    raw = tree()
    raw.update(partial=True, missing=[{"reason": "budget", "window": 12}], connection=3)
    raw["nodes"][0]["unavailable"] = [{"field": "description", "reason": "truncated"}]
    ui_client.deliver(
        result,
        raw,
        None,
        view,
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot", "args": {"window": 12}},
        context=CliOptions(None, "default", as_json, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    assert "<screen" not in output
    assert "<?androidctl" not in output
    assert result.result == "done"
    path = tmp_path / result.data["snapshotPath"]
    saved = path.read_text(encoding="utf-8")
    if as_json:
        wire = json.loads(output)
        assert "snapshot" not in wire["data"]
        assert "snapshotFacts" not in wire["data"]
    else:
        assert len(output.splitlines()) == 1
        assert 'observationWarning="partial; missing=1; unavailable=1"' in output
    metadata = xml_metadata(saved)
    assert metadata["args"] == {"window": 12}
    assert ET.fromstring(saved).find("button").get("ref") == "e15"
    assert path.suffix == ".xml"
    assert metadata["source"] == {
        "capture": "worker:9",
        "snapshotAgeMs": 10,
    }
    assert {key: result.data[key] for key in facts} == facts
    assert result.data["snapshotConnection"] == 3
    assert metadata["snapshotFacts"]["connection"] == 3
    assert metadata["snapshotFacts"]["sequence"] == 7
    assert metadata["snapshotFacts"]["finishedMs"] == 100
    assert metadata["snapshotFacts"]["scope"] == facts["snapshotScope"]
    assert "missing" not in metadata["snapshotFacts"]
    assert metadata["snapshotFacts"]["missingSummary"] == {
        "entries": 1,
        "reasons": {"budget": 1},
    }
    assert metadata["snapshotFacts"]["nodeUnavailable"] == [
        {"ref": "e15", "unavailable": raw["nodes"][0]["unavailable"]}
    ]


def test_raw_observation_file_preserves_original_tree(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    raw = tree()
    raw.update(partial=True, missing=[{"reason": "budget", "window": 12}])
    result = completed("snapshot")
    deliver_snapshot(result, raw, View(raw=True), as_json=True)
    output = json.loads(capsys.readouterr().out)
    assert result.result == "done"
    assert "snapshot" not in output["data"]
    assert "snapshotFacts" not in output["data"]
    path = tmp_path / result.data["snapshotPath"]
    assert path.suffix == ".json"
    assert json.loads(path.read_bytes()) == raw


def test_xml_defaults_keep_raw_and_invocation_inputs_unchanged():
    raw = tree()
    raw.update(
        startedMs=90,
        refContext="worker:lease",
        selectionScope={},
        partial=False,
        missing=[],
        connection=3,
        display={
            "interactive": True,
            "deepIdle": False,
            "userId": 0,
            "displayId": 0,
            "displayState": 2,
            "keyguardShowing": False,
            "keyguardSecure": False,
            "deviceLocked": False,
            "sampleMs": 100,
            "width": 1080,
            "height": 2400,
            "rotation": 0,
        },
    )
    invocation = {
        "command": "snapshot",
        "args": {
            "timeoutMs": 5000,
            "settleMs": 1500,
            "screenshot": False,
            "scope": {},
            "cached": False,
            "query": "Pay",
            "renderRef": "e15",
            "all": True,
            "boxes": True,
        },
    }
    source = {
        "capture": "worker:9",
        "snapshotCached": False,
        "snapshotBeforeAction": False,
        "snapshotAgeMs": 0,
    }
    original = deepcopy((raw, invocation, source))
    rendered = ui_client.render_observation(
        raw, View(), invocation, source=source, image=None
    )
    metadata = xml_metadata(rendered.body)
    assert metadata["args"] == {
        "cached": False,
        "query": "Pay",
        "renderRef": "e15",
        "all": True,
        "boxes": True,
    }
    assert metadata["source"] == {"capture": "worker:9", "snapshotAgeMs": 0}
    assert metadata["snapshotFacts"] == {
        "sequence": 7,
        "finishedMs": 100,
        "scope": {"window": 12, "package": "example.shop"},
        "startedMs": 90,
        "connection": 3,
        "display": {"width": 1080, "height": 2400, "rotation": 0},
    }
    assert rendered.facts["snapshotCompleteness"] == {
        "partial": False,
        "missingCount": 0,
        "unavailableNodeCount": 0,
    }
    raw_rendered = ui_client.render_observation(
        raw, View(raw=True), invocation, source=source, image=None
    )
    assert json.loads(raw_rendered.body) == original[0]
    assert (raw, invocation, source) == original
    no_args = ui_client.render_observation(
        raw, View(), {"command": "snapshot"}, source=source, image=None
    )
    assert "args" not in xml_metadata(no_args.body)


@pytest.mark.parametrize(
    ("display", "finished", "expected"),
    [
        ({"sampleMs": 99}, 100, {"sampleMs": 99}),
        ({"sampleMs": 100}, None, {"sampleMs": 100}),
        ({}, None, {}),
        (
            {"sampleMs": None, "interactive": None, "interactiveError": "failed"},
            100,
            {"sampleMs": None, "interactive": None, "interactiveError": "failed"},
        ),
        (
            {"displayId": 0, "displayState": 2, "displayError": "unavailable"},
            100,
            {"displayId": 0, "displayState": 2, "displayError": "unavailable"},
        ),
        (
            {
                "interactive": False,
                "deepIdle": True,
                "userId": 10,
                "displayId": 2,
                "displayState": 1,
                "keyguardShowing": True,
                "keyguardSecure": True,
                "deviceLocked": True,
            },
            100,
            {
                "interactive": False,
                "deepIdle": True,
                "userId": 10,
                "displayId": 2,
                "displayState": 1,
                "keyguardShowing": True,
                "keyguardSecure": True,
                "deviceLocked": True,
            },
        ),
    ],
)
def test_xml_defaults_preserve_nondefault_unavailable_and_unreported_facts(
    display, finished, expected
):
    raw = tree()
    raw.update(display=display, selectionScope=None, partial=None)
    if finished is None:
        del raw["finishedMs"]
    else:
        raw["finishedMs"] = finished
    invocation = {
        "command": "snapshot",
        "args": {
            "timeoutMs": 5000.0,
            "settleMs": None,
            "screenshot": 0,
            "scope": {"window": 12},
        },
    }
    source = {"snapshotCached": None, "snapshotBeforeAction": True}
    rendered = ui_client.render_observation(
        raw, View(), invocation, source=source, image=None
    )
    metadata = xml_metadata(rendered.body)
    assert metadata["args"] == invocation["args"]
    assert metadata["source"] == source
    assert metadata["snapshotFacts"]["display"] == expected
    assert metadata["snapshotFacts"]["selectionScope"] is None
    assert metadata["snapshotFacts"]["partial"] is None


def test_xml_image_shape_keeps_its_own_facts_and_nonunit_transformations():
    identity = {
        "matrix": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "magnification": [1.0, 0.0, 0.0],
    }
    translated = {**identity, "matrix": [1, 0, 12, 0, 1, 34, 0, 0, 1]}
    windows = [
        {"id": 12, "active": False, "focused": False, "transformation": identity},
        {"id": 13, "active": True, "transformation": translated},
        {"id": 14, "transformation": {"matrix": identity["matrix"]}},
        {"id": 15, "transformation": {"magnification": identity["magnification"]}},
        {"id": 16, "transformation": None, "transformationError": "unavailable"},
    ]
    image = {
        "at": 101,
        "width": 720,
        "height": 1600,
        "mappingKnown": False,
        "mappingError": "geometry changed",
        "shape": {
            "displayId": 0,
            "userId": 0,
            "interactive": True,
            "keyguardShowing": False,
            "keyguardSecure": False,
            "deviceLocked": False,
            "width": 720,
            "height": 1600,
            "rotation": 1,
            "windows": windows,
            "magnification": {"mode": 2, "activated": False, "scale": 1.0},
        },
    }
    original = deepcopy(image)
    raw = tree()
    raw["display"] = {"width": 1080, "height": 2400, "rotation": 0}
    rendered = ui_client.render_observation(
        raw, View(), {"command": "snapshot"}, source={}, image=image
    )
    metadata = xml_metadata(rendered.body)
    shape = metadata["image"]["shape"]
    assert metadata["image"] == {
        **original,
        "shape": {
            "width": 720,
            "height": 1600,
            "rotation": 1,
            "magnification": original["shape"]["magnification"],
            "windows": [{"id": 12}, *windows[1:]],
        },
    }
    assert shape["windows"][1]["transformation"] == translated
    assert image == original


@pytest.mark.parametrize(
    "shape",
    [
        None,
        {
            "displayId": None,
            "displayIdError": "unavailable",
            "userId": 10,
            "interactive": False,
            "keyguardShowing": True,
            "keyguardSecure": None,
            "deviceLocked": True,
        },
    ],
)
def test_xml_image_shape_preserves_null_error_and_nondefault_facts(shape):
    image = {"shape": shape, "mappingKnown": False, "mappingError": "unavailable"}
    rendered = ui_client.render_observation(
        tree(), View(), {"command": "snapshot"}, source={}, image=image
    )
    assert xml_metadata(rendered.body)["image"] == image


@pytest.mark.parametrize(
    ("view", "refs", "summary", "matches"),
    [
        (
            View(),
            {"e15", "e16"},
            {
                "entries": 5,
                "unreadChildren": 10,
                "unavailableChildren": 5,
                "reasons": {"repeatedSource": 2, "collectionLimit": 2},
            },
            None,
        ),
        (
            View(ref="e16"),
            {"e16"},
            {
                "entries": 6,
                "unreadChildren": 10,
                "unavailableChildren": 7,
                "reasons": {"repeatedSource": 2, "collectionLimit": 2},
            },
            None,
        ),
        (
            View(query="Branch"),
            {"e15", "e16"},
            {
                "entries": 5,
                "unreadChildren": 10,
                "unavailableChildren": 5,
                "reasons": {"repeatedSource": 2, "collectionLimit": 2},
            },
            1,
        ),
        (
            View(query="absent"),
            set(),
            {
                "entries": 7,
                "unreadChildren": 13,
                "unavailableChildren": 7,
                "reasons": {"repeatedSource": 2, "collectionLimit": 2},
            },
            0,
        ),
        (
            View(all_nodes=True),
            {"e15", "e16", "e17", "e18"},
            {"entries": 3, "unreadChildren": 6, "reasons": {"collectionLimit": 2}},
            None,
        ),
    ],
)
def test_xml_missing_groups_by_final_public_refs_without_changing_capture(
    view, refs, summary, matches
):
    raw = tree()
    raw["nodes"] = [
        node("e15", name="Branch"),
        node("e16", name="Child", parent="e15"),
        node("e17", "text", "Plain", clickable=False),
        node("e18", name="Hidden", visible=False),
        node("e19", name="Unavailable", refAvailable=False, refReason="incomplete"),
    ]
    raw.update(
        partial=True,
        missing=[
            {"ref": "e15", "unreadChildren": 0, "unavailableChildren": 2},
            {"ref": "e16", "unreadChildren": 3, "unavailableChildren": 0},
            {"ref": "e17", "unreadChildren": 4, "reason": "repeatedSource"},
            {"ref": "e18", "unavailableChildren": 5, "reason": "repeatedSource"},
            {"ref": "e19", "unreadChildren": 6, "unavailableChildren": 0},
            {"reason": "collectionLimit", "unreadChildren": 0},
            {"reason": "collectionLimit"},
        ],
    )
    original = deepcopy(raw)
    rendered = ui_client.render_observation(
        raw, view, {"command": "snapshot"}, source={}, image=None
    )
    metadata = xml_metadata(rendered.body)
    screen = ET.fromstring(rendered.body)
    assert (
        rendered.public_refs
        == refs
        == {
            element.attrib["ref"]
            for element in screen.iter()
            if "ref" in element.attrib
        }
    )
    facts = metadata["snapshotFacts"]
    expected_missing = [
        {
            key: value
            for key, value in entry.items()
            if key not in {"unreadChildren", "unavailableChildren"} or value != 0
        }
        for entry in raw["missing"]
        if entry.get("ref") in refs
    ]
    assert facts.get("missing", []) == expected_missing
    assert ("missing" in facts) is bool(expected_missing)
    assert facts["missingSummary"] == summary
    assert len(expected_missing) + summary["entries"] == 7
    assert rendered.facts["snapshotCompleteness"]["missingCount"] == 7
    assert rendered.facts.get("matches") == matches
    assert screen.get("unreadable") == "true"
    raw_rendered = ui_client.render_observation(
        raw,
        View(raw=True, ref=view.ref, query=view.query, all_nodes=view.all_nodes),
        {"command": "snapshot"},
        source={},
        image=None,
    )
    assert json.loads(raw_rendered.body) == original == raw
    assert raw_rendered.facts.get("matches") == matches


def test_xml_missing_omits_summary_when_every_entry_has_a_public_ref():
    raw = tree()
    raw["missing"] = [{"ref": "e15", "unreadChildren": 0, "unavailableChildren": 0}]
    rendered = ui_client.render_observation(
        raw, View(), {"command": "snapshot"}, source={}, image=None
    )
    facts = xml_metadata(rendered.body)["snapshotFacts"]
    assert facts["missing"] == [{"ref": "e15"}]
    assert "missingSummary" not in facts


def test_large_observation_body_is_saved_without_stdout_dump(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    raw = tree()
    name = "Long title " * 1000
    raw["nodes"][0]["view"]["name"] = name
    result = completed("snapshot")
    deliver_snapshot(result, raw, View())
    output = capsys.readouterr().out
    assert result.result == "done"
    assert "Long title " not in output
    saved = (tmp_path / result.data["snapshotPath"]).read_text(encoding="utf-8")
    assert name in saved
    assert ET.fromstring(saved).find("button").get("name") == name


def test_history_warning_keeps_delivered_observation(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    save = history.save

    def warning(*args, **kwargs):
        return save(*args, **kwargs)._replace(warning="old history cleanup failed")

    monkeypatch.setattr(history, "save", warning)
    result = completed(
        "snapshot",
        {
            "snapshotSequence": 7,
            "capture": "worker:8",
            "context": {"deviceId": "phone", "session": "task"},
        },
    )
    source = tree()
    source.update(connection=5)
    deliver_snapshot(result, source, View(), session="task")
    assert result.result == "done"
    saved = (tmp_path / result.data["snapshotPath"]).read_text(encoding="utf-8")
    metadata = xml_metadata(saved)
    assert metadata["snapshotFacts"]["sequence"] == 7
    assert ET.fromstring(saved).find("button").get("ref") == "e15"
    assert 'historyError="old history cleanup failed"' in capsys.readouterr().out


def test_history_failure_reports_missing_primary_file(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    blocker = tmp_path / ".androidctl"
    blocker.write_text("owned blocker")
    result = completed("snapshot")
    deliver_snapshot(result, tree(), View())
    assert result.result == "failed"
    assert result.exit_code() == 1
    assert "snapshotPath" not in result.data
    assert 'historyError="' in capsys.readouterr().out
    assert blocker.read_text() == "owned blocker"


@pytest.mark.parametrize("saved", [False, True])
def test_before_action_timing_is_not_a_snapshot_path(
    monkeypatch, tmp_path, capsys, saved
):
    monkeypatch.chdir(tmp_path)
    if not saved:
        (tmp_path / ".androidctl").write_text("owned blocker")
    result = Result(
        command="click e1",
        result="not sent",
        data={"dispatchStarted": False, "snapshotBeforeAction": True},
    )
    ui_client.deliver(
        result,
        tree(),
        None,
        ui_client.View(),
        time.monotonic() + 3,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert "timing=before-action" in output
    assert ("snapshotPath=" in output) is saved
    if saved:
        path = result.data["snapshotPath"]
        assert "snapshotPath=" + json.dumps(path) in output
        assert (tmp_path / path).is_file()
    assert result.result == "not sent"
    assert result.exit_code() == 2


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("state", ["done", "UNKNOWN", "not sent"])
@pytest.mark.parametrize("as_json", [False, True])
def test_only_undelivered_primary_snapshot_downgrades_done(
    monkeypatch, tmp_path, saved, state, as_json
):
    monkeypatch.chdir(tmp_path)
    if not saved:
        (tmp_path / ".androidctl").write_text("owned blocker")
    monkeypatch.setattr(sys, "stdout", BrokenStream())
    result = Result(command="snapshot", result=state)
    deliver_snapshot(result, tree(), ui_client.View(), as_json=as_json)
    expected = "failed" if state == "done" and not saved else state
    assert result.result == expected
    assert (
        result.exit_code()
        == {"done": 0, "failed": 1, "not sent": 2, "UNKNOWN": 3}[expected]
    )
    assert "deliveryError" in result.data
    assert bool(result.data.get("snapshotPath")) is saved


@pytest.mark.parametrize("operation", ["project", "screen", "header", "render"])
def test_presentation_program_error_propagates(monkeypatch, tmp_path, operation):
    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("program failure")

    monkeypatch.setattr(ui_view, operation, fail)
    with pytest.raises(ValueError, match="program failure"):
        deliver_snapshot(
            completed("snapshot"),
            tree(),
            ui_client.View(),
        )


def test_malformed_observation_preserves_known_action_and_explicit_exit(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    result = Result(
        command="click e1", result="done", exit=3, data={"dispatchStarted": True}
    )
    ui_client.deliver(
        result,
        {"nodes": [], "windows": [], "scope": {}, "missing": None},
        None,
        ui_client.View(),
        time.monotonic() + 1,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    assert output.startswith("click e1: done;")
    assert "dispatchStarted=true" not in output
    assert 'snapshotError="' in output
    assert "<screen" not in output
    assert "snapshotPath" not in result.data
    assert "snapshotCompleteness" not in result.data
    assert result.result == "done"
    assert result.exit_code() == 3


@pytest.mark.parametrize("selector", [False, True])
def test_fill_pi_omits_value_but_preserves_observed_body(
    tmp_path, monkeypatch, capsys, selector
):
    monkeypatch.chdir(tmp_path)
    value = "-5 secret value"
    tree = {
        "scope": {"window": 1},
        "windows": [{"id": 1}],
        "keyboard": "hidden",
        "nodes": [
            {
                "ref": "e1",
                "refAvailable": True,
                "parent": None,
                "visible": True,
                "editable": True,
                "text": value,
                "view": {
                    "role": "textbox",
                    "name": "输入?>",
                    "nameConsumed": False,
                    "detail": "",
                    "nameComplete": True,
                    "detailComplete": True,
                },
                "row": {"complete": True},
            }
        ],
    }
    calls = []

    async def call(self, command, args):
        calls.append((command, args.copy()))
        return completed(command, {"capture": "test"})

    async def blob(self, capture, kind):
        return json.dumps(tree).encode(), {}

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    monkeypatch.setattr(ui_client.UiClient, "blob", blob)
    monkeypatch.setattr(ui_client.UiClient, "action", call)
    args = (
        ["fill", "--name", "输入?>", "--submit", "--", value]
        if selector
        else ["fill", "e1", "--", value]
    )
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert calls[0][1]["value"] == value
    path = next((tmp_path / ".androidctl").rglob("*.xml"))
    saved = path.read_text(encoding="utf-8")
    pi = saved.split("?>", 1)[0]
    assert value not in pi
    assert '"value"' not in pi
    if selector:
        assert xml_metadata(saved)["args"]["selector"]["name"] == "输入?>"
    assert "<screen" not in result.output
    assert ET.fromstring(saved).find("textbox").get("value") == value


@pytest.mark.parametrize("command", ["snapshot", "screenshot"])
@pytest.mark.parametrize(
    ("original", "expected"),
    [("done", "failed"), ("not sent", "not sent"), ("UNKNOWN", "UNKNOWN")],
)
def test_missing_primary_artifact_does_not_rewrite_dispatch_facts(
    monkeypatch, command, original, expected
):
    async def call(self, method, args):
        return Result(
            command=method, result=original, data={"snapshotError": "controlled loss"}
        )

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    output = CliRunner().invoke(cli.app, [command])
    assert output.output.startswith(f"{command}: {expected}")
    assert output.exit_code == {"failed": 1, "not sent": 2, "UNKNOWN": 3}[expected]
    assert "Do not repeat" not in output.output


def test_raw_json_does_not_require_xml_projection(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)

    def unavailable(*args, **kwargs):
        raise Fault("snapshotInvalid", "controlled XML rendering failure")

    monkeypatch.setattr(ui_client.ui_view, "project", unavailable)
    result = Result(command="snapshot", result="done")
    ui_client.deliver(
        result,
        tree(),
        None,
        ui_client.View(raw=True),
        time.monotonic() + 1,
        command="snapshot",
        invocation={"command": "snapshot", "args": {}},
        context=CliOptions(None, "default", True, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "done"
    assert "snapshot" not in output["data"]
    assert (
        json.loads((tmp_path / output["data"]["snapshotPath"]).read_bytes()) == tree()
    )


@pytest.mark.parametrize(("all_nodes", "boxes"), [(False, False), (True, True)])
def test_xml_body_keeps_cached_source_with_json_stdout(
    tmp_path, monkeypatch, capsys, all_nodes, boxes
):
    monkeypatch.chdir(tmp_path)
    source = tree()
    source["nodes"] += [node("e16", "text", "Details", clickable=False, parent="e15")]
    source["nodes"].append(
        node("e17", "textbox", "Search", editable=True, actions=["focus", "fill"])
    )
    source["nodes"][0]["bounds"] = [0, 1, 2, 3]
    facts = {
        "snapshotCached": True,
        "snapshotSequence": 7,
        "snapshotSampleMs": 100,
        "snapshotAgeMs": 30,
    }
    result = Result(command="snapshot", result="done", data=facts)
    deliver_snapshot(
        result,
        source,
        ui_client.View(all_nodes=all_nodes, boxes=boxes),
        as_json=True,
    )
    output = json.loads(capsys.readouterr().out)
    saved_xml = tmp_path / output["data"]["snapshotPath"]
    saved = saved_xml.read_text(encoding="utf-8")
    metadata = xml_metadata(saved)
    assert metadata["snapshotFacts"]["sequence"] == 7
    assert metadata["source"]["snapshotCached"] is True
    screen = ET.fromstring(saved)
    assert screen.find("textbox").get("actions") == "focus fill"
    assert saved_xml.suffix == ".xml"
    assert screen.find("button").get("ref") == "e15"
    assert (screen.find("button").get("box") is not None) is boxes


@pytest.mark.parametrize("format_name", ["xml", "raw-json"])
def test_depth_1024_preserves_complete_observation(
    capsys, tmp_path, monkeypatch, format_name
):
    monkeypatch.chdir(tmp_path)
    source = tree()
    source["nodes"] = [
        node(f"e{i}", "text", f"Node {i}", parent=f"e{i - 1}" if i else None)
        for i in range(1024)
    ]
    settings = threading.stack_size(), sys.getrecursionlimit()
    result = completed("snapshot")
    deliver_snapshot(
        result,
        source,
        ui_client.View(raw=format_name == "raw-json"),
        as_json=format_name != "xml",
    )
    capsys.readouterr()
    assert result.result == "done"
    body = (tmp_path / result.data["snapshotPath"]).read_bytes()
    if format_name == "raw-json":
        assert json.loads(body) == source
    elif format_name == "xml":
        screen = ET.fromstring(body)
        assert screen.tag == "screen"
        assert [child.tag for child in screen] == ["window", "text"]
        children = screen.findall("text")
        for index in range(1024):
            assert len(children) == 1
            element = children[0]
            assert element.tag == "text"
            assert element.attrib == {"ref": f"e{index}", "name": f"Node {index}"}
            children = list(element)
        assert children == []
    assert (threading.stack_size(), sys.getrecursionlimit()) == settings


def test_required_observation_save_failure_keeps_cleanup_exit(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".androidctl").write_bytes(b"block directory")
    result = Result(
        command="snapshot",
        result="done",
        exit=3,
        data={"cleanupError": "prior cleanup"},
    )
    deliver(
        result,
        tree(),
        None,
        View(),
        time.monotonic() + 3,
        command="snapshot",
        invocation={"command": "snapshot", "args": {}},
        context=CliOptions(None, "default", True, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "failed"
    assert value["exit"] == 3
    assert value["data"]["cleanupError"] == "prior cleanup"
    assert "snapshotPath" not in value["data"]


@pytest.mark.parametrize("kind", ["fresh", "cached", "unavailable"])
def test_rejection_guidance_names_the_observation_actually_delivered(
    tmp_path, monkeypatch, capsys, kind
):
    monkeypatch.chdir(tmp_path)
    value = Result(
        command="click", result="not sent", data={"snapshotCached": kind == "cached"}
    )
    if kind == "cached":
        value.data.update(snapshotSequence=7, snapshotSampleMs=100, snapshotAgeMs=50)
    ui_client.deliver(
        value,
        None if kind == "unavailable" else tree(),
        None,
        ui_client.View(),
        time.monotonic() + 2,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    if kind == "unavailable":
        assert "Observation: unavailable;" in output
        assert "snapshotPath" not in value.data
    else:
        assert (tmp_path / value.data["snapshotPath"]).is_file()
        saved = (tmp_path / value.data["snapshotPath"]).read_text(encoding="utf-8")
        metadata = xml_metadata(saved)
        if kind == "cached":
            assert metadata["source"]["snapshotCached"] is True
        else:
            assert "snapshotCached" not in metadata["source"]
        assert value.data["snapshotCached"] is (kind == "cached")
        assert "observationWarning=" not in output
        assert "<screen" not in output
    assert value.result == "not sent"


@pytest.mark.parametrize(
    "error",
    [
        Fault("snapshotInvalid", "cannot render tree"),
        KeyError("cannot render tree"),
        TypeError("cannot render tree"),
    ],
)
def test_rejection_with_undeliverable_tree_keeps_known_observation_facts(
    tmp_path, monkeypatch, capsys, error
):
    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(ui_view, "render", fail)
    value = Result(command="click", result="not sent")
    source = tree()
    source.update(connection=5, partial=True, missing=[{"reason": "budget"}])
    ui_client.deliver(
        value,
        source,
        None,
        ui_client.View(query="Pay"),
        time.monotonic() + 2,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    assert "snapshotError=" + json.dumps(str(error)) in output
    assert "snapshotPath" not in value.data
    assert value.data["snapshotError"] == str(error)
    assert value.data["snapshotConnection"] == 5
    assert value.data["snapshotCompleteness"] == {
        "partial": True,
        "missingCount": 1,
        "unavailableNodeCount": 0,
    }
    assert value.data["matches"] == 1


def test_bad_history_keeps_action_done_without_stdout_body(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ".androidctl"
    private_directory(root)
    blocker = root / "snapshots"
    blocker.write_bytes(b"directory blocker")
    tree = {
        "scope": {"window": 12, "package": "example.shop"},
        "windows": [{"id": 12, "title": "Cart"}],
        "keyboard": "hidden",
        "nodes": [node("e15")],
    }
    result = Result(command="click", result="done", data={"settle": "unchanged"})
    ui_client.deliver(
        result,
        tree,
        None,
        ui_client.View(print_body=True),
        time.monotonic() + 5,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    output = capsys.readouterr().out
    assert output.startswith("click: done; settle=unchanged;")
    assert "<button" not in output
    assert 'historyError="' in output
    assert json.dumps(str(blocker))[1:-1] in output
    assert "snapshotPath=" not in output
    assert "<?androidctl" not in output
    assert result.result == "done"
    assert result.exit_code() == 0
    assert blocker.read_bytes() == b"directory blocker"


def test_delivery_timing_counts_local_rendering_without_fabricating_other_stages(
    tmp_path, monkeypatch, capsys
):
    tree = {
        "nodes": [],
        "windows": [],
        "scope": {"window": 1, "package": "app"},
        "keyboard": "hidden",
    }
    render = ui_view.render
    clock = [time.monotonic()]

    def delayed_render(*args):
        value = render(*args)
        clock[0] += 0.125
        return value

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ui_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(ui_view, "render", delayed_render)
    result = completed("snapshot", {"deliveryMs": 30})
    deliver(
        result,
        tree,
        None,
        View(),
        time.monotonic() + 5,
        command="snapshot",
        invocation={"command": "snapshot", "args": {}},
        context=CliOptions(None, "default", True, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    wire = json.loads(capsys.readouterr().out)
    assert wire["data"]["deliveryMs"] == 155
    assert not any(
        name in wire["data"] for name in ("settleMs", "preWaitMs", "uiConnectMs")
    )
    untouched = completed("click")
    deliver(
        untouched,
        None,
        None,
        View(),
        time.monotonic() + 5,
        command="click",
        invocation={"command": "click", "args": {}},
        context=CliOptions(None, "default", True, workspace_root=tmp_path),
        paths=ui_client.DeliveryPaths(),
    )
    assert json.loads(capsys.readouterr().out)["data"] == {}


def attachment_reader(monkeypatch, body, encoded, encoding):
    deadline = time.monotonic() + 2
    clock = [deadline - 2]
    monkeypatch.setattr(ui_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = ui_client.UiClient(CliOptions("phone", "test", False), deadline)
    chunks = {
        offset: {
            "state": "available",
            "capture": "c1",
            "offset": offset,
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "encoding": encoding,
            "decodedSize": len(body),
            "decodedSha256": hashlib.sha256(body).hexdigest(),
            "bytes": base64.b64encode(encoded[offset : offset + CHUNK_BYTES]).decode(),
        }
        for offset in range(0, len(encoded), CHUNK_BYTES)
    }
    offsets = []

    async def request(command, parameters, **kwargs):
        assert kwargs["deadline"] == deadline
        if deadline <= clock[0]:
            raise Fault("timeout", "Request budget exhausted before send")
        offset = parameters["args"]["offset"]
        offsets.append(offset)
        return completed(command, chunks[offset])

    client.local.peer = SimpleNamespace(opened=True, request=request)
    client.local.binding = BoundDevice("phone", "explicit")
    return client, chunks, offsets, clock


@pytest.mark.parametrize("encoding", ["raw", "zlib"])
async def test_attachment_codec_returns_the_verified_body(monkeypatch, encoding):
    body = b"abcdefgh01234567" * 8000
    encoded = body if encoding == "raw" else zlib.compress(body, 1)
    client, _, offsets, _ = attachment_reader(monkeypatch, body, encoded, encoding)
    assert (await client.blob("c1", "tree"))[0] == body
    assert offsets == list(range(0, len(encoded), CHUNK_BYTES))


@pytest.mark.parametrize(
    ("appended_bytes", "removed_bytes", "message"),
    [
        (b"extra", 0, "Incomplete or trailing compressed attachment"),
        (b"", 1, "Incomplete or trailing compressed attachment"),
    ],
)
async def test_attachment_codec_rejects_incomplete_or_trailing_compression(
    monkeypatch, appended_bytes, removed_bytes, message
):
    body = b"abcdefgh01234567" * 8000
    encoded = zlib.compress(body, 1)
    encoded = encoded[: len(encoded) - removed_bytes] + appended_bytes
    client, _, offsets, _ = attachment_reader(monkeypatch, body, encoded, "zlib")
    with pytest.raises(Fault, match=message) as caught:
        await client.blob("c1", "tree")
    assert caught.value.problem.code == "captureMismatch"
    assert offsets == [0]


@pytest.mark.parametrize(
    (
        "encoding",
        "metadata_changes",
        "omitted_fields",
        "change_offset",
        "message",
        "expected_offsets",
    ),
    [
        (
            "zlib",
            {"decodedSize": 3},
            (),
            0,
            "Incomplete or trailing compressed attachment",
            [0],
        ),
        (
            "zlib",
            {"decodedSha256": "0" * 64},
            (),
            0,
            "Decoded attachment length or hash mismatch",
            [0],
        ),
        ("zlib", {"sha256": "0" * 64}, (), 0, "Attachment hash mismatch", [0]),
        (
            "raw",
            {"decodedSize": 3},
            (),
            0,
            "Decoded attachment length or hash mismatch",
            [0, CHUNK_BYTES, 2 * CHUNK_BYTES, 3 * CHUNK_BYTES],
        ),
        (
            "raw",
            {"decodedSha256": "0" * 64},
            (),
            0,
            "Decoded attachment length or hash mismatch",
            [0, CHUNK_BYTES, 2 * CHUNK_BYTES, 3 * CHUNK_BYTES],
        ),
        (
            "raw",
            {"sha256": "0" * 64},
            (),
            0,
            "Attachment hash mismatch",
            [0, CHUNK_BYTES, 2 * CHUNK_BYTES, 3 * CHUNK_BYTES],
        ),
        (
            "zlib",
            {},
            ("encoding",),
            0,
            "Attachment metadata is missing required field encoding",
            [0],
        ),
        (
            "raw",
            {"decodedSize": 128001},
            (),
            CHUNK_BYTES,
            "Attachment metadata changed or exceeds its bound",
            [0, CHUNK_BYTES],
        ),
    ],
)
async def test_attachment_codec_rejects_invalid_or_changed_metadata(
    monkeypatch,
    encoding,
    metadata_changes,
    omitted_fields,
    change_offset,
    message,
    expected_offsets,
):
    body = b"abcdefgh01234567" * 8000
    encoded = body if encoding == "raw" else zlib.compress(body, 1)
    client, chunks, offsets, _ = attachment_reader(monkeypatch, body, encoded, encoding)
    for offset, chunk in chunks.items():
        if offset >= change_offset:
            chunk.update(metadata_changes)
            for field in omitted_fields:
                del chunk[field]
    with pytest.raises(Fault, match=message) as caught:
        await client.blob("c1", "tree")
    assert caught.value.problem.code == "captureMismatch"
    assert offsets == expected_offsets


async def test_attachment_reads_keep_the_original_deadline(monkeypatch):
    body = b"abcdefgh01234567" * 8000
    client, _, offsets, clock = attachment_reader(monkeypatch, body, body, "raw")
    request = client.local.peer.request

    async def expire_after_first_chunk(*args, **kwargs):
        response = await request(*args, **kwargs)
        clock[0] = client.deadline + 1
        return response

    client.local.peer.request = expire_after_first_chunk
    with pytest.raises(Fault, match="Request budget exhausted before send") as caught:
        await client.blob("c1", "tree")
    assert caught.value.problem.code == "timeout"
    assert offsets == [0]


async def test_attachment_cancellation_propagates_without_another_read(monkeypatch):
    body = b"abcdefgh01234567" * 8000
    client, _, offsets, _ = attachment_reader(monkeypatch, body, body, "raw")

    async def cancelled(command, parameters, **kwargs):
        assert kwargs["deadline"] == client.deadline
        offsets.append(parameters["args"]["offset"])
        raise asyncio.CancelledError()

    client.local.peer.request = cancelled
    with pytest.raises(asyncio.CancelledError):
        await client.blob("c1", "tree")
    assert offsets == [0]
