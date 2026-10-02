from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

from websockets.exceptions import ConnectionClosed

from .file_jobs import FileJobs
from .identity import DeviceIdentity, endpoint, trusted_certificate
from .private_files import PublishedWriteError, write_json
from .protocol import (
    UNCERTAIN_EXIT,
    VERSION,
    Fault,
    Result,
    TransportFailure,
    completed,
    is_local_transfer,
    rejected,
    uncertain,
)
from .state import (
    ActivePairing,
    HostState,
    PairingRecord,
    PairingSlot,
    require_active,
)
from .transport import Peer

T = TypeVar("T")
PeerRole = Literal["mutation", "query"]
PEER_IDLE_SECONDS = 45.0
QUERY_METHODS = frozenset(
    {
        "status",
        "ui.read",
        "job.get",
        "job.cancel",
        "output.read",
        "file.get",
        "file.cancel",
    }
)


def local_configuration(message: str) -> Fault:
    return Fault("localConfiguration", message, "local.configuration")


def connection_cleanup(result: Result, error: Exception) -> None:
    result.data["cleanupError"] = str(error)
    result.exit = UNCERTAIN_EXIT


def status_checks(
    failed_stage: Literal["localPairing", "deviceAuthentication", "workerStatus"],
    reason: str | None,
    authentication_detail: str | None = None,
) -> list[dict[str, str]]:
    checks = []
    failed = False
    for stage in ("localPairing", "deviceAuthentication", "workerStatus"):
        check = {"check": stage, "result": "not checked" if failed else "done"}
        if stage == failed_stage:
            check["result"] = "failed"
            if reason is not None:
                check["reason"] = reason
            failed = True
        elif stage == "deviceAuthentication" and authentication_detail is not None:
            check["detail"] = authentication_detail
        checks.append(check)
    return checks


@dataclass
class DeviceExchange:
    outcome: Result | Fault
    connect_ms: int | None
    authenticated: bool


