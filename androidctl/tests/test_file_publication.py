import asyncio
import errno
import os
import stat
import threading
from types import SimpleNamespace

import pytest

from androidctl import file_jobs, user_files
from androidctl.file_jobs import FileJob, FileJobs
from androidctl.private_files import check_private
from androidctl.protocol import Fault, Result
from androidctl.user_files import Directory
from file_test_support import Device, start


@pytest.mark.parametrize("delayed", [False, True])
async def test_file_pull_uses_original_chunk_and_publishes_private_verified_bytes(
    tmp_path, delayed
):
    device = Device(delayed=delayed)
    manager = FileJobs()
    job = start(manager, tmp_path, remote=device.call)
    await job.task
    assert job.view().result == "done"
    assert (tmp_path / "final").read_bytes() == device.body
    check_private(tmp_path / "final")
    assert device.commands.count("file.chunk") == 1
    assert device.commands.count("file.finish") == 1
    assert "file.cancel" not in device.commands
    assert not manager.targets
    assert not list(tmp_path.glob(".androidctl-*.tmp"))


def test_initial_destination_conflict_is_not_sent(tmp_path):
    (tmp_path / "final").write_bytes(b"preserve")

    async def run():
        device = Device()
        manager = FileJobs()
        job = start(manager, tmp_path, remote=device.call)
        await job.task
        assert job.view().result == "not sent"
        assert not device.commands
        assert (tmp_path / "final").read_bytes() == b"preserve"

    asyncio.run(run())


async def test_cancel_before_publish_permission_keeps_target_unpublished(
    tmp_path, monkeypatch
):
    original_publish = Directory.publish

    def publish(parent, temporary, name, owned, *, replace, before_publish):
        def cancel_then_request_permission():
            job.request_stop()
            before_publish()

        return original_publish(
            parent,
            temporary,
            name,
            owned,
            replace=replace,
            before_publish=cancel_then_request_permission,
        )

    monkeypatch.setattr(Directory, "publish", publish)
    device = Device()
    job = start(FileJobs(), tmp_path, remote=device.call, replace=True)
    await job.task
    result = job.view()
    assert result.result == "failed"
    assert result.data["stopRequested"] is True
    assert result.data["resourcesActive"] is False
    assert not job.local.exists()
    assert not job.manager.targets
    assert not list(tmp_path.glob(".androidctl-*.tmp"))


async def test_cancel_after_publish_permission_preserves_completed_publication(
    tmp_path, monkeypatch
):
    original_replace = user_files.os.replace

    def replace(*args, **kwargs):
        job.request_stop()
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(user_files.os, "replace", replace)
    device = Device()
    job = start(FileJobs(), tmp_path, remote=device.call, replace=True)
    await job.task
    result = job.view()
    assert result.result == "done"
    assert result.data["stopRequested"] is True
    assert result.data["resourcesActive"] is False
    assert job.local.read_bytes() == device.body
    assert not job.manager.targets
    assert not list(tmp_path.glob(".androidctl-*.tmp"))


@pytest.mark.parametrize("stage", ["preflight", "rename"])
def test_publish_eio_classification_follows_actual_publish_attempt(
    tmp_path, monkeypatch, stage
):
    original_verify = Directory.verify
    verified, renamed = [], []

    def verify(parent, name, expected):
        verified.append(name)
        if stage == "preflight" and len(verified) == 1:
            raise OSError(errno.EIO, "preflight EIO")
        original_verify(parent, name, expected)

    def replace(*args, **kwargs):
        renamed.append(args)
        raise OSError(errno.EIO, "rename EIO")

    monkeypatch.setattr(Directory, "verify", verify)
    monkeypatch.setattr(user_files.os, "replace", replace)

    async def run():
        device = Device()
        manager = FileJobs()
        job = start(manager, tmp_path, remote=device.call, replace=True)
        await job.task
        assert job.view().result == ("failed" if stage == "preflight" else "UNKNOWN")
        assert len(renamed) == (0 if stage == "preflight" else 1)
        assert job.ended
        assert job.parent is None
        assert job.stream is None
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_cancelling_during_actual_write_keeps_owner_until_io_finishes(
    tmp_path, monkeypatch
):
    entered, leave = threading.Event(), threading.Event()
    original = FileJob.write

    def slow(job, data):
        entered.set()
        assert leave.wait(5)
        original(job, data)

    monkeypatch.setattr(FileJob, "write", slow)

    async def run():
        device = Device()
        manager = FileJobs()
        job = start(manager, tmp_path, remote=device.call)
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            manager.find(job.id, "device", "pair", "session").request_stop()
            before = list(device.commands)
            competing = start(manager, tmp_path, remote=device.call)
            await competing.task
            assert not job.ended
            assert job.stream is not None
            assert not job.stream.closed
            assert manager.targets
            assert not (tmp_path / "final").exists()
            assert competing.view().result == "not sent"
            assert device.commands == before
            assert manager.targets[job.target_key] == job.id
            with pytest.raises(Fault, match="belongs"):
                manager.find(job.id, "device", "pair", "other")
        finally:
            leave.set()
            await job.task
        assert job.ended
        assert job.view().result == "failed"
        assert not list(tmp_path.iterdir())
        assert not manager.targets

    asyncio.run(run())


