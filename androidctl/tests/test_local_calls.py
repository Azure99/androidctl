import asyncio
import json
import time

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import job_commands, local_call, ui_client
from androidctl import local_server as local_server_module
from androidctl.cli_options import CliOptions
from androidctl.private_files import write_json
from androidctl.protocol import (
    JSON_UTF8_BYTES,
    Fault,
    Problem,
    Result,
    TransportFailure,
    completed,
)
from androidctl.transport import Peer, encode_message
from connection_test_support import connections, local_server
from pairing_test_support import new_device_identity, save_active


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("shape", ["small", "edge", "oversize"])
def test_file_script_uses_device_json_budget_before_business_send(
    tmp_path, monkeypatch, explicit, shape
):
    script_path = tmp_path / "script.sh"
    script_path.write_bytes(
        {"small": b"printf test\n", "edge": b"/" * 48854, "oversize": b"\\" * 60000}[
            shape
        ]
    )
    script = job_commands.script_from_file(script_path)

    async def run():
        received = []

        async def dispatch(method, args):
            received.append(method)
            return completed(method).wire()

        async with connections(tmp_path, dispatch) as (_, _, connect_local):
            connected = []

            async def connect(**kwargs):
                connected.append(True)
                return await connect_local(**kwargs)

            monkeypatch.setattr(local_call, "connect_local", connect)
            client = job_commands.JobClient(
                CliOptions("phone" if explicit else None, "task", False)
            )
            try:
                result = await client.call("shell.start", {"script": script})
                if shape == "oversize":
                    assert result.result == "not sent"
                    assert result.exit_code() == 2
                    assert result.error.code == "jsonLimit"
                else:
                    assert result.result == "done"
                assert connected == [True]
                assert received == ([] if shape == "oversize" else ["shell.start"])
            finally:
                await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(("prefix", "encoded_bytes"), [("", 0), ("/", 2)])
@pytest.mark.parametrize("method", ["snapshot", "shell.start", "file.begin"])
async def test_added_device_budget_counts_toward_json_limit_before_send(
    tmp_path, prefix, encoded_bytes, method
):
    received = []

    async def dispatch(method, args):
        received.append(args)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, _):
        peer = await Peer.open(material)
        try:
            # The snapshot envelope has 235 bytes with empty query/name, including
            # timeoutMs=2000, both identity fields and the largest deadlineMs.
            # Reserve 300 UTF-8 name bytes; each slash consumes two encoded bytes.
            envelope_bytes = 535 + len(method) - len("snapshot")
            query = prefix + "x" * (JSON_UTF8_BYTES - envelope_bytes - encoded_bytes)
            assert (
                await peer.request(
                    method,
                    {"query": query, "timeoutMs": 2000},
                    deadline=time.monotonic() + 10,
                )
            )["result"] == "done"
            with pytest.raises(Fault) as caught:
                await peer.request(
                    method,
                    {"query": query + "x", "timeoutMs": 2000},
                    deadline=time.monotonic() + 10,
                )
            assert caught.value.problem.code == "jsonLimit"
            assert received == [{"query": query, "timeoutMs": 2000}]
        finally:
            await peer.close()


