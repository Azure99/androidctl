import asyncio
import base64
import hashlib
import os

import pytest

from androidctl import file_jobs
from androidctl.file_jobs import FileJob, FileJobs
from androidctl.protocol import Problem, Result, completed
from file_test_support import Device, start


@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_cancel_before_begin_call_keeps_zero_dispatch_fact(
    tmp_path, monkeypatch, direction
):
    original_call = FileJob.call

    async def cancel_before_begin(job, method, parameters, **kwargs):
        if method == "file.begin":
            job.request_stop()
        return await original_call(job, method, parameters, **kwargs)

    monkeypatch.setattr(FileJob, "call", cancel_before_begin)
    local = tmp_path / "final"
    local.write_bytes(b"unchanged")
    device = Device()
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction=direction, replace=True
    )
    await job.task
    result = job.view()
    assert result.result == "not sent"
    assert result.exit_code() == 2
    assert result.error.code == "cancelled"
    assert result.data["stopRequested"]
    assert result.data["resourcesActive"] is False
    assert "deviceJob" not in result.data
    assert not device.commands
    assert local.read_bytes() == b"unchanged"
    assert not list(tmp_path.glob(".androidctl-*.tmp"))


@pytest.mark.parametrize(
    ("phase", "stage"), [("file.begin", "opening"), ("file.chunk", "copying")]
)
def test_unexpected_open_copy_failure_has_terminal_original_identity(
    tmp_path, phase, stage
):
    class DefectiveDevice(Device):
        async def call(self, command, parameters, **options):
            if command == phase:
                self.commands.append(phase)
                raise RuntimeError("unexpected transfer defect")
            return await super().call(command, parameters, **options)

    async def run():
        device = DefectiveDevice()
        manager = FileJobs()
        job = start(manager, tmp_path, remote=device.call)
        await job.task
        value = manager.find(job.id, "device", "pair", "session").view()
        assert value.result == "failed"
        assert value.error.wire() == {
            "code": "internal",
            "message": "unexpected transfer defect",
            "stage": f"host.file.{stage}",
        }
        assert value.data["job"] == job.id
        assert value.data.get("deviceJob") == (
            "f-original" if phase == "file.chunk" else None
        )
        assert value.data["resourcesActive"] is False
        assert device.commands.count(phase) == 1
        assert "file.finish" not in device.commands
        assert not list(tmp_path.iterdir())
        assert not manager.targets

    asyncio.run(run())


@pytest.mark.parametrize(
    ("body", "declared_size", "metadata_changed"),
    [
        (b"", None, False),
        (b"ordinary contents", None, False),
        (b"extra contents", 0, False),
        (b"extra contents", 5, False),
        (b"ordinary contents", None, True),
    ],
)
def test_push_verifies_source_before_publishing(
    tmp_path, monkeypatch, body, declared_size, metadata_changed
):
    source, target = tmp_path / "source", tmp_path / "device-target"
    source.write_bytes(body)
    target.write_bytes(b"preserve target")
    original = file_jobs.fingerprint

    def metadata(stream):
        value = original(stream)
        return value if declared_size is None else value._replace(size=declared_size)

    monkeypatch.setattr(file_jobs, "fingerprint", metadata)

    async def run():
        received, commands = bytearray(), []

        async def remote(
            command,
            parameters,
            *,
            session,
            deadline,
            before_send=None,
            exchange_seconds=None,
        ):
            if before_send:
                before_send()

            commands.append(command)
            if command == "file.begin":
                return Result(
                    command="push",
                    result="still running",
                    data={"job": "original", "ready": True, "size": parameters["size"]},
                )
            if command == "file.chunk":
                received.extend(base64.b64decode(parameters["bytes"], validate=True))
                if metadata_changed:
                    initial = source.stat()
                    os.utime(
                        source,
                        ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000),
                    )
                return completed(command, {"offset": len(received)})
            if command == "file.finish":
                target.write_bytes(received)
                return completed(
                    "push",
                    {
                        "job": "original",
                        "size": len(received),
                        "sha256": hashlib.sha256(received).hexdigest(),
                        "resourcesActive": False,
                    },
                )
            assert command == "file.cancel"
            return completed(command, {"resourcesActive": False})

        manager = FileJobs()
        job = start(
            manager,
            tmp_path,
            remote=remote,
            direction="push",
            local=str(source),
            replace=True,
        )
        await job.task
        result = job.view()
        if declared_size is None and not metadata_changed:
            assert result.result == "done"
            assert target.read_bytes() == body
            assert commands.count("file.finish") == 1
        else:
            assert result.result == "failed"
            assert result.error.code == "sourceChanged"
            assert target.read_bytes() == b"preserve target"
            assert "file.finish" not in commands
            assert commands[-1] == "file.cancel"
        assert source.read_bytes() == body
        assert result.data["resourcesActive"] is False
        assert job.stream is None
        assert not manager.targets

    asyncio.run(run())


