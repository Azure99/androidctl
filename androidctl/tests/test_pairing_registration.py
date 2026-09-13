import asyncio
import json
import time
import uuid
from http import HTTPStatus
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from typer.testing import CliRunner
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from androidctl import app as cli
from androidctl import coordinator as coordinator_module
from androidctl import local_call, transport
from androidctl.coordinator import Coordinator
from androidctl.private_files import (
    PublishedWriteError,
    private_directory,
)
from androidctl.protocol import (
    VERSION,
    Fault,
    TransportFailure,
)
from androidctl.state import HostState
from androidctl.transport import Peer
from certificate_test_support import certificate, tls_context
from connection_test_support import local_server
from pairing_test_support import Gateway


@pytest.mark.parametrize("as_json", [False, True])
async def test_pair_reports_device_uuid_through_cli(tmp_path, monkeypatch, as_json):
    gateway = Gateway(tmp_path / "device")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="pair-cli")
    try:
        async with (
            gateway.listening(),
            local_server(tmp_path / "ipc", coordinator.dispatch) as (material, _),
        ):

            async def connect(*, deadline, start=True):
                return await Peer.open(material, source="local", deadline=deadline)

            monkeypatch.setattr(local_call, "connect_local", connect)
            address = urlsplit(gateway.identity["endpoint"]).netloc
            args = (["--json"] if as_json else []) + [
                "pair",
                address,
                "000042",
                "--name",
                "phone",
            ]
            response = await asyncio.to_thread(CliRunner().invoke, cli.app, args)
            assert response.exit_code == 0, response.output
            identifier = gateway.identity["deviceId"]
            if as_json:
                result = json.loads(response.stdout)
                assert result["command"] == "pair"
                assert result["result"] == "done"
                assert result["data"]["deviceId"] == identifier
                assert result["data"]["activated"] is True
            else:
                assert "pair: done" in response.stdout
                assert f"deviceId={identifier}" in response.stdout
            assert state.resolve_selection("phone")[0].device_id == identifier
            assert len(gateway.registers) == 1
    finally:
        await coordinator.close()
    assert all(connection.state.name == "CLOSED" for connection in gateway.connections)


async def test_pair_uses_one_budget_for_registration_and_confirmation(
    tmp_path, monkeypatch
):
    gateway = Gateway(tmp_path / "identity")
    tls, identity = gateway.tls, gateway.identity
    requests, connections = [], []
    confirmation = asyncio.Event()

    async def handle(connection):
        connections.append(connection)
        pairing = bool(connection.request.headers.get("X-AndroidCtl-Pairing"))
        try:
            if pairing:
                await asyncio.sleep(0.08)
            else:
                await confirmation.wait()
            await connection.send(
                json.dumps(
                    {
                        "event": "connected",
                        "version": VERSION,
                        "deviceId": identity["deviceId"],
                        "name": identity["name"],
                        "pairingId": "paired",
                        "blockId": "hello",
                    }
                )
            )
            async for message in connection:
                request = json.loads(message)
                if "ack" in request:
                    continue
                requests.append(request)
                await asyncio.sleep(0.08)
                await connection.send(
                    json.dumps(
                        {
                            "id": request["id"],
                            "result": {"pairingId": "paired"},
                            "blockId": "registered",
                        }
                    )
                )
        except ConnectionClosed:
            pass

    async with serve(handle, "127.0.0.1", 0, ssl=tls) as server:
        material = {
            **identity,
            "endpoint": f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl",
        }
        private_directory(tmp_path / "host")
        coordinator = Coordinator(HostState(tmp_path / "host"), instance="fixture")
        try:
            result = await coordinator.pair(
                {
                    "endpoint": material["endpoint"],
                    "code": "000042",
                    "name": "device",
                    "timeoutMs": 1000,
                },
                deadline=time.monotonic() + 1,
            )
            assert result.result == "failed"
            assert result.data["deviceId"] == identity["deviceId"]
            assert result.data["registered"] is True
            assert result.data["confirmed"] is False
            assert not confirmation.is_set()
            await asyncio.wait_for(connections[-1].wait_closed(), 3)
            assert len(requests) == 1
            assert requests[0]["method"] == "pair.register"
            assert 0 < requests[0]["args"]["timeoutMs"] < 1000
            confirmation.set()
            resumed = await coordinator.pair(
                {
                    "endpoint": material["endpoint"],
                    "code": "000042",
                    "name": "device",
                    "timeoutMs": 1000,
                },
                deadline=time.monotonic() + 1,
            )
            assert resumed.result == "done"
            assert resumed.data["deviceId"] == identity["deviceId"]
            assert len(requests) == 1
        finally:
            confirmation.set()
            await coordinator.close()
    assert not server.connections


