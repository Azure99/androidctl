import asyncio
import io
import json
import time
from contextlib import redirect_stdout
from copy import deepcopy
from types import SimpleNamespace

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import job_commands, local_call, ui_client
from androidctl import local_server as local_server_module
from androidctl.cli_options import CliOptions
from androidctl.coordinator import Coordinator
from androidctl.private_files import private_directory
from androidctl.protocol import (
    Fault,
    Result,
    TransportFailure,
    completed,
)
from androidctl.state import HostState
from androidctl.transport import Peer, encode_message
from androidctl.ui_client import UiClient
from connection_test_support import connections, local_server
from pairing_test_support import Gateway, save_active


def test_default_local_request_shares_its_budget_with_close(monkeypatch):
    clock = [100.0]
    received = []
    local = local_call.LocalCall()
    monkeypatch.setattr(cli, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def operation(deadline):
        received.append(("request", deadline))
        clock[0] += 7
        return completed("devices")

    async def close(*, deadline):
        received.append(("close", deadline))
        assert deadline - clock[0] == 13

    monkeypatch.setattr(local, "close", close)
    assert cli.request_local("devices", local, operation).result == "done"
    assert received == [("request", 120), ("close", 120)]


def test_stop_request_close_and_confirmation_share_one_budget(monkeypatch):
    clock = [100.0]
    received = []
    monkeypatch.setattr(cli, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def request(self, method, *, deadline, start):
        assert method == "daemon.stop"
        assert start is False
        received.append(("request", deadline))
        clock[0] += 7
        return completed(method, {"instance": "stopped-instance"})

    async def close(self, *, deadline):
        received.append(("close", deadline))
        clock[0] += 3

    async def confirm_stop(result, deadline):
        received.append(("confirm", deadline))
        assert deadline - clock[0] == 10
        assert result.data["instance"] == "stopped-instance"
        return result

    monkeypatch.setattr(local_call.LocalCall, "request", request)
    monkeypatch.setattr(local_call.LocalCall, "close", close)
    monkeypatch.setattr(cli, "confirm_stop", confirm_stop)
    response = CliRunner().invoke(cli.app, ["daemon", "stop"])
    assert response.exit_code == 0, response.output
    assert received == [("request", 120), ("close", 120), ("confirm", 120)]


@pytest.mark.parametrize("budget", [None, True, 0, -1, 1.5, "1000"])
async def test_local_envelope_rejects_invalid_budget_before_dispatch(tmp_path, budget):
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            request = {"id": "invalid", "method": "devices", "args": {}}
            if budget is not None:
                request["budgetMs"] = budget
            await peer.connection.send(encode_message(request))
            reply = await asyncio.wait_for(peer.receive(), 3)
            assert reply == {
                "id": "invalid",
                "error": {
                    "code": "invalidRequest",
                    "message": "Expected positive integer budgetMs",
                },
            }
            assert not received
            assert (await peer.request("devices", deadline=time.monotonic() + 3))[
                "result"
            ] == "done"
            assert received == ["devices"]
        finally:
            await peer.close()


async def test_local_slot_wait_spends_envelope_budget_and_keeps_parameters(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    async def dispatch(method, args, deadline):
        received.append((method, args, deadline - time.monotonic()))
        if method == "hold":
            entered.set()
            await release.wait()
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        held = asyncio.create_task(peer.request("hold", deadline=time.monotonic() + 3))
        timer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            timer = asyncio.get_running_loop().call_later(0.2, release.set)
            parameters = {"timeoutMs": 9000, "args": {"timeoutMs": 8000}}
            assert (
                await peer.request("pair", parameters, deadline=time.monotonic() + 0.8)
            )["result"] == "done"
            await held
            assert received[1][:2] == ("pair", parameters)
            assert 0 < received[1][2] < 0.55
            assert parameters == {"timeoutMs": 9000, "args": {"timeoutMs": 8000}}
        finally:
            if timer is not None:
                timer.cancel()
            release.set()
            await held
            await peer.close()


@pytest.mark.parametrize("method", ["call", "pair"])
async def test_device_timeout_fact_reaches_local_caller_with_reply_reserve(
    tmp_path, monkeypatch, method
):
    received = []
    release = asyncio.Event()

    async def dispatch(command, args):
        received.append(command)
        return completed(command).wire()

    gateway = Gateway(tmp_path / "device", dispatch=dispatch)
    gateway.credentials["fixture"] = "paired"
    original_reply = gateway.reply

    async def reply(connection, value):
        if "id" in value:
            await release.wait()
        await original_reply(connection, value)

    monkeypatch.setattr(gateway, "reply", reply)
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="test")
    async with (
        gateway.listening(),
        local_server(tmp_path / "ipc", coordinator.dispatch) as (material, _),
    ):
        save_active(
            state, {**gateway.identity, "credential": "fixture", "alias": "phone"}
        )
        peer = await Peer.open(material, source="local")
        try:
            args = (
                {"device": "phone", "command": "snapshot", "args": {"timeoutMs": 3000}}
                if method == "call"
                else gateway.args()
            )
            request = peer.request(method, args, deadline=time.monotonic() + 0.6)
            if method == "call":
                result = await request
                assert result["result"] == "UNKNOWN"
                problem = result["error"]
                assert received == ["snapshot"]
            else:
                with pytest.raises(TransportFailure) as caught:
                    await request
                problem = caught.value.problem.wire()
                assert len(gateway.registers) == 1
            assert problem["code"] == "replyUnavailable"
            assert problem["stage"] == "device.websocket"
            assert not release.is_set()
        finally:
            release.set()
            await peer.close()
            await coordinator.close()


async def test_local_message_limit_rejects_before_dispatch_and_releases_slot(tmp_path):
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            with pytest.raises(Fault, match="message limit") as caught:
                await peer.request(
                    "call",
                    {"command": "shell.start", "args": {"script": "\x01" * 50000}},
                    deadline=time.monotonic() + 3,
                )
            assert caught.value.problem.code == "messageLimit"
            assert not isinstance(caught.value, TransportFailure)
            assert not received
            assert (await peer.request("devices", deadline=time.monotonic() + 3))[
                "result"
            ] == "done"
            assert received == ["devices"]
        finally:
            await peer.close()


@pytest.mark.parametrize("entry", ["ui", "job"])
async def test_ui_keeps_total_deadline_while_job_calls_get_separate_budgets(
    tmp_path, entry
):
    received = []

    async def dispatch(method, args, deadline):
        if method == "devices":
            return completed(
                method,
                {
                    "devices": [{"deviceId": "phone", "selected": True}],
                    "context": {"deviceId": "phone", "deviceSource": "explicit"},
                },
            ).wire()
        received.append(args["command"])
        return completed(args["command"]).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        context = CliOptions("phone", "task", False)
        client = (
            ui_client.UiClient(context, time.monotonic() + 0.5)
            if entry == "ui"
            else job_commands.JobClient(context)
        )
        client.local.peer = peer
        options = {"timeout": 0.5} if entry == "job" else {}
        try:
            assert (await client.call("first", {}, **options)).result == "done"
            await asyncio.sleep(0.6)
            if entry == "ui":
                with pytest.raises(Fault, match="budget exhausted"):
                    await client.call("second", {})
                assert received == ["first"]
            else:
                assert (await client.call("second", {}, **options)).result == "done"
                assert received == ["first", "second"]
        finally:
            await client.close()


@pytest.mark.parametrize("shape", ["missing-timeout", "missing-args", "explicit"])
async def test_local_call_keeps_business_parameters_without_changing_input(
    tmp_path, shape
):
    received = []

    async def dispatch(method, args, deadline):
        received.append(args)
        return completed(args["command"]).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        body = {"device": "phone", "session": "ours", "command": "snapshot"}
        if shape != "missing-args":
            body["args"] = {"timeoutMs": 100} if shape == "explicit" else {}
        original = deepcopy(body)
        try:
            result = await peer.request("call", body, deadline=time.monotonic() + 3)
            assert result["result"] == "done"
            assert received == [original]
            assert body == original
        finally:
            await peer.close()


@pytest.mark.parametrize("lose_reply", [False, True])
async def test_queue_deadline_prevents_dispatch_even_when_its_reply_is_lost(
    tmp_path, monkeypatch, lose_reply
):
    received, replies = [], []
    entered, release = asyncio.Event(), asyncio.Event()
    reply_entered, reply_release = asyncio.Event(), asyncio.Event()
    original_send = local_server_module.send

    async def send(connection, value):
        body = json.loads(value)
        if body.get("result", {}).get("command") == "snapshot":
            replies.append(body["result"])
            reply_entered.set()
            if lose_reply:
                await reply_release.wait()
        await original_send(connection, value)

    async def dispatch(method, args):
        received.append(method)
        if method == "hold":
            entered.set()
            await release.wait()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, connect):
        peer = await connect(deadline=time.monotonic() + 3)
        device = await coordinator.device("phone")

        async def prepared_device(identifier):
            return device

        monkeypatch.setattr(coordinator, "device", prepared_device)
        monkeypatch.setattr(local_server_module, "send", send)
        held = asyncio.create_task(
            coordinator.dispatch(
                "call",
                {
                    "device": "phone",
                    "session": "ours",
                    "command": "hold",
                    "args": {"timeoutMs": 10000},
                },
                deadline=time.monotonic() + 10,
            )
        )
        timer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            # Ignoring the caller's budget would let snapshot run after this release.
            timer = asyncio.get_running_loop().call_later(0.5, release.set)
            request = peer.request(
                "call",
                {
                    "device": "phone",
                    "session": "ours",
                    "command": "snapshot",
                    "args": {"timeoutMs": 100},
                },
                deadline=time.monotonic() + 0.25,
            )
            if lose_reply:
                with pytest.raises(TransportFailure) as error:
                    await request
                assert error.value.problem.code == "replyUnavailable"
                assert error.value.problem.stage == "local.websocket"
            else:
                assert (await request)["result"] == "not sent"
            await asyncio.wait_for(reply_entered.wait(), 3)
            assert replies[0]["result"] == "not sent"
            assert replies[0]["error"]["code"] == "connectionBusy"
            await held
            await asyncio.gather(*tuple(coordinator.tasks))
            assert received == ["hold"]
        finally:
            if timer is not None:
                timer.cancel()
            reply_release.set()
            release.set()
            await held
            await peer.close()


@pytest.mark.parametrize("entry", ["app", "ui", "job"])
async def test_cli_entries_deliver_results_with_their_normal_budget(
    tmp_path, monkeypatch, entry
):
    received = []

    async def dispatch(method, args):
        received.append((method, args))
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, _, connect):
        command = "job.get" if entry == "job" else "snapshot"
        context = CliOptions("phone", "ours", True)
        monkeypatch.setattr(local_call, "connect_local", connect)

        def app_call():
            output = io.StringIO()
            with redirect_stdout(output), pytest.raises(cli.typer.Exit):
                cli.device_call(
                    typer.Context(
                        get_command(cli.app).commands["snapshot"],
                        info_name="snapshot",
                        parent=typer.Context(get_command(cli.app)),
                        obj=context,
                    ),
                    command,
                )
            return Result.model_validate(json.loads(output.getvalue()))

        client = None
        try:
            if entry == "app":
                result = await asyncio.to_thread(app_call)
            else:
                client = (
                    ui_client.UiClient(context, time.monotonic() + 20)
                    if entry == "ui"
                    else job_commands.JobClient(context)
                )
                result = await client.call(command, {})
            assert result.result == "done", result
            assert [method for method, _ in received] == [command]
            assert 0 < received[0][1]["timeoutMs"] <= 20000
        finally:
            if client:
                await client.close()