@pytest.mark.parametrize(
    "phase", ["file.begin", "file.get", "file.chunk", "file.finish"]
)
def test_device_problem_survives_host_transfer_classification(tmp_path, phase):
    problem = Problem(
        code="fileExists" if phase in ("file.begin", "file.get") else "deviceIo",
        message="Device diagnostic",
        stage="device.file",
        diagnostic_truncated=True,
    )

    class FailingDevice(Device):
        async def call(self, command, parameters, **options):
            if command == phase:
                self.commands.append(phase)
                return Result(
                    command=phase,
                    result=(
                        "not sent" if phase in ("file.begin", "file.get") else "failed"
                    ),
                    reason="Transfer rejected",
                    error=problem,
                    data={
                        **({"job": "f-original"} if phase != "file.begin" else {}),
                        "resourcesActive": False,
                    },
                )
            return await super().call(command, parameters, **options)

    async def run():
        device = FailingDevice()
        job = start(FileJobs(), tmp_path, remote=device.call)
        await job.task
        result = job.view()
        assert result.error == problem
        assert result.reason.startswith("Transfer rejected")
        assert result.result == (
            "not sent" if phase in ("file.begin", "file.get") else "failed"
        )
        assert not result.data["resourcesActive"]
        assert not (tmp_path / "final").exists()
        assert not list(tmp_path.glob(".androidctl-*.tmp"))
        assert device.commands.count(phase) == 1

    asyncio.run(run())


@pytest.mark.parametrize("direction", ["push", "pull"])
@pytest.mark.parametrize(
    "failure", ["offline", "lost", "before-open", "after-open", "query-not-sent"]
)
def test_begin_classification_uses_device_facts_and_actual_cleanup(
    tmp_path, direction, failure
):
    class BeginFailure(Device):
        async def call(self, command, parameters, **options):
            if command == "file.get" and failure == "query-not-sent":
                self.commands.append(command)
                return Result(
                    command=command,
                    result="not sent",
                    reason="Device query was not dispatched",
                    error=Problem(code="connectionBusy", message="Query unavailable"),
                )
            if command == "file.begin":
                self.commands.append(command)
                if failure == "offline":
                    return Result(
                        command=command,
                        result="not sent",
                        reason="offline",
                        error=Problem(
                            code="connectionFailed", message="offline", stage="tcp"
                        ),
                    )
                if failure == "lost":
                    return Result(command=direction, result="UNKNOWN")
                if failure == "query-not-sent":
                    return Result(
                        command=direction,
                        result="still running",
                        data={"job": "f-original"},
                    )
                return Result(
                    command=direction,
                    result="not sent" if failure == "before-open" else "failed",
                    reason="injected open failure",
                    data={
                        "job": "f-original",
                        "resourcesActive": False,
                    },
                )
            return await super().call(command, parameters, **options)

    async def run():
        source = tmp_path / "source"
        source.write_bytes(b"source")
        device = BeginFailure()
        job = start(
            FileJobs(),
            tmp_path,
            remote=device.call,
            direction=direction,
            local=str(source if direction == "push" else tmp_path / "final"),
        )
        await job.task
        expected = "not sent" if failure in ("offline", "before-open") else "failed"
        assert job.view().result == expected
        assert job.view().data["resourcesActive"] is False
        assert bool(job.device_job_id) == (failure not in ("offline", "lost"))
        if failure == "query-not-sent":
            assert device.commands.count("file.cancel") == 1
            assert job.view().error.code == "connectionBusy"
        assert device.commands.count("file.begin") == 1
        assert "file.chunk" not in device.commands
        assert not (tmp_path / "final").exists()
        assert source.read_bytes() == b"source"
        assert not list(tmp_path.glob(".androidctl-*.tmp"))

    asyncio.run(run())
