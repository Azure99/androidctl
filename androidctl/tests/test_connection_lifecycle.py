import asyncio
import base64
import json
import socket
import time

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from androidctl import transport
from androidctl.coordinator import Coordinator
from androidctl.identity import local_identity
from androidctl.private_files import private_directory
from androidctl.protocol import VERSION, Fault, TransportFailure, completed
from androidctl.state import HostState
from androidctl.transport import Peer
from connection_test_support import connections, local_server
from pairing_test_support import Gateway


async def test_connect_tries_other_addresses_and_shares_dns_welcome_budget(
    tmp_path, monkeypatch
):
    private_directory(tmp_path / "state")
    tls, identity = local_identity(tmp_path / "state")
    requests, connections, acknowledgments = [], [], []
    phase = None
    gate, dns_gate, stage_entered = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def handle(connection):
        connections.append(connection)
        if phase == "welcome":
            stage_entered.set()
            await gate.wait()
        try:
            await connection.send(
                json.dumps(
                    {"event": "connected", "version": VERSION, "blockId": "hello"}
                )
            )
            async for message in connection:
                value = json.loads(message)
                (acknowledgments if "ack" in value else requests).append(value)
        except ConnectionClosed:
            pass

    async with serve(handle, "127.0.0.1", 0, ssl=tls) as server:
        port = server.sockets[0].getsockname()[1]
        material = {
            **identity,
            "credential": "fixture",
            "endpoint": f"wss://multiple.test:{port}/ipc",
        }
        loop = asyncio.get_running_loop()
        lookups = []
        times = {}

        async def addresses(host, number, **kwargs):
            lookups.append((host, number))
            if phase is not None:
                await dns_gate.wait()
                times["dns"] = loop.time()
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in (
                    ("127.0.0.2", "127.0.0.1") if phase is None else ("127.0.0.1",)
                )
            ]

        monkeypatch.setattr(loop, "getaddrinfo", addresses)
        peer = await Peer.open(material, deadline=time.monotonic() + 5, source="local")
        await peer.close()
        await asyncio.wait_for(connections[-1].wait_closed(), 3)
        assert lookups == [("multiple.test", port)]
        assert acknowledgments == [{"ack": "hello"}]
        original = Peer.acknowledge

        async def slow_hello(self, value):
            stage_entered.set()
            await gate.wait()
            await original(self, value)

        for phase in ("welcome", "ack"):
            gate, dns_gate, stage_entered = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            if phase == "ack":
                monkeypatch.setattr(Peer, "acknowledge", slow_hello)
            started = loop.time()
            deadline = started + 1.5

            def release(gate=gate):
                times["release"] = loop.time()
                gate.set()

            dns_timer = loop.call_later(0.6, dns_gate.set)
            stage_timer = loop.call_later(1.8, release)
            try:
                with pytest.raises(Fault) as error:
                    await asyncio.wait_for(
                        Peer.open(material, deadline=deadline, source="local"), 4
                    )
                assert error.value.problem.stage == "local.authentication"
                assert stage_entered.is_set()
                assert not gate.is_set()
                await asyncio.wait_for(connections[-1].wait_closed(), 3)
                await asyncio.wait_for(gate.wait(), 3)
                # This release would succeed if welcome or ACK reset the DNS budget.
                assert deadline < times["release"] < times["dns"] + 1.5
            finally:
                dns_timer.cancel()
                stage_timer.cancel()
                dns_gate.set()
                gate.set()
        assert acknowledgments == [{"ack": "hello"}]
        assert not requests
    assert not server.connections