@pytest.mark.parametrize("failure", ["presend", "remote", "lost"])
async def test_registration_failure_reuses_candidate_after_authentication(
    tmp_path, monkeypatch, failure
):
    private_directory(tmp_path / "tls")
    device_id = str(uuid.uuid4())
    name = f"{device_id}.androidctl.local"
    key = ec.generate_private_key(ec.SECP256R1())
    pem, der = certificate(key, name)
    tls = tls_context(tmp_path / "tls", key, pem)
    identity = {"deviceId": device_id, "name": name, "certificate": der}
    requests, modes = [], []
    credential = None

    async def opening(connection, request):
        pairing = bool(request.headers.get("X-AndroidCtl-Pairing"))
        modes.append("register" if pairing else "confirm")
        if not pairing and request.headers.get("Authorization") != "Bearer " + str(
            credential
        ):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "not registered")
        return None

    async def connected(connection):
        nonlocal credential
        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "connected",
                        "deviceId": device_id,
                        "name": name,
                        "version": VERSION,
                        "pairingId": "paired",
                        "blockId": "welcome",
                    }
                )
            )
            async for message in connection:
                request = json.loads(message)
                if "ack" in request:
                    continue
                requests.append(request)
                assert request["method"] == "pair.register"
                if failure == "remote" and len(requests) == 1:
                    response = {
                        "id": request["id"],
                        "error": {
                            "code": "pairingExpired",
                            "message": "window expired",
                        },
                    }
                else:
                    credential = request["args"]["credential"]
                    if failure == "lost":
                        await connection.close()
                        return
                    response = {
                        "id": request["id"],
                        "result": {"pairingId": "paired"},
                    }
                await connection.send(json.dumps({**response, "blockId": "registered"}))
        except ConnectionClosed:
            pass

    original = Peer.request_body
    inject = failure == "presend"

    def body(peer, method, *args, **kwargs):
        nonlocal inject
        if inject and method == "pair.register":
            inject = False
            raise Fault("timeout", "Injected before send")
        return original(peer, method, *args, **kwargs)

    monkeypatch.setattr(Peer, "request_body", body)
    async with serve(
        connected, "127.0.0.1", 0, ssl=tls, process_request=opening
    ) as server:
        private_directory(tmp_path / "host")
        state = HostState(tmp_path / "host")
        coordinator = Coordinator(state, instance="test")
        supplied = {
            **identity,
            "endpoint": f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl",
        }
        args = {
            "endpoint": supplied["endpoint"],
            "code": "000042",
            "name": "phone",
            "timeoutMs": 20000,
        }
        try:
            expected = {
                "presend": Fault,
                "remote": Fault,
                "lost": TransportFailure,
            }[failure]
            with pytest.raises(expected) as caught:
                await coordinator.pair(
                    args, deadline=time.monotonic() + args["timeoutMs"] / 1000
                )
            candidate = state.record(device_id).candidate
            assert (
                caught.value.problem.code
                == {
                    "presend": "timeout",
                    "remote": "pairingExpired",
                    "lost": "replyUnavailable",
                }[failure]
            )
            with pytest.raises(Fault, match="unconfirmed"):
                await coordinator.device("phone")
            assert len(requests) == (0 if failure == "presend" else 1)
            assert (
                await coordinator.pair(
                    args, deadline=time.monotonic() + args["timeoutMs"] / 1000
                )
            ).result == "done"
            assert len(requests) == (2 if failure == "remote" else 1)
            assert all(
                request["args"]["credential"] == candidate.credential
                for request in requests
            )
            assert (
                state.resolve_selection("phone")[0].active.credential
                == candidate.credential
            )
            assert modes == (
                ["register", "confirm", "register", "confirm"]
                if failure != "lost"
                else ["register", "confirm"]
            )
        finally:
            await coordinator.close()


@pytest.mark.parametrize("published", [False, True])
async def test_zero_register_configuration_failure_is_not_unknown_over_real_ipc(
    tmp_path, monkeypatch, published
):
    gateway = Gateway(tmp_path / "device")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="test")
    original = state.save_record

    def save(record):
        if published:
            original(record)
            raise PublishedWriteError("directory synchronization injected")
        raise OSError("configuration write injected")

    monkeypatch.setattr(state, "save_record", save)
    async with local_server(
        state.root,
        coordinator.dispatch,
        credential="ipc",
        stopped=asyncio.Event(),
    ) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            async with gateway.listening():
                with pytest.raises(Fault) as caught:
                    await peer.request(
                        "pair", gateway.args(), deadline=time.monotonic() + 10
                    )
                assert caught.value.problem.code == "localConfiguration"
                assert caught.value.problem.stage == "local.configuration"
                assert not isinstance(caught.value, TransportFailure)
                assert gateway.registers == []
                if published:
                    candidate = state.record(gateway.identity["deviceId"]).candidate
                    assert candidate is not None
                    monkeypatch.setattr(state, "save_record", original)
                    result = await peer.request(
                        "pair", gateway.args(), deadline=time.monotonic() + 10
                    )
                    assert result["result"] == "done"
                    assert gateway.headers == [
                        ("000042", ""),
                        (None, "Bearer " + candidate.credential),
                        ("000042", ""),
                        (None, "Bearer " + candidate.credential),
                    ]
                    assert len(gateway.registers) == 1
                else:
                    assert not list(state.pairings.iterdir())
        finally:
            await peer.close()
            await coordinator.close()


async def test_pair_uses_one_deadline_and_late_configuration_does_not_register(
    tmp_path, monkeypatch
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        coordinator = Coordinator(state, instance="test")
        original = state.save_record
        clock = [time.monotonic()]
        expired = clock[0] + 6
        saved = []
        monkeypatch.setattr(
            coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock[0])
        )
        monkeypatch.setattr(
            transport, "time", SimpleNamespace(monotonic=lambda: clock[0])
        )

        def slow_save(record):
            assert record.device_id in coordinator.changing
            original(record)
            saved.append(record.device_id)
            clock[0] = expired

        monkeypatch.setattr(state, "save_record", slow_save)
        try:
            with pytest.raises(Fault):
                await coordinator.pair(
                    gateway.args(timeoutMs=5000), deadline=clock[0] + 5
                )
            assert saved
            assert set(saved) == {gateway.identity["deviceId"]}
            assert gateway.registers == []
            assert not coordinator.changing
            assert not coordinator.io_task
            assert state.record(gateway.identity["deviceId"]).candidate is not None
        finally:
            await coordinator.close()
