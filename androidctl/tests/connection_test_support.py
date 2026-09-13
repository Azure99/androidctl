import asyncio
import uuid
from contextlib import asynccontextmanager

from androidctl.coordinator import Coordinator
from androidctl.identity import local_identity
from androidctl.local_server import LocalServer
from androidctl.private_files import private_directory
from androidctl.state import HostState
from androidctl.transport import Peer
from pairing_test_support import Gateway, save_active


@asynccontextmanager
async def local_server(directory, dispatch, *, credential="fixture", stopped=None):
    private_directory(directory)
    tls, identity = local_identity(directory)
    server = LocalServer(
        tls, credential, dispatch, stopped if stopped is not None else asyncio.Event()
    )
    server.start()
    try:
        yield (
            {
                **identity,
                "instance": uuid.uuid4().hex,
                "credential": credential,
                "endpoint": f"wss://127.0.0.1:{server.port}/ipc",
            },
            server,
        )
    finally:
        await server.stop()


@asynccontextmanager
async def connections(tmp_path, dispatch, *, welcome_release=None):
    device = Gateway(tmp_path / "gateway", dispatch=dispatch)
    device.welcome_release = welcome_release
    device.credentials["device"] = "pair"
    async with device.listening():
        private_directory(tmp_path / "host")
        state = HostState(tmp_path / "host")
        material = {
            **device.identity,
            "alias": "phone",
            "pairingId": "pair",
            "credential": "device",
        }
        save_active(state, material)
        coordinator = Coordinator(state, instance="fixture")
        async with local_server(
            tmp_path / "identity",
            coordinator.dispatch,
            credential="local",
            stopped=asyncio.Event(),
        ) as (local_material, _):

            async def connect(*, deadline, start=True):
                return await Peer.open(
                    local_material, deadline=deadline, source="local"
                )

            try:
                yield coordinator, material, connect
            finally:
                await coordinator.close()
    assert all(connection.state.name == "CLOSED" for connection in device.connections)
