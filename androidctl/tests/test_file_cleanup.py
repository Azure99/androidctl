import asyncio
import sys
import time

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import user_files
from androidctl.file_jobs import FileJob, FileJobs
from androidctl.protocol import Fault, Problem, Result, completed
from androidctl.user_files import Directory
from file_test_support import Device, start


def test_unexpected_remote_cleanup_error_does_not_skip_local_close(tmp_path):
    class BrokenCleanup(Device):
        async def call(self, command, parameters, **options):
            if command == "file.cancel":
                self.commands.append("file.cancel")
                raise RuntimeError("remote cleanup defect")
            value = await super().call(command, parameters, **options)
            if command == "file.finish":
                value.data["resourcesActive"] = True
            return value

    async def run():
        device = BrokenCleanup()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        value = job.view()
        assert value.result == "done"
        assert value.exit_code() == 3
        assert "remote cleanup defect" in value.data["cleanupError"]
        assert not value.data["resourcesActive"]
        assert job.stream is None
        assert job.parent is None
        assert not job.manager.targets
        assert device.commands.count("file.cancel") == 1
        assert (tmp_path / "final").read_bytes() == device.body

    asyncio.run(run())


@pytest.mark.parametrize("closed_first", [False, True])
def test_unconfirmed_local_close_keeps_target_without_retry(
    tmp_path, monkeypatch, closed_first
):
    original_io = FileJob.io
    retained = []

    async def io(job, operation, *args, **kwargs):
        if job.stream is not None and operation == job.stream.close:
            retained.append(job.stream)
            if closed_first:
                await original_io(job, operation, *args, **kwargs)
            raise OSError("stream close unconfirmed")
        return await original_io(job, operation, *args, **kwargs)

    monkeypatch.setattr(FileJob, "io", io)

    async def run():
        manager = FileJobs()
        job = start(manager, tmp_path, remote=Device().call)
        try:
            await job.task
            value = job.view()
            assert value.result == "done"
            assert value.exit_code() == 3
            assert value.data["resourcesActive"]
            assert "stream close unconfirmed" in value.data["cleanupError"]
            assert job.stream.closed is closed_first
            assert job.parent is None
            with pytest.raises(Fault, match="owns this destination"):
                manager.occupy(job.target_key, "other")
            job.request_stop()
            await manager.close()
            manager.trim()
            assert manager.items[job.id] is job
            assert len(retained) == 1
        finally:
            for stream in retained:
                stream.close()

    asyncio.run(run())


def test_cleanup_errors_accumulate_and_continue_without_losing_primary_problem(
    tmp_path, monkeypatch
):
    original_io = FileJob.io
    retained = []
    steps = []

    async def io(job, operation, *args, **kwargs):
        if job.stream is not None and operation == job.stream.close:
            steps.append("stream")
            retained.append(job.stream)
            raise OSError("stream close failed")
        if job.parent is not None and operation == job.parent.remove:
            steps.append("temporary")
            raise OSError("temporary removal failed")
        if job.parent is not None and operation == job.parent.close:
            steps.append("parent")
            await original_io(job, operation, *args, **kwargs)
            raise OSError("directory closed but unconfirmed")
        return await original_io(job, operation, *args, **kwargs)

    class FailedDevice(Device):
        async def call(self, command, parameters, **options):
            if command == "file.chunk":
                return Result(
                    command="file.chunk",
                    result="failed",
                    reason="original copy failure",
                    error=Problem(
                        code="originalCopy",
                        message="original copy failure",
                        stage="device.file",
                    ),
                )
            if command == "file.cancel":
                steps.append("remote")
                raise RuntimeError("remote cleanup defect")
            return await super().call(command, parameters, **options)

    monkeypatch.setattr(FileJob, "io", io)

    async def run():
        job = start(FileJobs(), tmp_path, remote=FailedDevice().call)
        try:
            await job.task
            value = job.view()
            assert value.result == "failed"
            assert value.exit_code() == 3
            assert value.error.wire() == {
                "code": "originalCopy",
                "message": "original copy failure",
                "stage": "device.file",
            }
            assert value.data["resourcesActive"]
            assert steps == ["remote", "stream", "temporary", "parent"]
            for message in (
                "remote cleanup defect",
                "stream close failed",
                "temporary removal failed",
                "directory closed but unconfirmed",
            ):
                assert message in value.data["cleanupError"]
            assert job.parent.closed
            assert job.manager.targets[job.target_key] == job.id
        finally:
            for stream in retained:
                stream.close()

    asyncio.run(run())


