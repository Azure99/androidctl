import asyncio
import base64
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call, ui_commands
from androidctl.protocol import CHUNK_BYTES, Result, completed
from connection_test_support import connections
from ui_test_support import node, raw_read_data, tree, xml_metadata


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


async def test_cli_body_formats_and_images_follow_observation_freshness(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source = tree()
    source["nodes"].append(node("e16", "text", "Other row", clickable=False))
    received = []
    image = {"id": "current-image", "width": 1, "height": 1}

    async def dispatch(method, args):
        received.append((method, args))
        if method == "ui.read":
            data = raw_read_data(
                json.dumps(source).encode()
                if args["kind"] == "tree"
                else b"PNG fixture",
                "capture",
            )
            if args["kind"] == "image":
                data["image"] = image
            return completed(method, data).wire()
        return completed(
            method,
            {
                "capture": "capture",
                "snapshotCached": args["cached"],
                "snapshotAgeMs": 30,
                "snapshotSequence": 7,
                "snapshotSampleMs": 100,
            },
        ).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        cases = [
            (["snapshot"], False, False, False),
            (["snapshot"], True, True, False),
            (["snapshot", "--cached"], False, False, True),
            (["snapshot", "e15"], True, False, True),
            (["find", "Pay"], False, True, True),
            (["find", "Pay"], True, True, True),
        ]
        for index, (command, raw, as_json, cached) in enumerate(cases):
            destination = tmp_path / (
                f"body-{index}.json" if raw else f"body-{index}.xml"
            )
            before = len(received)
            output = await asyncio.to_thread(
                CliRunner().invoke,
                cli.app,
                [
                    *(["--json"] if as_json else []),
                    "--device",
                    "phone",
                    "-s",
                    "formats",
                    *command,
                    "--out",
                    str(destination),
                    *([] if cached else ["--screenshot"]),
                    *(["--raw"] if raw else []),
                    *([] if as_json else ["--print"]),
                ],
            )
            assert output.exit_code == 0, output.output
            calls = received[before:]
            kinds = ["tree"] if cached else ["tree", "image"]
            assert [method for method, _ in calls] == [
                "snapshot",
                *(["ui.read"] * len(kinds)),
            ]
            assert calls[0][1]["cached"] is cached
            assert calls[0][1].get("screenshot", False) is (not cached)
            assert [args["kind"] for _, args in calls[1:]] == kinds
            saved = destination.read_text(encoding="utf-8")
            if raw:
                assert json.loads(saved) == source
            else:
                metadata = xml_metadata(saved)
                if cached:
                    assert metadata["source"]["snapshotCached"] is True
                else:
                    assert "snapshotCached" not in metadata["source"]
                assert metadata["source"]["snapshotAgeMs"] == 30
                assert metadata["snapshotFacts"]["sequence"] == 7
                assert metadata.get("image") == (None if cached else image)
            if as_json:
                value = json.loads(output.stdout)
                assert value["command"] == command[0]
                assert value["data"]["snapshotPath"] == str(destination)
                assert value["data"]["snapshotCached"] is cached
                assert value["data"]["snapshotAgeMs"] == 30
                if command[0] == "find":
                    assert value["data"]["matches"] == 1
                if cached:
                    assert "image" not in value["data"]
                    assert "imagePath" not in value["data"]
                else:
                    assert value["data"]["image"]["width"] == 1
                    assert value["data"]["image"]["height"] == 1
                    assert (
                        Path(value["data"]["imagePath"]).read_bytes() == b"PNG fixture"
                    )
                assert "snapshot" not in value["data"]
            else:
                assert output.stdout.endswith(saved)
                assert "snapshotPath=" + json.dumps(str(destination)) in output.stdout
                if cached:
                    assert "age=30ms" in output.stdout
                    assert "imagePath=" not in output.stdout
                else:
                    assert "imageSize=1 x 1 px" in output.stdout
            assert not list(tmp_path.glob(".androidctl/snapshots/*"))
        assert [
            path.read_bytes() for path in tmp_path.glob(".androidctl/shots/*.png")
        ] == [b"PNG fixture"] * 2


@pytest.mark.parametrize("case", ["body", "action-image", "unknown-image"])
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
        ["home", "--image-out", str(destination)],
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


async def test_print_broken_os_pipe_preserves_action_and_saved_body(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source = tree()
    source["nodes"][0]["view"]["name"] = "large body " * 20000
    received = []

    async def dispatch(method, args):
        received.append(method)
        if method == "ui.read":
            body = json.dumps(source).encode()
            data = raw_read_data(body, "capture")
            offset = args["offset"]
            data["offset"] = offset
            data["bytes"] = base64.b64encode(
                body[offset : offset + CHUNK_BYTES]
            ).decode()
            return completed(method, data).wire()
        return completed(method, {"capture": "capture", "dispatchStarted": True}).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        monkeypatch.setattr(
            sys, "argv", ["androidctl", "--device", "phone", "click", "e15", "--print"]
        )
        reader, writer = os.pipe()

        def close_after_body_header():
            with os.fdopen(reader, "rb", buffering=0) as stream:
                prefix = bytearray()
                while line := stream.readline():
                    prefix.extend(line)
                    if line.startswith(b"<?androidctl "):
                        assert prefix.startswith(b"click: done"), prefix
                        return bytes(prefix)
                pytest.fail("Saved body was not echoed to the OS pipe")

        def invoke():
            errors = io.StringIO()
            with (
                os.fdopen(writer, "w", encoding="utf-8", buffering=1) as stream,
                redirect_stdout(stream),
                redirect_stderr(errors),
                pytest.raises(SystemExit) as stopped,
            ):
                cli.main()
            return stopped.value.code, errors.getvalue()

        _, (exit_code, errors) = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(close_after_body_header), asyncio.to_thread(invoke)
            ),
            15,
        )
        assert exit_code == 0
        assert "delivery: done; stdout:" in errors
        assert received.count("click") == 1
        (saved,) = tmp_path.glob(".androidctl/snapshots/*.xml")
        assert source["nodes"][0]["view"]["name"] in saved.read_text(encoding="utf-8")
