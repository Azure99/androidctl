import asyncio
import uuid
from contextlib import asynccontextmanager

from androidctl.identity import local_identity
from androidctl.local_server import LocalServer
from androidctl.private_files import private_directory


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