class Device:
    def __init__(self, record: PairingRecord):
        self._record = record
        self._peers: dict[PeerRole, Peer] = {}
        self._connection_gates: dict[PeerRole, asyncio.Lock] = {
            "mutation": asyncio.Lock(),
            "query": asyncio.Lock(),
        }
        self._active_calls = 0

    @property
    def record(self) -> PairingRecord:
        return self._record

    @property
    def active(self) -> ActivePairing:
        return cast(ActivePairing, self.record.active)

    async def exchange(
        self,
        command: str,
        parameters: dict[str, Any],
        *,
        session: str,
        deadline: float,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> DeviceExchange:
        role: PeerRole = "query" if command in QUERY_METHODS else "mutation"
        connect_ms: int | None = None
        authenticated = False

        async def connect() -> Peer:
            nonlocal connect_ms
            async with self._connection_gates[role]:
                current = self._peers.get(role)
                if current is None or not current.opened:
                    started = time.monotonic()
                    try:
                        current = await Peer.open(
                            self.record.material(self.active), deadline=deadline
                        )
                    finally:
                        connect_ms = int((time.monotonic() - started) * 1000)
                    self._peers[role] = current
                current.last_used = time.monotonic()
                return current

        self._active_calls += 1
        try:
            try:
                peer = await asyncio.wait_for(
                    connect(), max(0, deadline - time.monotonic())
                )
            except TimeoutError as error:
                raise Fault(
                    "timeout",
                    "Device connection did not finish within the request budget",
                    "device.connect",
                ) from error
            authenticated = True
            outcome: Result | Fault = await peer.request(
                command,
                parameters,
                session=session,
                deadline=deadline,
                validate=Result.model_validate,
                wait_for_owner=command in QUERY_METHODS or exchange_seconds is not None,
                before_send=before_send,
                exchange_seconds=exchange_seconds,
            )
        except Fault as error:
            outcome = error
        finally:
            self._active_calls -= 1
        return DeviceExchange(outcome, connect_ms, authenticated)

    async def request(
        self,
        command: str,
        parameters: dict[str, Any],
        *,
        session: str,
        deadline: float,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> Result:
        if time.monotonic() >= deadline:
            exchange = DeviceExchange(
                Fault("timeout", "Request budget exhausted before device dispatch"),
                None,
                False,
            )
        else:
            exchange = await self.exchange(
                command,
                parameters,
                session=session,
                deadline=deadline,
                before_send=before_send,
                exchange_seconds=exchange_seconds,
            )
        outcome = exchange.outcome
        timings = (
            {"deviceConnectMs": exchange.connect_ms}
            if exchange.connect_ms is not None
            else {}
        )
        if isinstance(outcome, TransportFailure):
            return uncertain(command, outcome, timings)
        if isinstance(outcome, Fault):
            return Result(
                command=command,
                result="not sent",
                reason=str(outcome),
                error=outcome.problem,
                data=timings,
            )
        outcome.data.update(timings)
        return outcome

    def retain_transfer(self) -> None:
        self._active_calls += 1

    def release_transfer(self) -> None:
        self._active_calls -= 1

    async def replace_record(self, record: PairingRecord) -> None:
        self._record = record
        await self.close()

    async def expire_connections(self) -> None:
        for role, gate in self._connection_gates.items():
            if gate.locked():
                continue
            async with gate:
                peer = self._peers.get(role)
                if (
                    peer is not None
                    and not peer.busy
                    and time.monotonic() - peer.last_used >= PEER_IDLE_SECONDS
                ):
                    del self._peers[role]
                    await peer.close()

    async def close(self, *, deadline: float | None = None) -> None:
        peers, self._peers = self._peers, {}
        await asyncio.gather(
            *(peer.close(deadline=deadline) for peer in peers.values())
        )

    def connected(self) -> bool:
        return any(peer.opened for peer in self._peers.values())

    def busy(self) -> bool:
        return any(peer.busy for peer in self._peers.values())

    def in_use(self) -> bool:
        return bool(self._active_calls) or self.busy()


class Coordinator:
    def __init__(self, state: HostState, *, instance: str):
        self.state, self.instance = state, instance
        self.devices: dict[str, Device] = {}
        self.tasks: set[asyncio.Task[Result]] = set()
        self.io_task: asyncio.Task[Any] | None = None
        self.io_lock = asyncio.Lock()
        self.changing: set[str] = set()
        self.files = FileJobs()

    async def io(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        await self.io_lock.acquire()
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self.io_task = task

        def ended(finished: asyncio.Task[T]) -> None:
            self.io_task = None
            self.io_lock.release()
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(ended)
        return await asyncio.shield(task)

    async def device(self, identifier: str | None) -> tuple[Device, str]:
        try:
            material, source = await self.io(self.state.resolve_selection, identifier)
            require_active(material)
        except OSError as error:
            raise local_configuration(str(error)) from error
        key = material.device_id
        if key in self.changing:
            raise Fault("busy", "Device connection is being changed")
        if key not in self.devices:
            self.devices[key] = Device(material)
        return self.devices[key], source

    async def discover_pairing(
        self, args: dict[str, Any], deadline: float
    ) -> tuple[DeviceIdentity, Peer | None]:
        record = await self.io(self.state.find_pairing_for_recovery, args["name"])
        if record is None:
            known = Fault("knownDevice", "Use the stored device identity")

            async def discover(value: DeviceIdentity) -> None:
                nonlocal record
                try:
                    record = await self.io(self.state.record, value.device_id)
                except Fault as error:
                    if error.problem.code == "deviceNotFound":
                        return
                    raise
                if record.certificate != value.certificate:
                    raise Fault(
                        "tlsIdentity",
                        "Device certificate changed; import trusted certificate",
                        "tls",
                    )
                raise known

            try:
                return await Peer.discover(
                    args["endpoint"],
                    args["code"],
                    discover,
                    deadline=deadline,
                )
            except Fault as error:
                if error is not known:
                    raise
        return cast(PairingRecord, record).identity, None

    async def register_candidate(
        self,
        record: PairingRecord,
        peer: Peer | None,
        code: str,
        deadline: float,
    ) -> None:
        candidate = cast(PairingSlot, record.candidate)
        if peer is None:
            peer = await Peer.open(
                record.material(candidate), pairing_code=code, deadline=deadline
            )
        try:
            await peer.request(
                "pair.register",
                {"credential": candidate.credential, "name": socket.gethostname()},
                deadline=deadline,
            )
        finally:
            await peer.close(deadline=deadline)

    async def confirm_pairing(
        self, record: PairingRecord, peer: Peer, deadline: float
    ) -> ActivePairing:
        saved = cast(PairingSlot, record.candidate)
        try:
            pairing_id = peer.welcome.get("pairingId")
            if not isinstance(pairing_id, str) or not pairing_id:
                raise Fault(
                    "pairingUnconfirmed", "Peer did not confirm the saved credential"
                )
            return ActivePairing.model_construct(
                alias=saved.alias,
                endpoint=saved.endpoint,
                credential=saved.credential,
                pairing_id=pairing_id,
            )
        finally:
            await peer.close(deadline=deadline)

    async def activate_pairing(
        self,
        record: PairingRecord,
        active: ActivePairing,
        old: Device | None,
        deadline: float,
    ) -> Result:
        result = completed(
            "pair",
            {
                "device": active.alias,
                "deviceId": record.device_id,
                "registered": True,
                "confirmed": True,
                "activated": False,
            },
        )
        record.active, record.candidate = active, None
        try:
            await self.io(self.state.save_record, record)
        except OSError as error:
            result.error = local_configuration(str(error)).problem
            if not isinstance(error, PublishedWriteError):
                result.result = "failed"
                result.reason = (
                    "Credential confirmed but not activated; "
                    "run pair again to confirm the saved credential"
                )
                return result
            result.exit = UNCERTAIN_EXIT
            result.reason = (
                "Credential activated; final file synchronization or cleanup failed; "
                "do not register again"
            )
        result.data["activated"] = True
        self.devices[record.device_id] = Device(record)
        if old is not None:
            try:
                await old.close(deadline=deadline)
            except (OSError, Fault, ConnectionClosed) as error:
                connection_cleanup(result, error)
        return result

    async def pair(self, args: dict[str, Any], deadline: float) -> Result:
        identity, peer = await self.discover_pairing(args, deadline)
        key = identity.device_id
        old = self.devices.get(key)
        if key in self.changing or (old is not None and old.in_use()):
            if peer is not None:
                await peer.close(deadline=deadline)
            raise Fault("busy", "Finish device requests before changing its pairing")
        self.changing.add(key)
        try:
            try:
                record, candidate_created = await self.io(
                    self.state.prepare_pairing, identity, args["endpoint"], args["name"]
                )
            except OSError as error:
                raise local_configuration(f"Registration not sent: {error}") from error
            saved = cast(PairingSlot, record.candidate)
            if not candidate_created:
                try:
                    confirmation = await Peer.open(
                        record.material(saved), deadline=deadline
                    )
                    confirmed = await self.confirm_pairing(
                        record, confirmation, deadline
                    )
                except Fault as error:
                    if (error.problem.code, error.problem.stage) != (
                        "unauthorized",
                        "device.authentication",
                    ):
                        raise
                else:
                    return await self.activate_pairing(record, confirmed, old, deadline)
            registration_peer, peer = peer, None
            await self.register_candidate(
                record, registration_peer, args["code"], deadline
            )
            try:
                confirmation = await Peer.open(
                    record.material(saved), deadline=deadline
                )
            except Fault as error:
                return Result(
                    command="pair",
                    result="failed",
                    reason=(
                        "Device registered this credential, but confirmation failed; "
                        "run pair again; the saved credential is checked first"
                    ),
                    error=error.problem,
                    data={
                        "device": saved.alias,
                        "deviceId": record.device_id,
                        "registered": True,
                        "confirmed": False,
                    },
                )
            active = await self.confirm_pairing(record, confirmation, deadline)
            return await self.activate_pairing(record, active, old, deadline)
        finally:
            try:
                if peer is not None:
                    await peer.close(deadline=deadline)
            finally:
                self.changing.discard(key)

    async def remote(
        self,
        args: dict[str, Any],
        *,
        deadline: float,
    ) -> Result:
        command = args["command"]
        parameters = dict(args.get("args", {}))
        try:
            device, source = await asyncio.wait_for(
                self.device(args.get("device")),
                max(0, deadline - time.monotonic()),
            )
        except TimeoutError as error:
            raise Fault(
                "timeout", "Local pairing lookup exceeded the request budget"
            ) from error
        except Fault as error:
            if command != "status":
                raise
            return Result(
                command=command,
                result="failed",
                reason=str(error),
                error=error.problem,
                data={"checks": status_checks("localPairing", str(error))},
            )
        if time.monotonic() >= deadline:
            raise Fault("timeout", "Request budget exhausted before device dispatch")
        if command == "file.start":
            device.retain_transfer()
            try:
                result = self.files.start(
                    device.record.device_id,
                    device.active.pairing_id,
                    args.get("session", "default"),
                    parameters,
                    device.release_transfer,
                    remote=device.request,
                )
            except BaseException:
                device.release_transfer()
                raise
        elif command in ("job.get", "job.cancel") and is_local_transfer(
            parameters.get("job", "")
        ):
            result = self.local_file(device, args, parameters)
        elif command == "status":
            result = await self.status(device, args, deadline)
        else:
            query = parameters.pop("query", "") if command == "apps" else ""
            result = await device.request(
                command,
                parameters,
                session=args.get("session", "default"),
                deadline=deadline,
            )
            if command == "apps" and result.result == "done":
                query = query.casefold()
                result.data["apps"] = [
                    item
                    for item in result.data["apps"]
                    if query in item["name"].casefold()
                    or query in item["package"].casefold()
                ]
        result.data["context"] = {
            "deviceId": device.record.device_id,
            "deviceSource": source,
        }
        return result

    async def status(
        self, device: Device, args: dict[str, Any], deadline: float
    ) -> Result:
        exchange = await device.exchange(
            "status",
            dict(args.get("args", {})),
            session=args.get("session", "default"),
            deadline=deadline,
        )
        timings = (
            {"deviceConnectMs": exchange.connect_ms}
            if exchange.connect_ms is not None
            else {}
        )
        authentication_detail = None
        if exchange.authenticated:
            authentication_detail = (
                "new connection"
                if exchange.connect_ms is not None
                else "reused authenticated connection"
            )
        outcome = exchange.outcome
        if isinstance(outcome, Fault):
            return Result(
                command="status",
                result="failed",
                reason=str(outcome),
                error=outcome.problem,
                data={
                    **timings,
                    "checks": status_checks(
                        "workerStatus"
                        if exchange.authenticated
                        else "deviceAuthentication",
                        str(outcome),
                        authentication_detail,
                    ),
                },
            )
        outcome.data.update(timings)
        if outcome.result != "done":
            outcome.data["checks"] = status_checks(
                "workerStatus", None, authentication_detail
            )
        outcome.data["device"] = device.active.alias
        return outcome

    def local_file(
        self,
        device: Device,
        args: dict[str, Any],
        parameters: dict[str, Any],
    ) -> Result:
        job = self.files.find(
            parameters["job"],
            device.record.device_id,
            device.active.pairing_id,
            args.get("session", "default"),
        )
        if args["command"] == "job.cancel":
            job.request_stop()
        return job.view()

    async def address(self, args: dict[str, Any]) -> Result:
        endpoint(args["endpoint"])
        return await self.update_connection(args, "endpoint", args["endpoint"])

    async def forget(self, identifier: str) -> Result:
        key = await self.io(self.state.resolve_forget_key, identifier)
        device = self.devices.get(key)
        if (
            key in self.changing
            or device is not None
            and device.in_use()
            or any(
                job.device == key and not job.ended for job in self.files.items.values()
            )
        ):
            raise Fault(
                "busy", "Finish local device requests and transfers before forgetting"
            )
        self.changing.add(key)
        try:
            if device is not None:
                await device.close()
            try:
                data = await self.io(self.state.forget, key)
            except OSError as error:
                return rejected("device forget", local_configuration(str(error)))
            self.devices.pop(key, None)
            data["discardedJobFacts"] = self.files.discard_device(key)
            return Result(
                command="device forget",
                result="failed" if data.get("selectionError") else "done",
                reason="Local pairing removed; device jobs were not checked or "
                "stopped; device credentials were not revoked",
                data=data,
            )
        finally:
            self.changing.discard(key)

    async def trust(self, args: dict[str, Any]) -> Result:
        return await self.update_connection(args, "certificate", args["pem"])

    async def update_connection(
        self,
        args: dict[str, Any],
        field: Literal["endpoint", "certificate"],
        value: str,
    ) -> Result:
        record, _ = await self.io(self.state.resolve_selection, args.get("device"))
        key = record.device_id
        device = self.devices.get(key)
        if key in self.changing or device is not None and device.in_use():
            raise Fault("busy", "Finish device requests before changing its connection")
        if field == "certificate":
            value = trusted_certificate(value, record.name)
        result = completed(
            "device trust" if field == "certificate" else "device address",
            {"context": {"deviceId": key, "deviceSource": "args"}},
        )
        self.changing.add(key)
        try:
            if field == "certificate":
                record.certificate = value
            else:
                for slot in (record.active, record.candidate):
                    if slot is not None:
                        slot.endpoint = value
            try:
                await self.io(self.state.save_record, record)
            except PublishedWriteError as error:
                result.error = local_configuration(str(error)).problem
                result.reason = (
                    "Configuration updated; final file synchronization "
                    "or cleanup failed"
                )
                result.exit = UNCERTAIN_EXIT
            except OSError as error:
                raise local_configuration(str(error)) from error
            if device is not None:
                try:
                    await device.replace_record(record)
                except (OSError, Fault, ConnectionClosed) as error:
                    connection_cleanup(result, error)
        finally:
            self.changing.discard(key)
        return result

    async def dispatch(
        self, method: str, args: dict[str, Any], deadline: float
    ) -> dict[str, Any]:
        if method == "daemon.stop":
            if self.active_work():
                raise Fault(
                    "busy", "Active requests or I/O must finish before stopping"
                )
            return completed("daemon stop", {"instance": self.instance}).wire()
        task = asyncio.create_task(self.run(method, args, deadline))
        self.tasks.add(task)

        def ended(finished: asyncio.Task[Result]) -> None:
            self.tasks.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(ended)
        return (await asyncio.shield(task)).wire()

    async def run(self, method: str, args: dict[str, Any], deadline: float) -> Result:
        if method == "call":
            return await self.remote(args, deadline=deadline)
        if method == "pair":
            return await self.pair(args, deadline)
        if method == "device.address":
            return await self.address(args)
        if method == "device.trust":
            return await self.trust(args)
        if method == "device.forget":
            result = await self.forget(args["device"])
            if "forgotten" in result.data:
                result.data["context"] = {
                    "deviceId": result.data["forgotten"],
                    "deviceSource": "args",
                }
            return result
        if method == "device.select":

            def select() -> Result:
                selected, _ = self.state.resolve_selection(args["device"])
                require_active(selected)
                result = completed(
                    "device select",
                    {
                        "context": {
                            "deviceId": selected.device_id,
                            "deviceSource": "args",
                        }
                    },
                    reason=selected.current_slot.alias,
                )
                try:
                    write_json(self.state.selection, {"deviceId": selected.device_id})
                except PublishedWriteError as error:
                    result.error = local_configuration(
                        "Selection saved; final file synchronization "
                        f"or cleanup failed: {error}"
                    ).problem
                    result.exit = UNCERTAIN_EXIT
                return result

            try:
                return await self.io(select)
            except OSError as error:
                raise local_configuration(str(error)) from error
        if method == "devices":
            return completed(
                "devices", await self.io(self.state.devices, args.get("device"))
            )
        if method == "daemon.status":
            return completed(
                "daemon status",
                {
                    "pid": os.getpid(),
                    "version": VERSION,
                    "connected": sum(
                        device.connected() for device in self.devices.values()
                    ),
                    # dispatch already owns the current status request.
                    "active": max(0, len(self.tasks) - 1),
                },
            )
        raise Fault("method", "Unsupported local method")

    async def expire_connections(self) -> None:
        for device in tuple(self.devices.values()):
            await device.expire_connections()

    def active_work(self) -> bool:
        return bool(
            self.tasks
            or self.io_task
            or self.changing
            or any(device.in_use() for device in self.devices.values())
        )

    def occupied(self) -> bool:
        return self.active_work() or any(
            device.connected() for device in self.devices.values()
        )

    async def close(self) -> None:
        await self.files.close()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.io_task is not None:
            await asyncio.gather(self.io_task, return_exceptions=True)
        await asyncio.gather(*(device.close() for device in self.devices.values()))