def test_local_cleanup_failure_cannot_be_not_sent(tmp_path, monkeypatch):
    async def offline(command, parameters, **options):
        return Result(
            command="file.begin",
            result="not sent",
            reason="offline",
            error=Problem(code="connectionFailed", message="offline", stage="tcp"),
        )

    def fail_remove(*_):
        raise PermissionError("owned temporary removal denied")

    monkeypatch.setattr(Directory, "remove", fail_remove)

    async def run():
        job = start(FileJobs(), tmp_path, remote=offline)
        await job.task
        assert job.view().result == "failed"
        assert "removal denied" in job.view().data["cleanupError"]
        assert job.ended
        assert not job.manager.targets
        assert not (tmp_path / "final").exists()
        assert len(list(tmp_path.glob(".androidctl-*.tmp"))) == 1

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_error", [None, "device close failed"])
def test_finished_device_is_not_cancelled_and_cleanup_error_keeps_execution(
    tmp_path, monkeypatch, cleanup_error
):
    class FinishedDevice(Device):
        async def call(self, command, parameters, **options):
            value = await super().call(command, parameters, **options)
            if command == "file.finish" and cleanup_error:
                value.data["cleanupError"] = cleanup_error
            return value

    async def run():
        device = FinishedDevice()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        result = job.view()
        assert result.result == "done"
        assert result.exit_code() == (3 if cleanup_error else 0)
        assert (tmp_path / "final").read_bytes() == device.body
        assert "file.cancel" not in device.commands
        assert job.ended
        assert job.stream is None
        assert job.parent is None
        return result

    result = asyncio.run(run())
    from androidctl import job_commands

    async def call(*_, **__):
        return result

    monkeypatch.setattr(job_commands.JobClient, "call", call)
    for flags in ([], ["--json"]):
        shown = CliRunner().invoke(cli.app, [*flags, "job", "get", result.data["job"]])
        assert shown.exit_code == result.exit_code()
        if cleanup_error:
            assert cleanup_error in shown.stdout


