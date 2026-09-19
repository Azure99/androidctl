import asyncio
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from androidctl import daemon, local_call
from androidctl import local_server as server_module
from androidctl.coordinator import Coordinator
from androidctl.identity import local_identity
from androidctl.private_files import (
    LifetimeLock,
    PublishedWriteError,
    atomic_write,
    private_directory,
    write_json,
)
from androidctl.protocol import (
    VERSION,
    Fault,
    Problem,
    Result,
    completed,
)
from androidctl.state import HostState
from androidctl.transport import Peer, send
from connection_test_support import local_server


def assert_daemon_stopped(root):
    assert not (root / "daemon.json").exists()
    lock = LifetimeLock(root / "daemon.lock")
    try:
        assert lock.acquire()
    finally:
        lock.close()


@pytest.mark.parametrize("stage", ["tls", "authentication"])
async def test_daemon_unavailable_preserves_last_connection_stage(
    isolated_state, stage
):
    root = isolated_state

    async def dispatch(method, args, deadline):
        raise AssertionError("No business request is allowed")

    async with local_server(root, dispatch) as (material, _):
        material.update(version=VERSION, credential="wrong")
        if stage == "tls":
            private_directory(root / "other")
            _, other = local_identity(root / "other")
            material["certificate"] = other["certificate"]
        write_json(root / "daemon.json", material)
        lock = LifetimeLock(root / "daemon.lock")
        assert lock.acquire()
        try:
            with pytest.raises(Fault) as caught:
                await local_call.connect_local(
                    start=False, deadline=time.monotonic() + 10
                )
            assert caught.value.problem.code == "daemonUnavailable"
            assert caught.value.problem.stage == f"local.{stage}"
        finally:
            lock.close()


@pytest.mark.parametrize("disappears_on_read", [1, 2])
async def test_disappearing_descriptor_can_be_rediscovered(
    isolated_state, monkeypatch, disappears_on_read
):
    root = isolated_state
    path = root / "daemon.json"
    original = local_call.open_read
    reads = 0

    async def dispatch(method, args, deadline):
        return completed("devices", {"devices": []}).wire()

    async with local_server(root, dispatch) as (material, _):
        material["version"] = VERSION
        write_json(path, material)
        lock = LifetimeLock(root / "daemon.lock")
        assert lock.acquire()

        def read(self, *args, **kwargs):
            nonlocal reads
            if self == path:
                reads += 1
                if reads == disappears_on_read:
                    path.unlink()
                    asyncio.get_running_loop().call_later(
                        0.01, write_json, path, material
                    )
            return original(self, *args, **kwargs)

        monkeypatch.setattr(local_call, "open_read", read)
        try:
            peer = await local_call.connect_local(deadline=time.monotonic() + 2)
            try:
                assert (await peer.request("devices", deadline=time.monotonic() + 10))[
                    "result"
                ] == "done"
            finally:
                await peer.close()
        finally:
            lock.close()


@pytest.mark.parametrize("error", [ValueError("bad JSON"), PermissionError("denied")])
@pytest.mark.parametrize("boundary", ["permission", "read"])
def test_descriptor_errors_are_not_treated_as_absence(
    isolated_state, monkeypatch, error, boundary
):
    root = isolated_state
    private_directory(root)
    path = root / "daemon.json"
    write_json(path, {"instance": "fixture"})
    original = local_call.open_read

    def read(self, *args, **kwargs):
        if self == path:
            raise error
        return original(self, *args, **kwargs)

    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid descriptor must not start a daemon")

    if boundary == "read":
        monkeypatch.setattr(local_call, "open_read", read)
    else:
        check = local_call.check_private

        def denied(target):
            if target == path:
                raise error
            return check(target)

        monkeypatch.setattr(local_call, "check_private", denied)
        monkeypatch.setattr(local_call, "open_read", unexpected)
    monkeypatch.setattr(local_call.subprocess, "Popen", unexpected)
    with pytest.raises(Fault) as caught:
        asyncio.run(local_call.connect_local(deadline=time.monotonic() + 10))
    assert caught.value.problem.code == "localConfiguration"
    assert caught.value.problem.message == str(error)