async def test_different_sessions_arriving_together_do_not_queue(tmp_path):
    entered, leave = asyncio.Event(), asyncio.Event()
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        entered.set()
        await leave.wait()
        return completed(method).wire()

    async with local_server(tmp_path / "state", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        first = asyncio.create_task(
            peer.request("first", session="one", deadline=time.monotonic() + (5))
        )
        second = asyncio.create_task(
            peer.request("second", session="two", deadline=time.monotonic() + (1))
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(Fault) as error:
                await asyncio.wait_for(second, 3)
            assert error.value.problem.code == "connectionBusy"
            assert received == ["first"]
            assert not leave.is_set()
            assert not first.done()
            leave.set()
            assert (await first)["result"] == "done"
        finally:
            leave.set()
            await asyncio.gather(first, second, return_exceptions=True)
            await peer.close()


async def test_coordinator_session_wait_and_per_peer_idle(tmp_path):
    entered, leave = asyncio.Event(), asyncio.Event()
    received = []

    async def dispatch(method, args):
        received.append(method)
        if method == "file.chunk":
            entered.set()
            await leave.wait()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, _):

        async def call(command, session="ours", timeout=5):
            return await coordinator.dispatch(
                "call",
                {
                    "device": "phone",
                    "session": session,
                    "command": command,
                    "args": {"timeoutMs": int(timeout * 1000)},
                },
                deadline=time.monotonic() + timeout,
            )

        active = asyncio.create_task(call("file.chunk"))
        waiting = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            waiting = asyncio.create_task(call("snapshot"))
            refused = await asyncio.wait_for(call("click", "other"), 3)
            assert refused["result"] == "not sent"
            assert "occupied" in refused["reason"]
            assert not leave.is_set()
            assert not active.done()
            assert not waiting.done()
            assert (await call("file.get"))["result"] == "done"
            assert (await call("file.cancel"))["result"] == "done"
            assert "snapshot" not in received
            leave.set()
            assert (await active)["result"] == "done"
            assert (await waiting)["result"] == "done"
            device = next(iter(coordinator.devices.values()))
            for peer in device._peers.values():
                if peer.ack_task is not None:
                    await peer.ack_task
            mutation, query = device._peers["mutation"], device._peers["query"]
            mutation.last_used = time.monotonic() - 46
            query.last_used = time.monotonic()
            await coordinator.expire_connections()
            assert "mutation" not in device._peers
            assert not mutation.opened
            assert query.opened
            assert (await call("snapshot"))["result"] == "done"
            assert received.count("file.chunk") == 1
            assert "click" not in received
        finally:
            leave.set()
            await asyncio.gather(
                active, *([waiting] if waiting else []), return_exceptions=True
            )


@pytest.mark.parametrize("command", ["status", "session.close"])
async def test_reused_peer_survives_idle_sweep_during_request_handoff(
    tmp_path, monkeypatch, command
):
    received, sweeps = [], []

    async def dispatch(method, args):
        received.append(method)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, _):
        arguments = {
            "device": "phone",
            "session": "ours",
            "command": command,
            "args": {"timeoutMs": 2000},
        }
        assert (
            await coordinator.dispatch("call", arguments, deadline=time.monotonic() + 2)
        )["result"] == "done"
        device = next(iter(coordinator.devices.values()))
        peer = next(iter(device._peers.values()))
        if peer.ack_task is not None:
            await peer.ack_task
        peer.last_used = time.monotonic() - 46
        role = "query" if command == "status" else "mutation"
        gate = device._connection_gates[role]
        release = gate.release

        def handoff():
            release()
            monkeypatch.setattr(gate, "release", release)
            # Sweep after the connection gate releases, before request ownership.
            asyncio.get_running_loop().call_soon(
                lambda: sweeps.append(
                    asyncio.create_task(coordinator.expire_connections())
                )
            )

        monkeypatch.setattr(gate, "release", handoff)
        result = await coordinator.dispatch(
            "call", arguments, deadline=time.monotonic() + 2
        )
        assert len(sweeps) == 1
        await asyncio.gather(*sweeps)
        assert result["result"] == "done"
        assert received == [command, command]
        assert peer.opened
        assert peer in device._peers.values()


async def test_closed_is_zero_send_but_lost_reply_is_unknown_and_close_obeys_budget(
    tmp_path, monkeypatch
):
    commands = []
    lost_entered, lost_release = asyncio.Event(), asyncio.Event()
    ack_entered, ack_release = asyncio.Event(), asyncio.Event()

    async def dispatch(method, args):
        commands.append(method)
        if method == "lost":
            lost_entered.set()
            await lost_release.wait()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, _):
        peer = await Peer.open(material)
        try:
            await peer.close()
            with pytest.raises(Fault) as error:
                await peer.request("never", deadline=time.monotonic() + (0.1))
            assert not isinstance(error.value, TransportFailure)
            assert error.value.problem.code == "connectionClosed"
            assert not commands
            peer = await Peer.open(material)
            with pytest.raises(TransportFailure):
                await peer.request("lost", deadline=time.monotonic() + (0.05))
            assert commands == ["lost"]
            assert lost_entered.is_set()
            assert not lost_release.is_set()
            lost_release.set()
            await peer.close()
            peer = await Peer.open(material)
            original = peer.acknowledge

            async def slow_ack(value):
                ack_entered.set()
                await ack_release.wait()
                await original(value)

            monkeypatch.setattr(peer, "acknowledge", slow_ack)
            result = await peer.request("known", deadline=time.monotonic() + (30))
            await asyncio.wait_for(ack_entered.wait(), 3)
            await asyncio.wait_for(peer.close(deadline=time.monotonic() + 0.1), 3)
            assert not ack_release.is_set()
            assert peer.ack_task.done()
            assert result["result"] == "done"
            assert not peer.opened
            assert not peer.busy
        finally:
            lost_release.set()
            ack_release.set()
            await peer.close()