@pytest.mark.parametrize("failure", [KeyError, TypeError, ValueError, RuntimeError])
async def test_local_dispatch_internal_failure_retains_uncertain_execution(
    tmp_path, failure
):
    effects = []

    async def dispatch(method, args, deadline):
        effects.append(method)
        raise failure("internal failure after observable effect")

    async with local_server(tmp_path / "state", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            with pytest.raises(TransportFailure, match="internal failure") as caught:
                await peer.request("change", deadline=time.monotonic() + 10)
            assert effects == ["change"]
            assert caught.value.problem.stage is None
        finally:
            await peer.close()


@pytest.mark.parametrize("failure", ["local", "device", "rejected"])
async def test_two_hop_failures_keep_the_connection_source(tmp_path, failure):
    calls = []

    async def action(method, args):
        calls.append(method)
        if failure == "rejected":
            raise Fault("notReady", "Original target not ready", "worker.guard")
        failed = (
            caller
            if failure == "local"
            else next(iter(coordinator.devices.values()))._peers["mutation"]
        )
        failed.connection.transport.abort()
        return completed("click").wire()

    async with connections(tmp_path, action) as (coordinator, _, connect):
        caller = await connect(deadline=time.monotonic() + 3)
        try:
            request = caller.request(
                "call",
                {"command": "click", "args": {"timeoutMs": 2000}},
                deadline=time.monotonic() + (2),
                validate=Result.model_validate,
            )
            if failure == "local":
                with pytest.raises(TransportFailure) as caught:
                    await request
                assert caught.value.problem.stage == "local.websocket"
            else:
                result = Result.model_validate(await request)
                assert result.result == (
                    "not sent" if failure == "rejected" else "UNKNOWN"
                )
                assert result.error.stage == (
                    "worker.guard" if failure == "rejected" else "device.websocket"
                )
            assert calls == ["click"]
        finally:
            await caller.close()


async def test_local_dispatch_uncertainty_preserves_the_original_problem(tmp_path):
    problem = Problem(
        code="replyLost",
        message="Device reply was lost after dispatch",
        stage="device.websocket",
        diagnostic_truncated=True,
    )
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        raise TransportFailure.from_problem(problem)

    async with local_server(tmp_path / "tls", dispatch) as (material, _):
        client = local_call.LocalCall("phone", "task")
        client.peer = await Peer.open(material, source="local")
        try:
            with pytest.raises(TransportFailure) as caught:
                await client.call("click", {}, deadline=time.monotonic() + 2)
            assert received == ["call"]
            assert caught.value.problem == problem
            assert client.binding is None
        finally:
            await client.close()


@pytest.mark.parametrize(
    ("arguments", "command"),
    [(["status"], "status"), (["snapshot"], "snapshot"), (["shell", "true"], "shell")],
)
def test_uncertain_local_reply_uses_the_actual_cli_command(
    tmp_path, monkeypatch, arguments, command
):
    monkeypatch.chdir(tmp_path)
    problem = Problem(
        code="replyLost", message="Original reply lost", stage="device.websocket"
    )

    async def run():
        received = []

        async def dispatch(method, args, deadline):
            received.append(method)
            raise TransportFailure.from_problem(problem)

        async with local_server(tmp_path / "ipc", dispatch) as (material, _):

            async def connect(*, deadline, start=True):
                return await Peer.open(material, source="local", deadline=deadline)

            monkeypatch.setattr(local_call, "connect_local", connect)
            result = await asyncio.to_thread(
                CliRunner().invoke, cli.app, ["--json", "--device", "phone", *arguments]
            )
            assert result.exit_code == 3, result.output
            value = json.loads(result.stdout)
            assert value["command"] == command
            assert value["result"] == "UNKNOWN"
            assert value["error"] == problem.wire()
            assert received == ["call"]
            assert "deviceId" not in value["data"]["context"]

    asyncio.run(run())


async def test_job_output_lost_query_reply_is_unknown(tmp_path, monkeypatch):
    received = []

    async def dispatch(method, args, deadline):
        received.append((method, args["command"], args["args"]))
        return completed("job.get", {"job": "j1"}).wire()

    original_send = local_server_module.send

    async def send(connection, value):
        if "result" in json.loads(value):
            await connection.close()
            return
        await original_send(connection, value)

    monkeypatch.setattr(local_server_module, "send", send)
    async with local_server(tmp_path / "ipc", dispatch) as (material, _):

        async def connect(*, deadline, start=True):
            return await Peer.open(material, source="local", deadline=deadline)

        monkeypatch.setattr(local_call, "connect_local", connect)
        destination = tmp_path / "out.bin"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--device",
                "phone",
                "job",
                "output",
                "j1",
                "--out",
                str(destination),
            ],
        )
    assert response.exit_code == 3, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == "UNKNOWN"
    assert value["data"]["job"] == "j1"
    assert value["error"]["code"] == "replyUnavailable"
    assert value["error"]["stage"] == "local.websocket"
    assert "deviceId" not in value["data"]["context"]
    assert received == [("call", "job.get", {"job": "j1"})]
    assert not destination.exists()


