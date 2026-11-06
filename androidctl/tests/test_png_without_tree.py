import json
import time
from pathlib import Path

import pytest

from androidctl import ui_client
from androidctl.cli_options import CliOptions
from androidctl.protocol import Result


@pytest.mark.parametrize(
    ("command", "outcome"), [("screenshot", "done"), ("click", "failed")]
)
def test_received_image_is_delivered_when_tree_is_unavailable(
    tmp_path, capsys, command, outcome
):
    result = Result(
        command=command,
        result=outcome,
        reason="Refused" if outcome == "failed" else None,
        data={
            "snapshotError": "Tree attachment failed",
            "image": {"width": 1, "height": 1},
        },
    )
    ui_client.deliver(
        result,
        None,
        b"received PNG bytes",
        ui_client.View(),
        time.monotonic() + 10,
        command=command,
        invocation={"command": command},
        context=CliOptions(
            device="phone", session="test", as_json=True, workspace_root=tmp_path
        ),
        paths=ui_client.DeliveryPaths(),
    )
    wire = json.loads(capsys.readouterr().out)
    assert wire["result"] == outcome
    assert wire.get("reason") == ("Refused" if outcome == "failed" else None)
    assert wire["data"]["snapshotError"] == "Tree attachment failed"
    assert "snapshotPath" not in wire["data"]
    assert Path(wire["data"]["imagePath"]).read_bytes() == b"received PNG bytes"
