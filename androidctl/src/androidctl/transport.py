from __future__ import annotations

import asyncio
import base64
import json
import ssl
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar, cast, overload

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.connection import Connection
from websockets.datastructures import HeadersLike
from websockets.exceptions import InvalidStatus
from websockets.http11 import USER_AGENT
from websockets.protocol import State

from .identity import DeviceIdentity, device_certificate, endpoint, trust_context
from .protocol import (
    CONNECT_SECONDS,
    JSON_UTF8_BYTES,
    MAX_MESSAGE,
    PAIRING_NAME_UNITS,
    VERSION,
    Fault,
    Problem,
    TransportFailure,
)

T = TypeVar("T")
SEND_SECONDS = 5.0
CLOSE_SECONDS = 3.0
CERTIFICATE_NOT_YET_VALID = 9
CERTIFICATE_EXPIRED = 10


def encode_message(body: dict[str, Any]) -> str:
    value = json.dumps(body, ensure_ascii=False)
    if len(value.encode("utf-8")) > MAX_MESSAGE:
        raise Fault("messageLimit", "Request exceeds the message limit")
    return value


async def send(connection: Connection, value: str) -> None:
    try:
        await asyncio.wait_for(connection.send(value), SEND_SECONDS)
    except BaseException:
        connection.transport.abort()
        await connection.wait_closed()
        raise


async def close(connection: Connection, *, deadline: float | None = None) -> None:
    remaining = (
        CLOSE_SECONDS
        if deadline is None
        else min(CLOSE_SECONDS, deadline - time.monotonic())
    )
    try:
        if remaining <= 0:
            connection.transport.abort()
        else:
            await asyncio.wait_for(connection.close(), remaining)
    except TimeoutError:
        connection.transport.abort()
    except BaseException:
        connection.transport.abort()
        raise
    finally:
        await connection.wait_closed()


def connection_fault(
    error: BaseException,
    *,
    phase: str,
    source: Literal["local", "device"],
    pairing: bool,
) -> Fault:
    code = (
        "tlsIdentity"
        if isinstance(error, ssl.SSLCertVerificationError)
        else "connectionFailed"
    )
    if isinstance(error, ssl.SSLError):
        phase = "tls"
    message = (
        f"{phase} did not finish within the request budget"
        if isinstance(error, TimeoutError)
        else f"{type(error).__name__}: {error}"
    )
    if isinstance(error, ssl.SSLCertVerificationError):
        if error.verify_code == CERTIFICATE_NOT_YET_VALID:
            message += "; certificate not yet valid; check host and device clocks"
        elif error.verify_code == CERTIFICATE_EXPIRED:
            message += (
                "; certificate expired; check clocks, renew on the device, "
                "then import its trusted export with device trust"
            )
        else:
            message += (
                "; verify device identity and import a certificate "
                "obtained through a trusted path"
            )
    if isinstance(error, InvalidStatus) and error.response.status_code in (
        401,
        403,
    ):
        phase = "authentication"
        code = "unauthorized"
        message += (
            "; pairing window rejected; show a new code on the device"
            if pairing
            else "; pairing credential rejected; check the device pairing"
        )
    return Fault(code, message, f"{source}.{phase}")