async def test_job_output_unknown_device_is_rejected_before_device_rpc(
    tmp_path, monkeypatch
):
    async def dispatch(method, args):
        pytest.fail("An unknown device sent a device RPC")

    async with connections(tmp_path, dispatch) as (_, _, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        destination = tmp_path / "out.bin"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--workspace-root",
                str(tmp_path),
                "--device",
                "missing-device",
                "job",
                "output",
                "j1",
                "--out",
                str(destination),
            ],
        )
    assert response.exit_code == 2, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "deviceNotFound"
    assert value["data"]["job"] == "j1"
    assert "deviceId" not in value["data"]["context"]
    assert not destination.exists()


@pytest.mark.parametrize("case", ["rejected", "uncertain", "selection", "malformed"])
async def test_local_error_retains_problem_and_dispatch_classification(
    tmp_path, monkeypatch, case
):
    problem = Problem(
        code="deviceFailure",
        message="Retained device diagnostic",
        stage="worker.guard",
        diagnostic_truncated=True,
    )
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        raise Fault.from_problem(problem)

    original = local_server_module.send

    async def reply(connection, value):
        value = json.loads(value)
        if "error" in value:
            if case in ("uncertain", "selection"):
                value = {**value, "uncertain": True}
            elif case == "malformed":
                value = {**value, "error": {"code": "missing-message"}}
        await original(connection, encode_message(value))

    monkeypatch.setattr(local_server_module, "send", reply)
    async with local_server(tmp_path / "tls", dispatch) as (material, _):
        client = local_call.LocalCall(None if case == "selection" else "phone", "task")
        client.peer = await Peer.open(material, source="local")
        try:
            operation = (
                client.bind(time.monotonic() + 2)
                if case == "selection"
                else client.call("click", {}, deadline=time.monotonic() + 2)
            )
            with pytest.raises(Fault) as caught:
                await operation
            expected = {
                "rejected": Fault,
                "uncertain": TransportFailure,
                "selection": Fault,
                "malformed": TransportFailure,
            }[case]
            assert type(caught.value) is expected
            assert received == (["devices"] if case == "selection" else ["call"])
            if case == "malformed":
                assert caught.value.problem.code == "replyUnavailable"
                assert caught.value.problem.stage == "local.websocket"
            else:
                assert caught.value.problem == problem
                assert str(caught.value) == problem.message
        finally:
            await client.close()


async def test_ref_binding_keeps_a_reported_selection_problem_before_device_rpc(
    tmp_path, monkeypatch
):
    async def dispatch(method, args):
        pytest.fail("An unusable selection sent a device RPC")

    async with connections(tmp_path, dispatch) as (coordinator, _, connect):
        write_json(coordinator.state.selection, {})
        problem = coordinator.state.devices()["selectionError"]
        monkeypatch.setattr(local_call, "connect_local", connect)
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "--workspace-root", str(tmp_path), "click", "e15"],
        )
    assert response.exit_code == 2, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "click"
    assert value["result"] == "not sent"
    assert value["error"] == problem
    assert "deviceId" not in value["data"]["context"]


