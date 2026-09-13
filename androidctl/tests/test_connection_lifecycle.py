import asyncio
import json
import socket
import time

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from androidctl import transport
from androidctl.identity import local_identity
from androidctl.private_files import private_directory
from androidctl.protocol import VERSION, Fault, completed
from androidctl.transport import Peer
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
