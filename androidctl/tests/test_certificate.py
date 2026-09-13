import asyncio
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from websockets.asyncio.server import ServerConnection, serve

from androidctl import transport
from androidctl.identity import trust_context, trusted_certificate
from androidctl.private_files import private_directory
from androidctl.protocol import VERSION, Fault
from androidctl.transport import Peer, encode_message, send
from certificate_test_support import certificate, tls_context
from pairing_test_support import Gateway


async def test_leaf_pin_is_checked_before_any_websocket_credentials(
    tmp_path, monkeypatch
):
    device_id = str(uuid.uuid4())
    private_directory(tmp_path / "cert")
    key = ec.generate_private_key(ec.SECP256R1())
    _, old = certificate(key)
    pem, new = certificate(key)
    tls = tls_context(tmp_path / "cert", key, pem)
    requests = []

    async def opening(connection, request):
        requests.append(dict(request.headers))

    async def connected(connection):
        await send(
            connection,
            encode_message(
                {
                    "event": "connected",
                    "version": VERSION,
                    "pairingId": "original",
                    "deviceId": device_id,
                    "name": "phone.androidctl.local",
                }
            ),
        )
        await connection.wait_closed()

    async with serve(
        connected, "127.0.0.1", 0, ssl=tls, process_request=opening
    ) as server:
        material = {
            "deviceId": device_id,
            "name": "phone.androidctl.local",
            "certificate": old,
            "credential": "original-secret",
            "endpoint": f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl",
        }
        # Accept the new chain to isolate exact-leaf validation before headers.
        monkeypatch.setattr(transport, "trust_context", lambda _: trust_context(new))
        with pytest.raises(Fault, match="certificate changed"):
            await Peer.open(material)
        assert requests == []
        peer = await Peer.open({**material, "certificate": new})
        assert peer.welcome["pairingId"] == "original"
        await peer.close()
        assert len(requests) == 1
        assert requests[0]["authorization"] == "Bearer original-secret"


async def test_discovery_cancel_before_http_closes_the_tls_connection(tmp_path):
    gateway = Gateway(tmp_path / "tls")
    raw_connections, requests = [], []
    inspected = asyncio.Event()

    class Captured(ServerConnection):
        def connection_made(self, transport):
            super().connection_made(transport)
            raw_connections.append(self)

    async def opening(connection, request):
        requests.append(request)
        return await gateway.opening(connection, request)

    async def inspect(identity):
        assert identity.device_id == gateway.identity["deviceId"]
        assert identity.certificate == gateway.identity["certificate"]
        inspected.set()
        await asyncio.Event().wait()

    async with serve(
        gateway.connected,
        "127.0.0.1",
        0,
        ssl=gateway.tls,
        process_request=opening,
        create_connection=Captured,
    ) as server:
        address = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl"
        task = asyncio.create_task(
            Peer.discover(address, "000042", inspect, deadline=time.monotonic() + 3)
        )
        await asyncio.wait_for(inspected.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(
            asyncio.gather(
                *(connection.wait_closed() for connection in raw_connections)
            ),
            2,
        )
        assert not requests
        assert raw_connections
        assert all(connection.transport.is_closing() for connection in raw_connections)


@pytest.mark.parametrize("future", [False, True])
async def test_real_tls_certificate_time_errors_are_actionable_before_credentials(
    tmp_path, future
):
    key = ec.generate_private_key(ec.SECP256R1())
    pem, der = certificate(key, expired=not future, future=future)
    tls = tls_context(tmp_path, key, pem)
    requests = []

    async def opening(connection, request):
        requests.append(request)

    async def connected(connection):
        await connection.wait_closed()

    expected = "not yet valid" if future else "expired"
    with pytest.raises(Fault, match=expected):
        trusted_certificate(pem, "phone.androidctl.local")
    async with serve(
        connected, "127.0.0.1", 0, ssl=tls, process_request=opening
    ) as server:
        with pytest.raises(Fault, match=expected) as failure:
            await Peer.open(
                {
                    "name": "phone.androidctl.local",
                    "certificate": der,
                    "credential": "test",
                    "endpoint": f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl",
                }
            )
        assert failure.value.problem.stage == "device.tls"
        assert "check" in str(failure.value)
        assert "clock" in str(failure.value)
        assert not requests