@pytest.mark.parametrize("entry", ["ui", "job"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_multi_rpc_command_keeps_its_device_when_default_changes(
    tmp_path, monkeypatch, entry, explicit
):
    received = []

    async def dispatch(method, args):
        received.append(method)
        if len(received) == 1 and explicit:
            original = coordinator.state.record(material["deviceId"])
            original.active.alias = "original"
            coordinator.state.save_record(original)
            replacement = coordinator.state.resolve_selection("other")[0]
            replacement.active.alias = "phone"
            coordinator.state.save_record(replacement)
        write_json(
            coordinator.state.selection,
            {"deviceId": replacement_id},
        )
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        replacement = save_active(
            coordinator.state,
            {
                **new_device_identity(),
                "alias": "other",
                "credential": "not-the-original-device",
            },
        )
        replacement_id = replacement["deviceId"]
        write_json(
            coordinator.state.selection,
            {"deviceId": coordinator.state.resolve_selection("phone")[0].device_id},
        )
        context = CliOptions("phone" if explicit else None, "task", False)
        client = (
            ui_client.UiClient(context, time.monotonic() + 3)
            if entry == "ui"
            else job_commands.JobClient(context)
        )
        monkeypatch.setattr(local_call, "connect_local", connect)
        try:
            for command in ("snapshot", "ui.read"):
                result = await client.call(command, {})
                assert result.result == "done"
            assert received == ["snapshot", "ui.read"]
            assert client.local.device_id == material["deviceId"]
            assert context == CliOptions("phone" if explicit else None, "task", False)
            assert (
                coordinator.state.resolve_selection(None)[0].device_id == replacement_id
            )
        finally:
            await client.close()


@pytest.mark.parametrize("source", ["global", "sole"])
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_job_query_final_context_keeps_first_binding_after_non_done_reply(
    tmp_path, monkeypatch, source, lost_reply
):
    received = []

    async def dispatch(method, args):
        received.append(method)
        if len(received) == 1:
            write_json(
                coordinator.state.selection, {"deviceId": replacement["deviceId"]}
            )
            return Result(
                command=method, result="still running", data={"job": "j1"}
            ).wire()
        return completed(method, {"job": "j1"}).wire()

    original_send = local_server_module.send

    async def send(connection, value):
        reply = json.loads(value)
        if lost_reply and reply.get("result", {}).get("result") == "done":
            await connection.close()
            return
        await original_send(connection, value)

    monkeypatch.setattr(local_server_module, "send", send)
    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        replacement = {**new_device_identity(), "alias": "other", "credential": "other"}
        if source == "global":
            save_active(coordinator.state, replacement)
            write_json(coordinator.state.selection, {"deviceId": material["deviceId"]})
        monkeypatch.setattr(local_call, "connect_local", connect)
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--workspace-root",
                str(tmp_path),
                "job",
                "get",
                "j1",
                "--wait",
                "2s",
            ],
        )
    assert response.exit_code == (3 if lost_reply else 0), response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job get"
    assert value["result"] == ("UNKNOWN" if lost_reply else "done")
    assert value["data"]["context"] == {
        "workspaceRoot": str(tmp_path),
        "deviceId": material["deviceId"],
        "deviceSource": source,
        "session": "default",
        "sessionSource": "default",
    }
    assert received == ["job.get", "job.get"]


@pytest.mark.parametrize("lost", ["selection", "business"])
async def test_selection_loss_is_not_business_dispatch_uncertainty(tmp_path, lost):
    calls = []
    release = asyncio.Event()

    async def dispatch(method, args, deadline):
        calls.append(method)
        if method == "call":
            assert args["device"] == "phone"
            assert args["session"] == "named-task"
            assert args["command"] == "fill"
        await release.wait()
        return completed(method).wire()

    async with local_server(tmp_path / "identity", dispatch, credential="local") as (
        material,
        _,
    ):
        peer = await Peer.open(material, source="local")
        client = local_call.LocalCall("phone", "named-task")
        client.peer = peer
        deadline = time.monotonic() + 0.15
        try:
            operation = (
                client.bind(deadline)
                if lost == "selection"
                else client.call("fill", {}, deadline=deadline)
            )
            with pytest.raises(Fault) as failure:
                await operation
            assert isinstance(failure.value, TransportFailure) == (lost == "business")
            assert calls == (["call"] if lost == "business" else ["devices"])
            assert failure.value.problem == Problem(
                code="replyUnavailable",
                message="Reply was not received within the request budget",
                stage="local.websocket",
            )
        finally:
            release.set()
            await client.close()
