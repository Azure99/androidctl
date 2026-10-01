import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call, ui_commands
from androidctl.protocol import completed
from connection_test_support import connections
from ui_test_support import raw_read_data, tree


async def test_workspace_roots_keep_explicit_paths_at_caller_cwd(tmp_path, monkeypatch):
    execute = ui_commands.execute
    inputs = []

    def execute_and_check_inputs(ctx, command, args, view):
        original_args = deepcopy(args)
        original_view = deepcopy(view)
        try:
            execute(ctx, command, args, view)
        finally:
            assert args == original_args
            assert view == original_view
            inputs.append((args, view))

    monkeypatch.setattr(ui_commands, "execute", execute_and_check_inputs)
    caller = tmp_path / "caller"
    environment_root = tmp_path / "environment"
    argument_root = tmp_path / "argument"
    for directory in (caller, environment_root, argument_root):
        directory.mkdir()
    (tmp_path / ".androidctl").mkdir()
    monkeypatch.chdir(caller)
    received = []

    async def dispatch(method, args):
        received.append(method)
        if method == "ui.read":
            body = json.dumps(tree()).encode()
            data = raw_read_data(body, "capture")
            return completed(method, data).wire()
        return completed(method, {"capture": "capture"}).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        for environment, argument, expected in [
            (None, None, caller),
            (environment_root, None, environment_root),
            (environment_root, "../argument", argument_root),
        ]:
            if environment is None:
                monkeypatch.delenv("ANDROIDCTL_WORKSPACE_ROOT", raising=False)
            else:
                monkeypatch.setenv("ANDROIDCTL_WORKSPACE_ROOT", str(environment))
            response = await asyncio.to_thread(
                CliRunner().invoke,
                cli.app,
                [
                    "--json",
                    "--device",
                    "phone",
                    *(["--workspace-root", argument] if argument else []),
                    "snapshot",
                ],
            )
            assert response.exit_code == 0, response.output
            data = json.loads(response.output)["data"]
            snapshot = Path(data["snapshotPath"])
            assert snapshot.is_absolute()
            assert snapshot.parent.resolve() == expected / ".androidctl/snapshots"
            assert ET.fromstring(snapshot.read_text(encoding="utf-8")).tag == "screen"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "--device", "phone", "snapshot", "--out", "body.xml"],
        )
        assert response.exit_code == 0, response.output
        data = json.loads(response.output)["data"]
        assert data["snapshotPath"] == str(caller / "body.xml")
        assert (
            ET.fromstring((caller / "body.xml").read_text(encoding="utf-8")).tag
            == "screen"
        )
    assert received == ["snapshot", "ui.read"] * 4
    assert len(inputs) == 4
    args, view = inputs[-1]
    assert view.out == Path("body.xml")
    assert not list((tmp_path / ".androidctl").iterdir())


def test_cli_delivery_preflight_rejects_without_rpc(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "existing"
    destination.write_bytes(b"keep")

    async def unexpected_connect(**kwargs):
        pytest.fail("Preflight rejection opened local TLS")

    monkeypatch.setattr(local_call, "connect_local", unexpected_connect)
    for command in (
        ["snapshot", "--out", str(destination)],
        ["snapshot", "--out", str(tmp_path / "missing/body.xml")],
        ["snapshot", "--print"],
    ):
        monkeypatch.setattr(sys, "argv", ["androidctl", "--json", *command])
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 2
        value = json.loads(capsys.readouterr().out)
        assert value["result"] == "not sent"
        assert "imageError" not in value["data"]
    assert destination.read_bytes() == b"keep"
