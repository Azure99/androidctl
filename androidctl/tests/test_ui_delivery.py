import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call, ui_commands
from androidctl.protocol import Result, completed
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
            body = json.dumps(tree()).encode() if args["kind"] == "tree" else b"PNG"
            data = raw_read_data(body, "capture")
            if args["kind"] == "image":
                data["image"] = {"width": 1, "height": 1}
            return completed(method, data).wire()
        return completed(method, {"capture": "capture"}).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        for index, (environment, argument, expected) in enumerate(
            [
                (None, None, caller),
                (environment_root, None, environment_root),
                (environment_root, "../argument", argument_root),
            ]
        ):
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
                    "--out",
                    f"body-{index}.xml",
                    "--screenshot",
                ],
            )
            assert response.exit_code == 0, response.output
            data = json.loads(response.output)["data"]
            assert data["snapshotPath"] == str(caller / f"body-{index}.xml")
            image = Path(data["imagePath"])
            assert image.is_absolute()
            assert image.parent.resolve() == expected / ".androidctl/shots"
            assert image.read_bytes() == b"PNG"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "--device", "phone", "snapshot", "--image-out", "image.png"],
        )
        assert response.exit_code == 0, response.output
        data = json.loads(response.output)["data"]
        assert (
            Path(data["snapshotPath"]).parent
            == environment_root / ".androidctl/snapshots"
        )
        assert data["imagePath"] == str(caller / "image.png")
        assert (caller / "image.png").read_bytes() == b"PNG"
    assert received == ["snapshot", "ui.read", "ui.read"] * 4
    assert len(inputs) == 4
    args, view = inputs[-1]
    assert args["screenshot"] is False
    assert view.image_out == Path("image.png")
    assert not list((tmp_path / ".androidctl").iterdir())


@pytest.mark.parametrize(
    "case",
    [
        "body",
    ],
)
async def test_create_new_races_keep_independent_delivery_facts(
    tmp_path, monkeypatch, case
):
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "raced-output"
    received = []

    async def dispatch(method, args):
        received.append(method)
        if method == "ui.read":
            data = raw_read_data(
                json.dumps(tree()).encode()
                if args["kind"] == "tree"
                else b"PNG fixture",
                "capture",
            )
            if args["kind"] == "image":
                data["image"] = {"width": 1, "height": 1}
            return completed(method, data).wire()
        destination.write_bytes(b"other writer")
        data = {"capture": "capture"}
        if method == "click":
            data["dispatchStarted"] = True
        return Result(
            command=method,
            result="UNKNOWN" if case == "unknown-image" else "done",
            exit=3 if case == "unknown-image" else None,
            data=data,
        ).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        command = (
            ["snapshot", "--out"] if case == "body" else ["click", "e15", "--image-out"]
        )
        output = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--device",
                "phone",
                *command,
                str(destination),
                *(["--screenshot"] if case == "body" else []),
            ],
        )
        value = json.loads(output.stdout)
        assert value["command"] == command[0]
        assert received == [
            "snapshot" if case == "body" else "click",
            "ui.read",
            "ui.read",
        ]
        assert destination.read_bytes() == b"other writer"
        if case == "body":
            assert output.exit_code == 1
            assert value["result"] == "failed"
            assert value["data"]["snapshotError"]
            assert "snapshotPath" not in value["data"]
            assert not list(tmp_path.glob(".androidctl/snapshots/*"))
            assert (
                tmp_path / value["data"]["imagePath"]
            ).read_bytes() == b"PNG fixture"
        else:
            assert value["result"] == ("UNKNOWN" if case == "unknown-image" else "done")
            assert output.exit_code == (3 if case == "unknown-image" else 0)
            assert value["data"]["dispatchStarted"] is True
            assert value["data"]["imageError"]
            assert "imagePath" not in value["data"]
            assert (tmp_path / value["data"]["snapshotPath"]).is_file()


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
