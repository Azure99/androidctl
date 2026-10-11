import json
import time
import xml.etree.ElementTree as ET

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import ui_client
from androidctl.cli_options import CliOptions
from androidctl.protocol import Result, completed
from androidctl.ui_client import UiClient
from ui_test_support import node, raw_read_data, tree, xml_metadata


@pytest.mark.parametrize("mode", ["screenshot", "action-image", "action"])
def test_image_request_combinations_deliver_without_extra_dispatch(
    monkeypatch, tmp_path, mode
):
    monkeypatch.chdir(tmp_path)
    calls, reads = [], []
    destination = tmp_path / "out.png"
    image_bytes = b"delivery fixture bytes"

    async def call(self, command, args):
        calls.append((command, args.copy()))
        data = {"capture": "current"}
        if command == "home":
            data["dispatchStarted"] = True
        return completed(command, data)

    async def blob(self, capture, kind):
        assert capture == "current"
        reads.append(kind)
        return (
            (json.dumps(tree()).encode(), {})
            if kind == "tree"
            else (image_bytes, {"width": 1, "height": 1})
        )

    monkeypatch.setattr(ui_client.UiClient, "call", call)
    monkeypatch.setattr(ui_client.UiClient, "blob", blob)
    args = ["screenshot"] if mode == "screenshot" else ["home"]
    if mode == "action-image":
        args.append("--screenshot")
    if mode != "action":
        args += ["--image-out", str(destination)]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][0] == ("screenshot" if mode == "screenshot" else "home")
    assert reads == (["tree"] if mode == "action" else ["tree", "image"])
    assert bool(list((tmp_path / ".androidctl/snapshots").glob("*.xml")))
    if mode != "action":
        assert destination.read_bytes() == image_bytes
        assert "imagePath=" + json.dumps(str(destination)) in result.output
        assert "imageSize=1 x 1 px" in result.output
    else:
        assert not destination.exists()


@pytest.mark.parametrize("command", ["screenshot", "home"])
def test_published_image_survives_history_failure(
    monkeypatch, tmp_path, capsys, command
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".androidctl").write_text("history blocker")
    destination = tmp_path / "published.png"
    result = Result(
        command=command,
        result="done",
        data={
            "image": {"width": 1, "height": 1},
        },
    )
    if command == "home":
        result.data["dispatchStarted"] = True
    ui_client.deliver(
        result,
        tree(),
        b"image fixture",
        ui_client.View(image_out=destination),
        time.monotonic() + 3,
        command=command,
        invocation={"command": command, "args": {}},
        context=CliOptions(None, "default", False, workspace_root=tmp_path),
        binding=None,
        paths=ui_client.DeliveryPaths(image=destination),
    )
    assert result.result == "done"
    assert result.exit_code() == 0
    if command == "home":
        assert result.data["dispatchStarted"] is True
    else:
        assert "dispatchStarted" not in result.data
    assert result.data["imagePath"] == str(destination)
    assert destination.read_bytes() == b"image fixture"
    assert "historyError" in result.data
    assert "imageError" not in result.data
    assert "snapshotPath" not in result.data
    output = capsys.readouterr().out
    assert "imagePath=" + json.dumps(str(destination)) in output
    assert "imageSize=1 x 1 px" in output


@pytest.mark.parametrize("fault", ["type", "capture", "hash", "tree-type", "metadata"])
def test_bad_attachment_cannot_replace_received_action_fact(
    tmp_path, monkeypatch, fault
):
    monkeypatch.chdir(tmp_path)
    calls = []
    tree = json.dumps(
        {
            "scope": {"window": 12, "package": "example.shop"},
            "windows": [{"id": 12, "title": "Cart"}],
            "keyboard": "hidden",
            "nodes": [node("e15")],
        }
    ).encode()

    async def call(self, command, args):
        calls.append((command, args))
        if command == "click":
            return Result(command=command, result="done", data={"capture": "c1"})
        body = tree if args["kind"] == "tree" else b"image"
        if fault == "tree-type" and args["kind"] == "tree":
            body = b"[]"
        data = raw_read_data(body, "c1")
        if fault == "metadata":
            del data["encoding"]
        elif args["kind"] == "image" and fault != "tree-type":
            field, value = {
                "type": ("size", None),
                "capture": ("capture", "another-capture"),
                "hash": ("sha256", "0" * 64),
            }[fault]
            data[field] = value
        if args["kind"] == "image":
            data["image"] = {"id": "i1", "width": 1, "height": 1, "connection": 7}
        return Result(command=command, result="done", data=data)

    monkeypatch.setattr(UiClient, "call", call)
    monkeypatch.setattr(UiClient, "action", call)
    destination = tmp_path / "out.png"
    output = CliRunner().invoke(
        cli.app,
        ["--json", "click", "e15", "--screenshot", "--image-out", str(destination)],
    )
    assert output.exit_code == 0, output.output
    result = json.loads(output.output)
    assert result["command"] == "click"
    assert result["result"] == "done"
    if fault == "tree-type":
        assert "imageError" not in result["data"]
        assert result["data"]["imagePath"] == str(destination)
        assert destination.read_bytes() == b"image"
    else:
        assert result["data"]["imageError"]
        assert "imagePath" not in result["data"]
    if fault in {"tree-type", "metadata"}:
        assert result["data"]["snapshotError"]
        assert "snapshot" not in result["data"]
        assert "snapshotPath" not in result["data"]
        if fault == "metadata":
            assert "missing required field encoding" in result["data"]["snapshotError"]
    else:
        saved = ET.fromstring((tmp_path / result["data"]["snapshotPath"]).read_bytes())
        assert saved.find("button").get("ref") == "e15"
    assert [method for method, _ in calls] == ["click", "ui.read", "ui.read"]