class Peer:
    def __init__(
        self,
        connection: ClientConnection,
        *,
        source: Literal["local", "device"] = "device",
    ):
        self.connection = connection
        self.welcome: dict[str, Any] = {}
        self.source = source
        self.lock = asyncio.Lock()
        self.requests: dict[object, str] = {}
        self.ack_task: asyncio.Task[None] | None = None
        self.last_used = time.monotonic()

    @property
    def busy(self) -> bool:
        return bool(self.requests)

    @property
    def owner(self) -> str | None:
        return next(iter(self.requests.values()), None)

    @property
    def opened(self) -> bool:
        return self.connection.state == State.OPEN

    @classmethod
    async def open(
        cls,
        material: dict[str, Any],
        *,
        pairing_code: str | None = None,
        deadline: float | None = None,
        source: Literal["local", "device"] = "device",
    ) -> Peer:
        peer, _ = await cls._open(
            material, pairing_code=pairing_code, deadline=deadline, source=source
        )
        return peer

    @classmethod
    async def discover(
        cls,
        address: str,
        pairing_code: str,
        inspect_identity: Callable[[DeviceIdentity], Awaitable[None]],
        *,
        deadline: float,
    ) -> tuple[DeviceIdentity, Peer]:
        peer, identity = await cls._open(
            {"endpoint": address},
            pairing_code=pairing_code,
            inspect_identity=inspect_identity,
            deadline=deadline,
        )
        return cast(DeviceIdentity, identity), peer

    @classmethod
    async def _open(
        cls,
        material: dict[str, Any],
        *,
        pairing_code: str | None = None,
        inspect_identity: Callable[[DeviceIdentity], Awaitable[None]] | None = None,
        deadline: float | None = None,
        source: Literal["local", "device"] = "device",
    ) -> tuple[Peer, DeviceIdentity | None]:
        endpoint(material["endpoint"], source=source)
        deadline = min(
            deadline if deadline is not None else float("inf"),
            time.monotonic() + CONNECT_SECONDS,
        )
        phase = "connect"
        identity: DeviceIdentity | None = None
        captured: list[ClientConnection] = []

        class Client(ClientConnection):
            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                super().connection_made(transport)
                captured.append(self)

            async def handshake(
                self,
                additional_headers: HeadersLike | None = None,
                user_agent_header: str | None = USER_AGENT,
            ) -> None:
                nonlocal phase, identity
                tls = self.transport.get_extra_info("ssl_object")
                der = tls.getpeercert(binary_form=True) if tls is not None else None
                if inspect_identity is not None and der is not None:
                    identity = device_certificate(der)
                    # Resolve saved identity before sending a pairing code in HTTP.
                    await inspect_identity(identity)
                elif der is None or der != base64.b64decode(
                    material["certificate"], validate=True
                ):
                    raise Fault(
                        "tlsIdentity",
                        "Device certificate changed; import trusted certificate",
                        "tls",
                    )
                phase = "websocket"
                await super().handshake(additional_headers, user_agent_header)

        async def establish() -> Peer:
            nonlocal phase
            if inspect_identity is None:
                context = trust_context(material["certificate"])
            else:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            headers = {"X-AndroidCtl-Version": VERSION}
            if pairing_code is None:
                headers["Authorization"] = "Bearer " + material["credential"]
            else:
                headers["X-AndroidCtl-Pairing"] = pairing_code
            connection = await connect(
                material["endpoint"],
                ssl=context,
                server_hostname=material.get("name", ""),
                additional_headers=headers,
                proxy=None,
                compression=None,
                ping_interval=None,
                open_timeout=None,
                ssl_handshake_timeout=CONNECT_SECONDS,
                happy_eyeballs_delay=0.25,
                close_timeout=CLOSE_SECONDS,
                max_size=MAX_MESSAGE,
                max_queue=4,
                write_limit=32768,
                create_connection=Client,
            )
            phase = "authentication"
            peer = cls(connection, source=source)
            welcome = await peer.receive()
            await peer.acknowledge(welcome)
            mismatch = (
                f"CLI release {VERSION} requires the matching AndroidCtl App; "
                "use androidctl apk --out NEW_PATH or obtain the same release APK"
                if source == "device"
                else "Peer uses a different release contract"
            )
            if welcome.get("event") == "rejected":
                code = welcome.get("code", "unauthorized")
                message = (
                    mismatch
                    if code == "protocolMismatch"
                    else "Pairing credential was rejected; check device pairing"
                )
                raise Fault(code, message, "authentication")
            if welcome.get("version") != VERSION or welcome.get("event") != "connected":
                raise Fault("protocolMismatch", mismatch, phase)
            if source == "device" and (
                welcome.get("deviceId")
                != (
                    identity.device_id if identity is not None else material["deviceId"]
                )
                or welcome.get("name")
                != (identity.name if identity is not None else material["name"])
            ):
                raise Fault(
                    "tlsIdentity", "Welcome names a different device", "authentication"
                )
            peer.welcome = welcome
            return peer

        try:
            peer = await asyncio.wait_for(
                establish(), max(0, deadline - time.monotonic())
            )
            return peer, identity
        except BaseException as error:
            for connection in captured:
                connection.transport.abort()
                await connection.wait_closed()
            if isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise
            if isinstance(error, Fault):
                if error.problem.stage in {
                    None,
                    "connect",
                    "tls",
                    "websocket",
                    "authentication",
                }:
                    error.problem.stage = f"{source}.{error.problem.stage or phase}"
                raise
            raise connection_fault(
                error, phase=phase, source=source, pairing=pairing_code is not None
            ) from error

    async def receive(self) -> dict[str, Any]:
        body = await self.connection.recv()
        if not isinstance(body, str):
            raise Fault("protocol", "Expected a JSON text message", "websocket")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise Fault("protocol", "Expected a JSON object", "websocket")
        return value

    async def acknowledge(self, value: dict[str, Any]) -> None:
        if "blockId" in value:
            await send(self.connection, encode_message({"ack": value["blockId"]}))

    @overload
    async def request(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        session: str = "default",
        deadline: float,
        validate: Callable[[dict[str, Any]], T],
        wait_for_owner: bool = False,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> T: ...

    @overload
    async def request(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        session: str = "default",
        deadline: float,
        validate: None = None,
        wait_for_owner: bool = False,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def request(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        session: str = "default",
        deadline: float,
        validate: Callable[[dict[str, Any]], T] | None = None,
        wait_for_owner: bool = False,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> T | dict[str, Any]:
        ticket = object()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Fault("timeout", "Request budget exhausted before send")

        async def acquire() -> None:
            if self.busy and self.owner != session and not wait_for_owner:
                raise Fault(
                    "connectionBusy", f"Connection is occupied by session {self.owner}"
                )
            self.requests[ticket] = session
            try:
                await self.lock.acquire()
            except BaseException:
                self.leave(ticket, acquired=False)
                raise

        try:
            await asyncio.wait_for(acquire(), remaining)
        except TimeoutError as error:
            raise Fault(
                "connectionBusy", "Connection is occupied within this RPC budget"
            ) from error
        transferred_lock = False
        try:
            if not self.opened:
                raise Fault(
                    "connectionClosed",
                    "Connection closed before send; no request was sent",
                    f"{self.source}.websocket",
                )
            if before_send is not None:
                before_send()
            if exchange_seconds is not None:
                deadline = min(deadline, time.monotonic() + exchange_seconds)
            body = self.request_body(method, args, session=session)
            message = self.prepare_message(body, deadline)
            request_id = body["id"]
            try:

                async def exchange() -> dict[str, Any]:
                    await send(self.connection, message)
                    return await self.receive()

                response = await asyncio.wait_for(
                    exchange(), max(0, deadline - time.monotonic())
                )
                if response.get("id") != request_id:
                    raise Fault(
                        "protocol", "Reply does not match this request", "websocket"
                    )
                result = response.get("result")
                problem = (
                    Problem.model_validate(response["error"])
                    if "error" in response
                    else None
                )
                if problem is None:
                    if not isinstance(result, dict):
                        raise Fault("protocol", "Missing reply object", "websocket")
                    if validate is not None:
                        result = validate(result)
            except BaseException as error:
                await close(self.connection, deadline=deadline)
                if isinstance(
                    error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
                ):
                    raise
                raise TransportFailure(
                    "replyUnavailable",
                    (
                        "Reply was not received within the request budget"
                        if isinstance(error, TimeoutError)
                        else str(error) or type(error).__name__
                    ),
                    f"{self.source}.websocket",
                ) from error
            if "blockId" in response:
                # Forward the fact now; the device ACK still owns the slot.
                self.ack_task = asyncio.create_task(
                    self.finish_ack(response, deadline, ticket)
                )
                transferred_lock = True
            else:
                self.last_used = time.monotonic()
            if problem is not None:
                if response.get("uncertain"):
                    raise TransportFailure.from_problem(problem)
                raise Fault.from_problem(problem)
            return cast(T | dict[str, Any], result)
        finally:
            if not transferred_lock:
                self.leave(ticket, acquired=True)

    def prepare_message(self, body: dict[str, Any], deadline: float) -> str:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise Fault("timeout", "Request budget exhausted before send")
        if self.source == "local":
            body["budgetMs"] = remaining_ms
        else:
            parameters = body["args"]
            # Device slot waiting consumes the remaining device exchange budget.
            parameters["timeoutMs"] = min(
                parameters.get("timeoutMs", remaining_ms), remaining_ms
            )
        if self.source == "device":
            # Include the final budget and the gateway's added identity fields.
            # Pairing names need at most three UTF-8 bytes per UTF-16 unit.
            complete_request = {
                **body,
                "pairingId": "0" * 36,
                "pairingName": "\u0800" * PAIRING_NAME_UNITS,
                "deadlineMs": 2**63 - 1,
            }
            encoded = json.dumps(complete_request, ensure_ascii=False).replace(
                "/", r"\/"
            )
            if len(encoded.encode("utf-8")) > JSON_UTF8_BYTES:
                raise Fault(
                    "jsonLimit", "Complete request exceeds the JSON UTF-8 budget"
                )
        return encode_message(body)

    def leave(self, ticket: object, *, acquired: bool) -> None:
        del self.requests[ticket]
        if acquired:
            self.lock.release()

    async def finish_ack(
        self, response: dict[str, Any], deadline: float, ticket: object
    ) -> None:
        try:
            await asyncio.wait_for(
                self.acknowledge(response),
                max(0.0, deadline - time.monotonic()),
            )
        except BaseException:
            self.connection.transport.abort()
            await self.connection.wait_closed()
        finally:
            self.last_used = time.monotonic()
            self.leave(ticket, acquired=True)

    async def close(self, *, deadline: float | None = None) -> None:
        if self.ack_task is not None:
            if deadline is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self.ack_task),
                        max(0, deadline - time.monotonic()),
                    )
                except TimeoutError:
                    self.connection.transport.abort()
                    self.ack_task.cancel()
            await asyncio.gather(self.ack_task, return_exceptions=True)
        await close(self.connection, deadline=deadline)

    def request_body(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        session: str = "default",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "method": method,
            "args": dict(args or {}),
            "session": session,
        }
        return body
