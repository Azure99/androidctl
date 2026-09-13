import asyncio
import json
import time

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call
from androidctl import local_server as local_server_module
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
    [(["status"], "status")],
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
            assert "deviceId" not in value["data"].get("context", {})

    asyncio.run(run())


@pytest.mark.parametrize("case", ["rejected", "uncertain", "malformed"])
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
            if case == "uncertain":
                value = {**value, "uncertain": True}
            elif case == "malformed":
                value = {**value, "error": {"code": "missing-message"}}
        await original(connection, encode_message(value))

    monkeypatch.setattr(local_server_module, "send", reply)
    async with local_server(tmp_path / "tls", dispatch) as (material, _):
        client = local_call.LocalCall("phone", "task")
        client.peer = await Peer.open(material, source="local")
        try:
            operation = client.call("click", {}, deadline=time.monotonic() + 2)
            with pytest.raises(Fault) as caught:
                await operation
            expected = {
                "rejected": Fault,
                "uncertain": TransportFailure,
                "malformed": TransportFailure,
            }[case]
            assert type(caught.value) is expected
            assert received == ["call"]
            if case == "malformed":
                assert caught.value.problem.code == "replyUnavailable"
                assert caught.value.problem.stage == "local.websocket"
            else:
                assert caught.value.problem == problem
                assert str(caught.value) == problem.message
        finally:
            await client.close()