@pytest.mark.parametrize("mode", ["history", "explicit", "json"])
def test_image_delivery_reports_original_dimensions_and_preserves_action(
    tmp_path, monkeypatch, mode
):
    monkeypatch.chdir(tmp_path)
    tree = json.dumps(
        {
            "scope": {"window": 12, "package": "example.shop"},
            "windows": [{"id": 12, "title": "Cart"}],
            "keyboard": "hidden",
            "nodes": [node("e15")],
        }
    ).encode()
    png = b"delivered image bytes"
    image = {
        "id": "i1",
        "width": 1080,
        "height": 2400,
        "shape": {"displayId": 0},
        "connection": 7,
    }
    actions = []

    async def call(self, command, args):
        if command == "click":
            actions.append(command)
            return Result(command=command, result="done", data={"capture": "c1"})
        body = tree if args["kind"] == "tree" else png
        data = raw_read_data(body, "c1")
        data["image"] = image if args["kind"] == "image" else None
        return Result(
            command=command,
            result="done",
            data=data,
        )

    monkeypatch.setattr(UiClient, "call", call)
    monkeypatch.setattr(UiClient, "action", call)
    args = ["click", "e15", "--screenshot"]
    if mode == "explicit":
        args += ["--image-out", "screen (original).png"]
    elif mode == "json":
        args.insert(0, "--json")
    output = CliRunner().invoke(cli.app, args)
    assert output.exit_code == 0, output.output
    assert actions == ["click"]
    if mode == "json":
        result = json.loads(output.output)
        assert result["command"] == "click"
        assert result["result"] == "done"
        assert result["data"]["image"] == {
            key: value for key, value in image.items() if key != "shape"
        }
        body = (tmp_path / result["data"]["snapshotPath"]).read_text(encoding="utf-8")
        assert xml_metadata(body)["image"] == {**image, "shape": {}}
        saved = tmp_path / result["data"]["imagePath"]
    else:
        assert output.output.startswith("click: done;")
        assert len(output.output.splitlines()) == 1
        assert "imageSize=1080 x 2400 px" in output.output
        saved = (
            tmp_path / "screen (original).png"
            if mode == "explicit"
            else next(tmp_path.glob(".androidctl/shots/*.png"))
        )
        assert "imagePath=" + json.dumps(str(saved)) in output.output
    assert saved.read_bytes() == png


@pytest.mark.parametrize("existing_error", [None, "Request budget exhausted"])
def test_missing_capture_reports_requested_image_without_changing_action(
    monkeypatch, existing_error
):
    calls = []

    async def call(self, command, args):
        calls.append(command)
        data = {"snapshotError": "Task window changed during capture"}
        if existing_error:
            data["imageError"] = existing_error
        return Result(command=command, result="done", data=data)

    monkeypatch.setattr(UiClient, "call", call)
    monkeypatch.setattr(UiClient, "action", call)
    output = CliRunner().invoke(cli.app, ["click", "e15", "--screenshot"])
    assert output.exit_code == 0, output.output
    assert output.output.startswith("click: done;")
    assert 'snapshotError="Task window changed during capture"' in output.output
    assert (
        "imageError="
        + json.dumps(existing_error or "Task window changed during capture")
        in output.output
    )
    assert calls == ["click"]
