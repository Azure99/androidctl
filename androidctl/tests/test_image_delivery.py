import json
import time

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import ui_client
from androidctl.cli_options import CliOptions
from androidctl.protocol import Result, completed
from ui_test_support import tree


@pytest.mark.parametrize(
    "mode",
    [
        "screenshot",
    ],
)
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


@pytest.mark.parametrize(
    "command",
    [
        "screenshot",
    ],
)
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