@pytest.mark.parametrize("release_slot", [False, True])
async def test_request_queue_spends_the_original_deadline(
    tmp_path, monkeypatch, release_slot
):
    received = []

    async def dispatch(method, args):
        received.append((method, args))
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, _):
        peer = await Peer.open(material)
        released, ack_entered = asyncio.Event(), asyncio.Event()
        original = peer.acknowledge

        async def ack(value):
            if value.get("result", {}).get("command") == "first":
                ack_entered.set()
                await released.wait()
            await original(value)

        monkeypatch.setattr(peer, "acknowledge", ack)
        try:
            await peer.request("first", deadline=time.monotonic() + 10)
            await asyncio.wait_for(ack_entered.wait(), 3)
            parameters = {"timeoutMs": 500}
            if release_slot:
                asyncio.get_running_loop().call_later(0.15, released.set)
                result = await peer.request(
                    "snapshot", parameters, deadline=time.monotonic() + 0.5
                )
                assert result["result"] == "done"
                assert received[1][0] == "snapshot"
                assert 0 < received[1][1]["timeoutMs"] < 400
            else:
                with pytest.raises(Fault) as caught:
                    await peer.request(
                        "snapshot", parameters, deadline=time.monotonic() + 0.1
                    )
                assert caught.value.problem.code == "connectionBusy"
                assert [method for method, _ in received] == ["first"]
            assert parameters == {"timeoutMs": 500}
        finally:
            released.set()
            await peer.close()


