from __future__ import annotations

import asyncio
import hmac
import json
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any, cast

from websockets.asyncio.server import Server, ServerConnection
from websockets.http11 import Request, Response
from websockets.server import ServerProtocol

from .protocol import (
    CONNECT_SECONDS,
    MAX_CONNECTIONS,
    MAX_MESSAGE,
    VERSION,
    Fault,
    TransportFailure,
)
from .transport import CLOSE_SECONDS, close, encode_message, send

IDLE_SECONDS = 60.0


class AcceptedServer(Server):
    accepting = True

    def is_serving(self) -> bool:
        return self.accepting


class LocalServer:
    def __init__(
        self,
        tls: ssl.SSLContext,
        credential: str,
        dispatch: Callable[[str, dict[str, Any], float], Awaitable[dict[str, Any]]],
        stopped: asyncio.Event,
    ):
        self.tls, self.credential, self.dispatch = tls, credential, dispatch
        self.stopped = stopped
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(MAX_CONNECTIONS)
        self.listener.setblocking(False)
        self.port = int(self.listener.getsockname()[1])
        self.tasks: set[asyncio.Task[None]] = set()
        self.connections: set[ServerConnection] = set()
        self.server = AcceptedServer(
            self.handle, process_request=self.authenticate, open_timeout=CONNECT_SECONDS
        )
        self.acceptor: asyncio.Task[None] | None = None

    def authenticate(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        if (
            request.path != "/ipc"
            or request.headers.get("X-AndroidCtl-Version") != VERSION
            or not hmac.compare_digest(
                request.headers.get("Authorization", ""), "Bearer " + self.credential
            )
        ):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    def start(self) -> None:
        self.acceptor = asyncio.create_task(self.accept())

    async def accept(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            raw, _ = await loop.sock_accept(self.listener)
            if not self.server.accepting or len(self.tasks) >= MAX_CONNECTIONS:
                raw.close()
                continue
            task = asyncio.create_task(self.adopt(raw))
            self.tasks.add(task)

    async def adopt(self, raw: socket.socket) -> None:
        connection: ServerConnection | None = None

        def factory() -> ServerConnection:
            nonlocal connection
            connection = ServerConnection(
                ServerProtocol(max_size=MAX_MESSAGE),
                self.server,
                ping_interval=None,
                close_timeout=CLOSE_SECONDS,
                max_queue=4,
                write_limit=32768,
            )
            self.connections.add(connection)
            return connection

        try:
            await asyncio.get_running_loop().connect_accepted_socket(
                factory, raw, ssl=self.tls, ssl_handshake_timeout=CONNECT_SECONDS
            )
            if connection is not None:
                await connection.wait_closed()
                handler = self.server.handlers.get(connection)
                if handler is not None:
                    await handler
        except OSError:
            raw.close()
        finally:
            if connection is not None:
                self.connections.discard(connection)
            self.tasks.discard(cast(asyncio.Task[None], asyncio.current_task()))

    async def handle(self, connection: ServerConnection) -> None:
        try:
            await send(
                connection, encode_message({"event": "connected", "version": VERSION})
            )
            while True:
                raw = await asyncio.wait_for(connection.recv(), IDLE_SECONDS)
                if not self.server.accepting:
                    return
                request = json.loads(raw)
                if not isinstance(request, dict) or not isinstance(
                    request.get("id"), str
                ):
                    raise Fault("protocol", "Invalid request")
                stopping = False
                try:
                    if not isinstance(request.get("method"), str) or not isinstance(
                        request.get("args", {}), dict
                    ):
                        raise Fault(
                            "invalidRequest", "Expected method string and args object"
                        )
                    if request["method"] == "daemon.stop" and len(self.tasks) > 1:
                        raise Fault(
                            "busy",
                            "Other local connections, replies or handshakes "
                            "are still active",
                        )
                    budget = request.get("budgetMs")
                    if type(budget) is not int or budget <= 0:
                        raise Fault(
                            "invalidRequest", "Expected positive integer budgetMs"
                        )
                    seconds = budget / 1000
                    # Leave the outer caller time to receive the dispatch fact.
                    deadline = time.monotonic() + seconds - min(0.1, seconds / 4)
                    result = await self.dispatch(
                        request["method"], request.get("args", {}), deadline
                    )
                    stopping = request["method"] == "daemon.stop"
                    if stopping:
                        self.server.accepting = False
                    response = {"id": request["id"], "result": result}
                except TransportFailure as error:
                    response = {
                        "id": request["id"],
                        "error": error.problem.wire(),
                        "uncertain": True,
                    }
                except Fault as error:
                    response = {"id": request["id"], "error": error.problem.wire()}
                except Exception as error:
                    response = {
                        "id": request["id"],
                        "error": {"code": "internalError", "message": str(error)},
                        "uncertain": True,
                    }
                try:
                    await send(connection, encode_message(response))
                finally:
                    if stopping:
                        self.stopped.set()
                if stopping:
                    return
        except (Exception, asyncio.CancelledError):
            await close(connection)

    async def stop(self) -> None:
        self.server.accepting = False
        if self.acceptor is not None:
            self.acceptor.cancel()
            await asyncio.gather(self.acceptor, return_exceptions=True)
        self.listener.close()
        closed = await asyncio.gather(
            *(
                close(connection)
                for connection in tuple(self.connections)
                if hasattr(connection, "transport")
            ),
            return_exceptions=True,
        )
        # Half-TLS connections finish at their own handshake deadline.
        adopted = await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        for result in (*closed, *adopted):
            if isinstance(result, Exception):
                raise result
