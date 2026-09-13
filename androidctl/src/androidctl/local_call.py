from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .private_files import (
    LifetimeLock,
    check_private,
    private_directory,
    state_directory,
)
from .protocol import CONNECT_SECONDS, VERSION, Fault, Problem, Result
from .transport import Peer
from .user_files import open_read

DIAGNOSTIC_BYTES = 16384
DAEMON_START_SECONDS = 10


def descriptor(root: Path) -> dict[str, Any] | None:
    path = root / "daemon.json"
    try:
        check_private(path)
        with open_read(path) as stream:
            return cast(dict[str, Any], json.loads(stream.read().decode("utf-8")))
    except FileNotFoundError:
        return None


def daemon_free(root: Path) -> bool:
    lock = LifetimeLock(root / "daemon.lock")
    free = lock.acquire()
    lock.close()
    return free


def diagnostic(root: Path) -> dict[str, Any]:
    path = root / "daemon-status.json"
    check_private(path)
    with open_read(path) as handle:
        raw = handle.read(DIAGNOSTIC_BYTES + 1)
    if len(raw) > DIAGNOSTIC_BYTES:
        raise ValueError("Daemon diagnostic exceeds its size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Daemon diagnostic must be an object")
    return value


def recorded_failure(root: Path, identifier: str) -> Fault | None:
    try:
        value = diagnostic(root)
        if value.get("instance") != identifier or value.get("phase") != "startup":
            return None
        return Fault.from_problem(Problem.model_validate(value["problem"]))
    except (OSError, ValueError, TypeError, KeyError, Fault):
        return None


def start_daemon(root: Path, instance: str) -> None:
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        creationflags = 0
    subprocess.Popen(
        [sys.executable, "-m", "androidctl.daemon", "--instance", instance],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=root,
        creationflags=creationflags,
        start_new_session=sys.platform != "win32",
    )


async def connect_local(*, start: bool = True, deadline: float) -> Peer:
    try:
        return await _connect_local(start=start, deadline=deadline)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Fault("localConfiguration", str(error), "local.configuration") from error


async def _connect_local(*, start: bool, deadline: float) -> Peer:
    deadline = min(
        deadline,
        time.monotonic() + (DAEMON_START_SECONDS if start else CONNECT_SECONDS),
    )
    root = state_directory()
    private_directory(root)
    material = descriptor(root)
    initial_instance = material["instance"] if material is not None else None
    lock_free = daemon_free(root)
    spawned_instance = None
    if lock_free and not start:
        raise Fault("daemonStopped", "No daemon is running", "local")
    if time.monotonic() >= deadline:
        raise Fault(
            "timeout",
            "Request budget exhausted before daemon connection or startup",
        )
    attempted_instances: set[str] = (
        {initial_instance} if lock_free and initial_instance is not None else set()
    )
    last_connection_error: Fault | None = None
    while True:
        if time.monotonic() >= deadline:
            break
        if lock_free and spawned_instance is None:
            spawned_instance = secrets.token_hex(16)
            start_daemon(root, spawned_instance)
        material = descriptor(root)
        if material is not None:
            if not lock_free and material.get("version") != VERSION:
                raise Fault(
                    "daemonVersion",
                    f"Running daemon pid={material.get('pid')} "
                    f"version={material.get('version', 'unknown')}; "
                    f"CLI version={VERSION}. "
                    "The daemon exits after about five continuous idle minutes "
                    "with no local work or connections.",
                    "local.version",
                )
            instance = material["instance"]
            if instance not in attempted_instances:
                attempted_instances.add(instance)
                try:
                    return await Peer.open(material, deadline=deadline, source="local")
                except Fault as error:
                    last_connection_error = error
        if spawned_instance is not None:
            failure = recorded_failure(root, spawned_instance)
            if failure is not None:
                raise failure
        if not start or time.monotonic() >= deadline:
            break
        if spawned_instance is None:
            lock_free = daemon_free(root)
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    raise Fault(
        "daemonUnavailable",
        (
            str(last_connection_error)
            if last_connection_error
            else "The lock holder did not publish a usable endpoint; cause unknown"
        ),
        (
            last_connection_error.problem.stage
            if last_connection_error
            else "local.publish"
        ),
    )


@dataclass(frozen=True)
class BoundDevice:
    device_id: str
    source: str


class LocalCall:
    def __init__(self, identifier: str | None = None, session: str = "default"):
        self.identifier, self.session = identifier, session
        self.binding: BoundDevice | None = None
        self.peer: Peer | None = None
        self.connect_ms: int | None = None

    @property
    def device_id(self) -> str | None:
        return self.binding.device_id if self.binding is not None else None

    async def connect(self, *, deadline: float, start: bool = True) -> Peer:
        if self.peer is None or not self.peer.opened:
            started = time.monotonic()
            try:
                self.peer = await connect_local(start=start, deadline=deadline)
            finally:
                self.connect_ms = int((time.monotonic() - started) * 1000)
        return self.peer

    async def request(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        deadline: float,
        start: bool = True,
    ) -> Result:
        peer = await self.connect(deadline=deadline, start=start)
        return await peer.request(
            method,
            dict(args or {}),
            session=self.session,
            deadline=deadline,
            validate=Result.model_validate,
        )

    async def call(
        self, command: str, args: dict[str, Any], *, deadline: float
    ) -> Result:
        result = await self.request(
            "call",
            {
                "command": command,
                "args": args,
                "device": self.device_id or self.identifier,
                "session": self.session,
            },
            deadline=deadline,
        )
        if self.binding is None:
            context = result.data.get("context", {})
            if context.get("deviceId") is not None:
                self.binding = BoundDevice(context["deviceId"], context["deviceSource"])
        return result

    async def close(self, *, deadline: float | None = None) -> None:
        if self.peer is not None:
            await self.peer.close(deadline=deadline)
