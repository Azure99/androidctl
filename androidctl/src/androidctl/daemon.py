from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .coordinator import Coordinator
from .identity import local_identity
from .local_server import LocalServer
from .private_files import LifetimeLock, private_directory, state_directory, write_json
from .protocol import VERSION, Fault, Problem
from .state import HostState

if sys.platform == "win32":
    from .user_files import replace_shared

DAEMON_IDLE_SECONDS = 300.0
DIAGNOSTIC_MESSAGE_CHARS = 2048


def bounded_problem(problem: Problem) -> dict[str, Any]:
    value = problem.model_copy()
    if len(value.message) > DIAGNOSTIC_MESSAGE_CHARS:
        value.message = value.message[:DIAGNOSTIC_MESSAGE_CHARS]
        value.diagnostic_truncated = True
    return value.wire()


async def run(root: Path, instance: str) -> None:
    private_directory(root)
    lock = LifetimeLock(root / "daemon.lock")
    if not lock.acquire():
        return
    coordinator: Coordinator | None = None
    server: LocalServer | None = None
    descriptor = root / "daemon.json"
    phase = "startup"
    clean_stop = False
    publish: Callable[[Path, Path], None] | None = None
    if sys.platform == "win32":
        publish = replace_shared

    def write_status(phase: str, problem: Problem | None = None) -> None:
        write_json(
            root / "daemon-status.json",
            {
                "instance": instance,
                "phase": phase,
                **({"problem": bounded_problem(problem)} if problem else {}),
            },
            publish=publish,
        )

    try:
        tls, identity = local_identity(root)
        coordinator = Coordinator(HostState(root), instance=instance)
        credential = secrets.token_urlsafe(32)
        stop_requested = asyncio.Event()
        server = LocalServer(tls, credential, coordinator.dispatch, stop_requested)
        server.start()
        write_json(
            descriptor,
            {
                **identity,
                "credential": credential,
                "pid": os.getpid(),
                "version": VERSION,
                "instance": instance,
                "endpoint": f"wss://127.0.0.1:{server.port}/ipc",
            },
            publish=publish,
        )
        phase = "runtime"
        idle_since: float | None = None
        while not stop_requested.is_set():
            if server.acceptor is not None and server.acceptor.done():
                server.acceptor.result()
            await coordinator.expire_connections()
            occupied = coordinator.occupied() or bool(
                server.tasks or server.connections
            )
            if occupied:
                idle_since = None
            elif idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= DAEMON_IDLE_SECONDS:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), 1.0)
        clean_stop = True
    except Exception as error:
        problem = (
            error.problem
            if isinstance(error, Fault)
            else Fault("startupFailure", f"{type(error).__name__}: {error}").problem
        )
        with suppress(OSError, Fault):
            write_status(phase, problem)
        raise
    finally:
        try:
            try:
                descriptor.unlink(missing_ok=True)
            finally:
                try:
                    if server is not None:
                        await server.stop()
                finally:
                    if coordinator is not None:
                        await coordinator.close()
        except Exception as error:
            problem = Fault("daemonCleanup", str(error), "local.stop").problem
            write_status("cleanup", problem)
            raise
        else:
            if clean_stop:
                # The stop caller needs proof that cleanup, not just dispatch, ended.
                write_status("stopped")
        finally:
            lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    asyncio.run(run(state_directory(), parser.parse_args().instance))
