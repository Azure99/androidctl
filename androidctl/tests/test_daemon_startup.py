import asyncio
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from androidctl import daemon, local_call
from androidctl.coordinator import Coordinator
from androidctl.identity import local_identity
from androidctl.private_files import (
    LifetimeLock,
    PublishedWriteError,
    private_directory,
    write_json,
)
from androidctl.protocol import (
    VERSION,
    Fault,
    Problem,
    completed,
)
from androidctl.transport import Peer
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