@pytest.mark.parametrize("field", ["deviceId", "name"])
async def test_device_welcome_identity_is_checked_before_business(
    tmp_path, monkeypatch, field
):
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return completed(method).wire()

    gateway = Gateway(tmp_path / "gateway", dispatch=dispatch)
    gateway.credentials["valid"] = "pair"
    reply = gateway.reply

    async def wrong_identity(connection, value):
        await reply(connection, {**value, field: "other-device"})

    monkeypatch.setattr(gateway, "reply", wrong_identity)
    async with gateway.listening():
        material = {
            **gateway.identity,
            "credential": "valid",
        }
        with pytest.raises(Fault) as caught:
            await Peer.open(material)
        assert caught.value.problem.code == "tlsIdentity"
        assert caught.value.problem.stage == "device.authentication"
        assert not calls


@pytest.mark.parametrize("reason", ["unauthorized", "protocolMismatch"])
@pytest.mark.parametrize("source", ["device", "local"])
async def test_rejection_is_received_in_band_with_source_specific_guidance(
    tmp_path, monkeypatch, reason, source
):
    gateway = Gateway(tmp_path / "gateway")
    gateway.credentials["valid"] = "pair"
    received = []
    receive = Peer.receive

    async def observed(peer):
        value = await receive(peer)
        received.append(value)
        return value

    monkeypatch.setattr(Peer, "receive", observed)
    if source == "local":

        async def opening(connection, request):
            assert request.path == "/ipc"

        monkeypatch.setattr(gateway, "opening", opening)
    if reason == "protocolMismatch":
        monkeypatch.setattr(transport, "VERSION", "other-release")
    async with gateway.listening():
        with pytest.raises(Fault) as caught:
            await Peer.open(
                {
                    **gateway.identity,
                    "credential": "wrong" if reason == "unauthorized" else "valid",
                    "endpoint": gateway.identity["endpoint"].replace(
                        "/androidctl", "/ipc" if source == "local" else "/androidctl"
                    ),
                },
                source=source,
            )
        assert caught.value.problem.code == reason
        assert caught.value.problem.stage == f"{source}.authentication"
        if reason == "protocolMismatch" and source == "device":
            assert "CLI release other-release" in str(caught.value)
            assert "obtain the same release APK" in str(caught.value)
        else:
            assert "androidctl apk" not in str(caught.value)
        assert received[0]["event"] == "rejected"
        assert received[0]["code"] == reason
        assert not gateway.registers


@pytest.mark.parametrize("source", ["device", "local"])
async def test_connected_version_mismatch_guidance_does_not_dispatch(
    tmp_path, monkeypatch, source
):
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return completed(method).wire()

    gateway = Gateway(tmp_path / "gateway", dispatch=dispatch)
    gateway.credentials["valid"] = "pair"
    reply = gateway.reply

    async def wrong_version(connection, value):
        await reply(connection, {**value, "version": "other-release"})

    monkeypatch.setattr(gateway, "reply", wrong_version)
    if source == "local":

        async def opening(connection, request):
            assert request.path == "/ipc"

        monkeypatch.setattr(gateway, "opening", opening)
    async with gateway.listening():
        with pytest.raises(Fault) as caught:
            await Peer.open(
                {
                    **gateway.identity,
                    "credential": "valid",
                    "endpoint": gateway.identity["endpoint"].replace(
                        "/androidctl", "/ipc" if source == "local" else "/androidctl"
                    ),
                },
                source=source,
            )
        assert caught.value.problem.code == "protocolMismatch"
        assert caught.value.problem.stage == f"{source}.authentication"
        if source == "device":
            assert f"CLI release {VERSION}" in str(caught.value)
            assert "obtain the same release APK" in str(caught.value)
        else:
            assert "androidctl apk" not in str(caught.value)
        assert not calls


async def test_real_tls_authentication_and_validated_reply(tmp_path):
    root = tmp_path / "state"
    private_directory(root)
    coordinator = Coordinator(HostState(root), instance="fixture")
    async with local_server(
        root,
        coordinator.dispatch,
        credential="credential",
        stopped=asyncio.Event(),
    ) as (saved, _):
        try:
            with pytest.raises(Fault):
                await Peer.open({**saved, "credential": "wrong"}, source="local")
            wrong = {
                **saved,
                "certificate": base64.b64encode(b"not a certificate").decode(),
            }
            with pytest.raises(Fault):
                await Peer.open(wrong, source="local")
            peer = await Peer.open(saved, source="local")
            try:
                result = await peer.request("devices", deadline=time.monotonic() + 10)
                assert result["result"] == "done"
                assert result["data"]["devices"] == []
                assert json.loads(json.dumps(result)) == result
            finally:
                await peer.close()
        finally:
            await coordinator.close()