@pytest.mark.parametrize(
    ("held", "start"), [(False, True), (True, True), (True, False)]
)
def test_expired_daemon_discovery_entry_reports_timeout(
    isolated_state, monkeypatch, held, start
):
    root = isolated_state
    private_directory(root)
    lock = LifetimeLock(root / "daemon.lock")
    if held:
        assert lock.acquire()

    def unexpected(*args, **kwargs):
        raise AssertionError("Expired discovery entry must not start a daemon")

    monkeypatch.setattr(local_call.subprocess, "Popen", unexpected)
    try:
        with pytest.raises(Fault) as caught:
            asyncio.run(
                local_call.connect_local(start=start, deadline=time.monotonic() - 1)
            )
        assert caught.value.problem.code == "timeout"
        assert caught.value.problem.message == (
            "Request budget exhausted before daemon connection or startup"
        )
        assert caught.value.problem.stage is None
    finally:
        lock.close()


def test_initial_daemon_lock_probe_can_exhaust_discovery_entry_budget(
    isolated_state, monkeypatch
):
    root = isolated_state
    private_directory(root)
    now = [time.monotonic()]
    deadline = now[0] + 1
    acquire = LifetimeLock.acquire

    def probe(self):
        acquired = acquire(self)
        now[0] = deadline
        return acquired

    def unexpected(*args, **kwargs):
        raise AssertionError("Initial lock probe exhausted the startup budget")

    monkeypatch.setattr(local_call, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(LifetimeLock, "acquire", probe)
    monkeypatch.setattr(local_call.subprocess, "Popen", unexpected)
    with pytest.raises(Fault) as caught:
        asyncio.run(local_call.connect_local(deadline=deadline))
    assert caught.value.problem.code == "timeout"
    assert caught.value.problem.stage is None


def test_expired_stopped_daemon_keeps_stopped_result(isolated_state):
    with pytest.raises(Fault) as caught:
        asyncio.run(
            local_call.connect_local(start=False, deadline=time.monotonic() - 1)
        )
    assert caught.value.problem.code == "daemonStopped"
    assert caught.value.problem.stage == "local"


def test_lock_holder_without_endpoint_keeps_publication_failure(isolated_state):
    root = isolated_state
    private_directory(root)
    lock = LifetimeLock(root / "daemon.lock")
    assert lock.acquire()
    try:
        with pytest.raises(Fault) as caught:
            asyncio.run(
                local_call.connect_local(start=False, deadline=time.monotonic() + 10)
            )
        assert caught.value.problem.code == "daemonUnavailable"
        assert caught.value.problem.stage == "local.publish"
    finally:
        lock.close()


def test_daemon_does_not_start_after_lock_reprobe_exhausts_deadline(
    isolated_state, monkeypatch
):
    root = isolated_state
    private_directory(root)
    lock = LifetimeLock(root / "daemon.lock")
    assert lock.acquire()
    acquire = LifetimeLock.acquire
    probes = []
    now = [time.monotonic()]
    monkeypatch.setattr(local_call, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def reprobe(self):
        probes.append(self)
        if len(probes) == 2:
            lock.close()
            now[0] += 0.04
        return acquire(self)

    def unexpected(*args, **kwargs):
        raise AssertionError("deadline expired; must not start a daemon")

    monkeypatch.setattr(LifetimeLock, "acquire", reprobe)
    monkeypatch.setattr(local_call.subprocess, "Popen", unexpected)
    try:
        with pytest.raises(Fault) as error:
            asyncio.run(local_call.connect_local(deadline=now[0] + 0.03))
        assert error.value.problem.code == "daemonUnavailable"
        assert error.value.problem.stage == "local.publish"
        assert len(probes) == 2
    finally:
        lock.close()


def test_restart_waits_for_prior_lock_then_starts_only_once(
    isolated_state, monkeypatch
):
    root = isolated_state
    private_directory(root)
    _, old = local_identity(root)
    old["instance"] = "old-instance"
    write_json(
        root / "daemon.json",
        {
            **old,
            "version": VERSION,
            "credential": "old",
            "endpoint": "wss://127.0.0.1:1/ipc",
        },
    )
    lock = LifetimeLock(root / "daemon.lock")
    assert lock.acquire()
    original = subprocess.Popen
    owned = []

    def spawn(args, **kwargs):
        process = original(args, **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr(local_call.subprocess, "Popen", spawn)

    async def run():
        with pytest.raises(Fault):
            await local_call.connect_local(start=False, deadline=time.monotonic() + 10)
        assert not owned
        reprobed = asyncio.Event()
        acquire = LifetimeLock.acquire

        def observed_acquire(self):
            acquired = acquire(self)
            if not acquired:
                reprobed.set()
            return acquired

        monkeypatch.setattr(LifetimeLock, "acquire", observed_acquire)

        async def retire_old():
            await asyncio.wait_for(reprobed.wait(), 3)
            (root / "daemon.json").unlink()
            lock.close()

        retiring = asyncio.create_task(retire_old())
        peer = await local_call.connect_local(deadline=time.monotonic() + 10)
        await retiring
        try:
            descriptor = json.loads((root / "daemon.json").read_bytes())
            assert descriptor["instance"] != old["instance"]
            assert descriptor["instance"] == owned[0].args[-1]
            await send(
                peer.connection,
                peer.prepare_message(
                    peer.request_body("daemon.stop"), time.monotonic() + 5
                ),
            )
            # Do not consume the reply: server shutdown must not need an ACK.
            assert await asyncio.to_thread(owned[0].wait, timeout=5) == 0
            diagnostic = json.loads((root / "daemon-status.json").read_bytes())
            assert diagnostic["instance"] == descriptor["instance"]
            assert diagnostic["phase"] == "stopped"
            assert not (root / "daemon.json").exists()
            probe = LifetimeLock(root / "daemon.lock")
            try:
                assert probe.acquire()
            finally:
                probe.close()
        finally:
            await peer.close()

    try:
        asyncio.run(run())
        assert len(owned) == 1
        assert owned[0].wait(timeout=5) == 0
        assert not (root / "daemon.json").exists()
    finally:
        lock.close()
        for process in owned:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_daemon_version_is_checked_before_connection(isolated_state, monkeypatch):
    root = isolated_state
    private_directory(root)
    _, identity = local_identity(root)
    identity["instance"] = "test-instance"
    write_json(
        root / "daemon.json",
        {
            **identity,
            "version": "0.0.1",
            "credential": "old",
            "endpoint": "wss://127.0.0.1:1/ipc",
        },
    )
    lock = LifetimeLock(root / "daemon.lock")
    assert lock.acquire()

    def unexpected(*args, **kwargs):
        raise AssertionError(
            "Version mismatch must be rejected before connecting or spawning"
        )

    monkeypatch.setattr(Peer, "open", unexpected)
    monkeypatch.setattr(local_call.subprocess, "Popen", unexpected)
    try:
        with pytest.raises(Fault) as caught:
            asyncio.run(local_call.connect_local(deadline=time.monotonic() + 10))
        assert caught.value.problem.code == "daemonVersion"
        assert caught.value.problem.stage == "local.version"
    finally:
        lock.close()


@pytest.mark.parametrize("missing", [True, False])
def test_real_cli_lists_unusable_pairing_without_blocking_daemon(
    tmp_path, isolated_state, missing
):
    root = isolated_state
    private_directory(root)
    state = HostState(root)
    if missing:
        write_json(state.pairings / "invalid-id.json", {"retained": True})
    else:
        atomic_write(
            state.pairings / "11111111-1111-4111-8111-111111111111111.json",
            b"invalid configuration",
        )
    original = {
        path.name: path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    environment = dict(os.environ)
    value = subprocess.run(
        [sys.executable, "-m", "androidctl", "--json", "devices"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=25,
    )
    try:
        assert value.returncode == 0, value.stderr
        result = json.loads(value.stdout)
        assert result["data"]["devices"][0]["error"]["code"] == "pairingInvalid"
        assert not (root / "daemon-status.json").exists()
        for path in root.rglob("*"):
            if path.name in original:
                assert path.read_bytes() == original[path.name]
    finally:
        stopped = subprocess.run(
            [sys.executable, "-m", "androidctl", "--json", "daemon", "stop"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            timeout=25,
        )
        assert stopped.returncode == 0, stopped.stdout
    assert not (root / "daemon.json").exists()
    lock = LifetimeLock(root / "daemon.lock")
    try:
        deadline = time.monotonic() + 5
        while not lock.acquire():
            assert time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        lock.close()


def test_startup_diagnostics_are_bounded_and_preserve_fault_details(
    tmp_path, monkeypatch
):
    tmp_path = tmp_path / "private"

    def failure(_root):
        raise Fault("specificFailure", "x" * 4000, "identity")

    monkeypatch.setattr(daemon, "local_identity", failure)
    with pytest.raises(Fault, match="xxx"):
        asyncio.run(daemon.run(tmp_path, "this-start"))
    reported = local_call.recorded_failure(tmp_path, "this-start")
    assert reported.problem.code == "specificFailure"
    assert reported.problem.stage == "identity"
    assert reported.problem.diagnostic_truncated is True
    assert len(reported.problem.message) == 2048
    assert local_call.recorded_failure(tmp_path, "old-start") is None
    path = tmp_path / "daemon-status.json"
    value = json.loads(path.read_bytes())
    value["phase"] = "runtime"
    write_json(path, value)
    assert local_call.recorded_failure(tmp_path, "this-start") is None
    path.write_bytes(b"x" * 16385)
    assert local_call.recorded_failure(tmp_path, "this-start") is None
    path.unlink()
    path.mkdir()
    with pytest.raises(Fault, match="xxx"):
        asyncio.run(daemon.run(tmp_path, "write-unavailable"))
    assert local_call.recorded_failure(tmp_path, "write-unavailable") is None


@pytest.mark.parametrize(
    "failure",
    [
        PublishedWriteError("after replace, before helper returns"),
        Fault.from_problem(
            Problem(
                code="descriptorFailure",
                message="after replace, before helper returns",
                stage="local.publish",
                diagnostic_truncated=True,
            )
        ),
    ],
    ids=["published-io", "fault"],
)
def test_descriptor_write_failure_remains_a_startup_error(
    tmp_path, monkeypatch, failure
):
    tmp_path = tmp_path / "private"
    original = daemon.write_json

    def after_publish(path, value, **kwargs):
        original(path, value, **kwargs)
        if path.name == "daemon.json":
            raise failure

    monkeypatch.setattr(daemon, "write_json", after_publish)
    with pytest.raises(type(failure), match="after replace") as caught:
        asyncio.run(daemon.run(tmp_path, "published"))
    assert caught.value is failure
    value = json.loads((tmp_path / "daemon-status.json").read_bytes())
    assert value["phase"] == "startup"
    reported = local_call.recorded_failure(tmp_path, "published")
    assert reported is not None
    if isinstance(failure, Fault):
        assert reported.problem == failure.problem
    else:
        assert reported.problem.code == "startupFailure"
        assert str(reported) == f"PublishedWriteError: {failure}"
    assert_daemon_stopped(tmp_path)


def test_failure_after_descriptor_write_remains_a_runtime_diagnostic(
    tmp_path, monkeypatch
):
    tmp_path = tmp_path / "private"
    failure = Fault("maintenanceFailure", "Connection expiry failed", "maintenance")

    async def expire(_coordinator):
        descriptor = json.loads((tmp_path / "daemon.json").read_bytes())
        assert descriptor["instance"] == "running"
        raise failure

    monkeypatch.setattr(Coordinator, "expire_connections", expire)
    with pytest.raises(Fault, match="Connection expiry failed") as caught:
        asyncio.run(daemon.run(tmp_path, "running"))
    assert caught.value is failure
    value = json.loads((tmp_path / "daemon-status.json").read_bytes())
    assert value["phase"] == "runtime"
    assert value["problem"] == failure.problem.wire()
    assert local_call.recorded_failure(tmp_path, "running") is None
    assert_daemon_stopped(tmp_path)


@pytest.mark.parametrize("phase", ["runtime", "cleanup"])
async def test_listener_failures_reach_diagnostic_after_connections_close(
    isolated_state, monkeypatch, phase
):
    root = isolated_state
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    fail_accept = asyncio.Event()
    failure = OSError(f"injected {phase} failure")
    accept = loop.sock_accept
    accepted = False
    closed = []
    close = server_module.close

    class CapturedServer(daemon.LocalServer):
        def start(self):
            super().start()
            ready.set_result(self)

    async def failing_accept(listener):
        nonlocal accepted
        if accepted:
            await fail_accept.wait()
            raise failure
        accepted = True
        return await accept(listener)

    async def failing_close(connection):
        in_handler = asyncio.current_task() in server.server.handlers.values()
        await close(connection)
        if not in_handler:
            closed.append(connection)
            raise failure

    monkeypatch.setattr(daemon, "LocalServer", CapturedServer)
    if phase == "runtime":
        monkeypatch.setattr(loop, "sock_accept", failing_accept)
    running = asyncio.create_task(daemon.run(root, "listener-failure"))
    peers = []
    try:
        server = await asyncio.wait_for(ready, 3)
        material = json.loads((root / "daemon.json").read_bytes())
        for _ in range(1 if phase == "runtime" else 2):
            peers.append(await Peer.open(material, source="local"))
        if phase == "runtime":
            fail_accept.set()
        else:
            monkeypatch.setattr(server_module, "close", failing_close)
            server.stopped.set()
        with pytest.raises(OSError, match=f"injected {phase} failure") as caught:
            await asyncio.wait_for(running, 3)
        assert caught.value is failure
        diagnostic = json.loads((root / "daemon-status.json").read_bytes())
        assert diagnostic["phase"] == phase
        assert str(failure) in diagnostic["problem"]["message"]
        assert all(peer.connection.state.name == "CLOSED" for peer in peers)
        assert server.listener.fileno() == -1
        assert not server.tasks
        assert_daemon_stopped(root)
        if phase == "cleanup":
            assert len(closed) == 2
            confirmed = await local_call.confirm_stop(
                completed("daemon stop", {"instance": "listener-failure"}),
                time.monotonic() + 3,
            )
            assert confirmed.result == "failed"
            assert str(failure) in confirmed.reason
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.gather(*(peer.close() for peer in peers))


def test_lock_loser_keeps_descriptor_and_diagnostic(tmp_path):
    tmp_path = tmp_path / "private"
    private_directory(tmp_path)
    descriptor, diagnostic = tmp_path / "daemon.json", tmp_path / "daemon-status.json"
    write_json(descriptor, {"instance": "other"})
    write_json(diagnostic, {"instance": "other"})
    original = (descriptor.read_bytes(), diagnostic.read_bytes())
    lock = LifetimeLock(tmp_path / "daemon.lock")
    assert lock.acquire()
    try:
        asyncio.run(daemon.run(tmp_path, "loser"))
        assert (descriptor.read_bytes(), diagnostic.read_bytes()) == original
    finally:
        lock.close()


def test_authenticated_daemon_wins_over_matching_error_file(
    isolated_state, monkeypatch
):
    root = isolated_state
    original = subprocess.Popen
    owned = []

    def launch(args, **kwargs):
        proc = original(args, **kwargs)
        owned.append(proc)
        deadline = time.monotonic() + 10
        while not (root / "daemon.json").exists():
            assert proc.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        write_json(
            root / "daemon-status.json",
            {
                "instance": args[-1],
                "phase": "startup",
                "problem": {
                    "code": "injectedOldFailure",
                    "message": "Must not override authenticated service",
                },
            },
        )
        return proc

    monkeypatch.setattr(local_call.subprocess, "Popen", launch)

    async def run():
        peer = await local_call.connect_local(deadline=time.monotonic() + 10)
        try:
            result = await peer.request("daemon.stop", deadline=time.monotonic() + 10)
            assert result["result"] == "done"
        finally:
            await peer.close()

    try:
        asyncio.run(run())
        assert owned[0].wait(timeout=5) == 0
    finally:
        for proc in owned:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


def test_connects_to_competing_daemon_after_requested_child_loses_lock(
    isolated_state, monkeypatch
):
    root = isolated_state
    original = subprocess.Popen
    owned = []
    winner_instance = "competing-winner"

    def launch(args, **kwargs):
        winner = original(
            [sys.executable, "-m", "androidctl.daemon", "--instance", winner_instance],
            **kwargs,
        )
        owned.append(winner)
        until = time.monotonic() + 5
        while not (root / "daemon.json").exists():
            assert winner.poll() is None
            assert time.monotonic() < until
            time.sleep(0.01)
        requested = original(args, **kwargs)
        owned.append(requested)
        # Keep the winner alive until the requested child loses its lock race.
        assert requested.wait(timeout=5) == 0
        return requested

    monkeypatch.setattr(local_call.subprocess, "Popen", launch)

    async def run():
        deadline = time.monotonic() + 10
        peer = await local_call.connect_local(deadline=deadline)
        try:
            winner, requested = owned
            material = local_call.descriptor(root)
            assert material["instance"] == winner_instance
            status = await peer.request(
                "daemon.status", deadline=deadline, validate=Result.model_validate
            )
            assert status.result == "done"
            assert status.data["pid"] == winner.pid
            stopped = await peer.request(
                "daemon.stop", deadline=deadline, validate=Result.model_validate
            )
            assert stopped.result == "done"
            assert stopped.data["instance"] == winner_instance
        finally:
            await peer.close(deadline=deadline)
        confirmed = await local_call.confirm_stop(stopped, deadline)
        assert confirmed.result == "done"
        assert winner.wait(timeout=5) == 0
        assert requested.wait(timeout=5) == 0
        assert local_call.diagnostic(root) == {
            "instance": winner_instance,
            "phase": "stopped",
        }
        assert_daemon_stopped(root)

    try:
        asyncio.run(run())
    finally:
        for process in owned:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("diagnostic_write_fails", [False, True])
def test_stop_reports_cleanup_failure_after_releasing_original_lock(
    isolated_state, monkeypatch, diagnostic_write_fails
):
    root = isolated_state
    original = Coordinator.close

    async def cleanup(self):
        await original(self)
        raise OSError("injected cleanup failure " + "x" * 20000)

    monkeypatch.setattr(Coordinator, "close", cleanup)
    original_write = daemon.write_json

    def write(path, value, **kwargs):
        if diagnostic_write_fails and path.name == "daemon-status.json":
            raise OSError("diagnostic write failure")
        original_write(path, value, **kwargs)

    monkeypatch.setattr(daemon, "write_json", write)

    async def run():
        running = asyncio.create_task(daemon.run(root, "fixture"))
        try:
            until = time.monotonic() + 5
            while not (root / "daemon.json").exists():
                assert time.monotonic() < until
                await asyncio.sleep(0.01)
            peer = await local_call.connect_local(
                start=False, deadline=time.monotonic() + 10
            )
            reply = Result.model_validate(
                await peer.request("daemon.stop", deadline=time.monotonic() + 10)
            )
            await peer.close()
            confirmed = await local_call.confirm_stop(reply, time.monotonic() + 5)
            assert confirmed.result == "failed"
            if not diagnostic_write_fails:
                assert "injected cleanup failure" in confirmed.reason
                assert confirmed.error.diagnostic_truncated is True
            with pytest.raises(OSError, match="failure"):
                await running
            assert_daemon_stopped(root)
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    asyncio.run(run())


def test_stop_confirmation_does_not_open_another_wait_budget(isolated_state):
    root = isolated_state
    private_directory(root)
    held = LifetimeLock(root / "daemon.lock")
    assert held.acquire()
    try:
        result = asyncio.run(
            local_call.confirm_stop(
                completed("daemon stop", {"instance": "old"}), time.monotonic()
            )
        )
        assert result.result == "still running"
    finally:
        held.close()


def test_real_startup_identity_failure_remains_actionable(tmp_path, isolated_state):
    root = isolated_state
    private_directory(root)
    (root / "ipc.key").mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "androidctl", "--json", "devices"],
        cwd=tmp_path,
        env=dict(os.environ),
        capture_output=True,
        timeout=25,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "startupFailure"
    assert not (root / "daemon.json").exists()