def test_different_targets_publish_independently_and_keep_actual_io_owner(
    tmp_path, monkeypatch
):
    entered, leave = threading.Event(), threading.Event()
    original = Directory.publish

    def publish(parent, temporary, target, *args, **kwargs):
        if target == "A":
            entered.set()
            assert leave.wait(10)
        return original(parent, temporary, target, *args, **kwargs)

    monkeypatch.setattr(Directory, "publish", publish)

    async def run():
        manager = FileJobs()

        def spawn(name, budget):
            remote = Device().call
            value = manager.start(
                "device",
                "pair",
                "session",
                {
                    "direction": "pull",
                    "local": str(tmp_path / name),
                    "remote": "/file",
                    "runMs": budget,
                },
                lambda: None,
                remote=remote,
            )
            return manager.items[value.data["job"]]

        a = spawn("A", 10000)
        jobs = [a]
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            b, conflict = spawn("B", 2000), spawn("A", 2000)
            jobs.extend([b, conflict])
            await asyncio.wait_for(asyncio.gather(b.task, conflict.task), 3)
            assert b.view().result == "done"
            assert (tmp_path / "B").read_bytes() == Device().body
            assert conflict.view().result == "not sent"
            assert conflict.view().error.code == "busy"
            a.request_stop()
            assert not a.task.done()
            assert a.view().data["resourcesActive"] is True
            assert list(manager.targets.values()) == [a.id]
            assert not (tmp_path / "A").exists()
            leave.set()
            await a.task
            assert a.view().result == "failed"
            assert a.view().data["resourcesActive"] is False
            assert not (tmp_path / "A").exists()
            assert not manager.targets
            assert not list(tmp_path.glob(".androidctl-*.tmp"))
        finally:
            leave.set()
            await asyncio.gather(*(job.task for job in jobs))

    asyncio.run(run())


def test_posix_parent_owner_does_not_prevent_private_temporary_creation(
    tmp_path, monkeypatch
):
    descriptors, created_modes, methods = {}, [], []

    class PosixBoundary:
        O_DIRECTORY = 1 << 30
        O_CLOEXEC = O_NOFOLLOW = O_NONBLOCK = 0

        def __getattr__(self, name):
            return getattr(os, name)

        def getuid(self):
            return 1000

        def open(self, name, flags, create_mode=0o777, *, dir_fd=None):
            if dir_fd is None:
                assert flags & self.O_DIRECTORY
                fd = os.open(
                    tmp_path / "directory-fd", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
                )
                descriptors[fd] = True
            else:
                assert descriptors[dir_fd]
                if flags & os.O_CREAT:
                    assert flags & os.O_EXCL
                    created_modes.append(create_mode)
                fd = os.open(
                    tmp_path / name, flags | getattr(os, "O_BINARY", 0), create_mode
                )
                descriptors[fd] = False
            return fd

        def fstat(self, fd):
            actual = os.fstat(fd)
            directory = descriptors[fd]
            return SimpleNamespace(
                st_dev=actual.st_dev,
                st_ino=actual.st_ino,
                st_mode=(stat.S_IFDIR | 0o777) if directory else (stat.S_IFREG | 0o600),
                st_uid=2000 if directory else 1000,
            )

        def unlink(self, name, *, dir_fd):
            assert descriptors[dir_fd]
            return os.unlink(tmp_path / name)

    boundary = PosixBoundary()
    for module in (file_jobs, user_files):
        monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
        monkeypatch.setattr(module, "os", boundary)

    async def remote(
        command,
        parameters,
        *,
        session,
        deadline,
        before_send=None,
        exchange_seconds=None,
    ):
        methods.append(command)
        assert command == "file.begin"
        return Result(
            command="pull",
            result="not sent",
            data={"job": "original", "resourcesActive": False},
        )

    async def run():
        manager = FileJobs()
        job = FileJob(
            manager,
            "device",
            "pair",
            "session",
            {"direction": "pull", "local": str(tmp_path / "target"), "remote": "/file"},
            remote,
        )
        await job.run()
        assert job.view().result == "not sent"
        assert methods == ["file.begin"]
        assert created_modes == [0o600]
        assert not list(tmp_path.glob(".androidctl-*.tmp"))
        assert not manager.targets

    asyncio.run(run())