@pytest.mark.parametrize("delayed_hop", ["local", "device"])
async def test_welcome_read_timeout_closes_connection_without_business(
    tmp_path, monkeypatch, delayed_hop
):
    root = tmp_path / "private"
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return {"command": method, "result": "done", "data": {}}

    release, entered = asyncio.Event(), asyncio.Event()
    if delayed_hop == "device":
        root.mkdir()
        gateway = Gateway(root / "gateway", dispatch=dispatch)
        gateway.credentials["test"] = "pair"
        context = gateway.listening()
    else:

        async def local_dispatch(method, args, deadline):
            calls.append(method)
            return completed(method).wire()

        context = local_server(root, local_dispatch, credential="test")
    async with context as connection:
        if delayed_hop == "device":
            material = {
                **gateway.identity,
                "credential": "test",
                "alias": "fixture",
            }
            server = gateway
        else:
            material, server = connection
        original = server.reply if delayed_hop == "device" else local_server_module.send

        async def reply(connection, value):
            if (value if delayed_hop == "device" else json.loads(value)).get(
                "event"
            ) == "connected":
                entered.set()
                await release.wait()
            await original(connection, value)

        if delayed_hop == "device":
            monkeypatch.setattr(server, "reply", reply)
        else:
            monkeypatch.setattr(local_server_module, "send", reply)
        private_directory(root / "state")
        state = HostState(root / "state")
        if delayed_hop == "device":
            save_active(state, material)
        coordinator = Coordinator(state, instance="test")
        client = UiClient(CliOptions(None, "default", False), time.monotonic() + 10)
        original_receive = Peer.receive

        async def receive(peer):
            await asyncio.wait_for(entered.wait(), 3)
            return await asyncio.wait_for(original_receive(peer), 0.2)

        monkeypatch.setattr(Peer, "receive", receive)

        async def connect_local(*, deadline, start=True):
            return await Peer.open(material, deadline=deadline, source="local")

        monkeypatch.setattr(local_call, "connect_local", connect_local)
        try:
            request = (
                client.call("snapshot", {})
                if delayed_hop == "local"
                else coordinator.dispatch(
                    "call",
                    {"command": "snapshot", "args": {"timeoutMs": 10000}},
                    deadline=time.monotonic() + 10,
                )
            )
            if delayed_hop == "local":
                with pytest.raises(Fault, match="within the request budget") as error:
                    await request
                assert error.value.problem.stage == "local.authentication"
            else:
                result = await request
                assert result["result"] == "not sent"
                assert "within the request budget" in result["reason"]
                assert result["error"]["stage"] == "device.authentication"
            assert entered.is_set()
            assert not release.is_set()
            assert not calls
            assert server.connections
            for connection in tuple(server.connections):
                await asyncio.wait_for(connection.wait_closed(), 3)
        finally:
            release.set()
            await client.close()
            await coordinator.close()