@pytest.mark.parametrize("end_resources", [False, True])
@pytest.mark.parametrize("terminal_result", ["done", "UNKNOWN", "not sent"])
def test_device_terminal_fact_does_not_end_cleanup_while_resources_active(
    tmp_path, monkeypatch, end_resources, terminal_result
):
    if not end_resources:
        from androidctl import file_jobs

        monkeypatch.setattr(file_jobs, "CLEANUP_SECONDS", 0.45)

    class ClosingDevice(Device):
        async def call(
            self,
            command,
            parameters,
            *,
            session,
            deadline,
            before_send=None,
            exchange_seconds=None,
        ):
            if time.monotonic() >= deadline:
                return Result(
                    command=command,
                    result="not sent",
                    reason="Cleanup request budget exhausted before dispatch",
                    error=Problem(
                        code="timeout",
                        message="Cleanup request budget exhausted before dispatch",
                    ),
                )
            if command == "file.cancel" or self.cancelled:
                self.cancelled = True
                self.commands.append(command)
                ended = end_resources and self.commands.count("file.get") >= 2
                return Result(
                    command="pull",
                    result=terminal_result,
                    data={"resourcesActive": not ended},
                )
            value = await super().call(
                command,
                parameters,
                session=session,
                deadline=deadline,
                before_send=before_send,
                exchange_seconds=exchange_seconds,
            )
            if command == "file.finish":
                value.data["resourcesActive"] = True
            return value

    async def run():
        device = ClosingDevice()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await asyncio.wait_for(asyncio.shield(job.task), 7)
        assert job.view().result == "done"
        assert device.commands.count("file.cancel") == 1
        assert device.commands.count("file.get") >= 2
        assert bool(job.cleanup_error) != end_resources
        if not end_resources:
            assert "Device cleanup unconfirmed" in job.cleanup_error
        assert (tmp_path / "final").read_bytes() == device.body
        assert job.ended
        assert not job.manager.targets

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows create failure")
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_creation_postcheck_failure_is_cleaned_by_original_handle(
    tmp_path, monkeypatch, cleanup_fails
):
    info = user_files._info

    def fail_file_info(kernel, handle):
        result = info(kernel, handle)
        if not result[1] & 0x10:
            raise OSError("injected file metadata failure after creation")
        return result

    monkeypatch.setattr(user_files, "_info", fail_file_info)
    if cleanup_fails:

        def fail_dispose(*_):
            raise OSError("injected owned handle cleanup failure")

        monkeypatch.setattr(user_files, "_delete_handle", fail_dispose)

    async def run():
        device = Device()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        assert job.view().result == ("failed" if cleanup_fails else "not sent")
        assert bool(job.cleanup_error) == cleanup_fails
        if cleanup_fails:
            assert job.view().exit_code() == 3
            assert str(job.local.parent) in job.cleanup_error
            assert "injected file metadata failure" in job.cleanup_error
            assert "injected owned handle cleanup failure" in job.cleanup_error
        assert len(list(tmp_path.iterdir())) == int(cleanup_fails)
        assert not device.commands
        assert not job.manager.targets

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["file.begin", "file.chunk"])
def test_ended_device_receipt_preserves_cleanup_without_cancel(tmp_path, phase):
    class ClosedDevice(Device):
        async def call(self, command, parameters, **options):
            if command == phase:
                self.commands.append(phase)
                return Result(
                    command="pull",
                    result="failed",
                    reason="device file failure",
                    data={
                        "job": "f-original",
                        "resourcesActive": False,
                        "cleanupError": "original device close failure",
                    },
                )
            return await super().call(command, parameters, **options)

    async def run():
        device = ClosedDevice()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        value = job.view()
        assert value.result == "failed"
        assert value.exit_code() == 3
        assert value.reason == "device file failure"
        assert value.data["cleanupError"] == "original device close failure"
        assert not value.data["resourcesActive"]
        assert "file.cancel" not in device.commands
        assert not list(tmp_path.iterdir())
        assert not job.manager.targets

    asyncio.run(run())


def test_cleanup_reply_without_resource_fact_remains_unconfirmed(tmp_path):
    class UnconfirmedDevice(Device):
        async def call(self, command, parameters, **options):
            if command == "file.cancel":
                self.commands.append("file.cancel")
                return Result(
                    command="file cancel", result="not sent", reason="query refused"
                )
            value = await super().call(command, parameters, **options)
            if command == "file.finish":
                value.data["resourcesActive"] = True
            return value

    async def run():
        device = UnconfirmedDevice()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        value = job.view()
        assert value.result == "done"
        assert value.exit_code() == 3
        assert value.data["cleanupError"] == "Device cleanup unconfirmed: query refused"
        assert device.commands[-1] == "file.cancel"
        assert device.commands.count("file.cancel") == 1
        assert not value.data["resourcesActive"]
        assert (tmp_path / "final").read_bytes() == device.body

    asyncio.run(run())


async def test_stop_during_cleanup_keeps_queries_until_device_resources_end(
    tmp_path,
):
    entered, leave = asyncio.Event(), asyncio.Event()

    class PendingCleanup(Device):
        async def call(self, command, parameters, **options):
            if command == "file.cancel":
                self.commands.append("file.cancel")
                entered.set()
                await leave.wait()
                return completed("pull", {"resourcesActive": True})
            if command == "file.get" and entered.is_set():
                self.commands.append("file.get")
                return completed("pull", {"resourcesActive": False})
            value = await super().call(command, parameters, **options)
            if command == "file.finish":
                value.data["resourcesActive"] = True
            return value

    device = PendingCleanup()
    job = start(FileJobs(), tmp_path, remote=device.call)
    await asyncio.wait_for(entered.wait(), 3)
    job.request_stop()
    assert job.view().data["resourcesActive"] is True
    leave.set()
    await asyncio.wait_for(asyncio.shield(job.task), 3)
    assert device.commands[-2:] == ["file.cancel", "file.get"]
    result = job.view()
    assert result.result == "done"
    assert result.data["stopRequested"] is True
    assert result.data["resourcesActive"] is False
    assert "cleanupError" not in result.data
    assert (tmp_path / "final").read_bytes() == device.body
