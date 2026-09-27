import asyncio
import base64
import hashlib

import pytest

from androidctl import file_jobs
from androidctl.file_jobs import FileJob, FileJobs
from androidctl.protocol import Problem, Result, completed
from androidctl.user_files import Directory
from file_test_support import Device, start

PUBLICATION_BYTES = b"complete publication\n"
PUBLICATION_HASH = hashlib.sha256(PUBLICATION_BYTES).hexdigest()


class PushDevice(Device):
    async def call(self, command, parameters, **options):
        if command == "file.chunk":
            self.commands.append(command)
            return completed(
                "file chunk",
                {
                    "offset": parameters["offset"]
                    + len(base64.b64decode(parameters["bytes"]))
                },
            )
        if command == "file.finish":
            self.commands.append(command)
            self.fact = completed(
                "push",
                {
                    "job": "f-original",
                    "size": len(self.body),
                    "sha256": hashlib.sha256(self.body).hexdigest(),
                    "resourcesActive": False,
                },
            )
            return self.fact
        return await super().call(command, parameters, **options)


def assert_released(job):
    assert job.view().data["resourcesActive"] is False
    assert not job.manager.targets
    assert not list(job.local.parent.glob(".androidctl-*.tmp"))


def assert_internal_diagnostic(value, job):
    assert value.error.code == "internal"
    assert value.error.stage.startswith("host.file.")
    assert "finish defect" in value.reason
    assert value.data["job"] == job.id
    assert value.data["deviceJob"] == "f-original"
    assert_released(job)


async def test_defect_before_finish_keeps_pull_unpublished(tmp_path, monkeypatch):
    async def fail_finish(job, digest):
        raise RuntimeError("finish defect")

    monkeypatch.setattr(FileJob, "finish", fail_finish)
    device = Device(PUBLICATION_BYTES)
    job = start(FileJobs(), tmp_path, remote=device.call)
    await job.task
    value = job.manager.find(job.id, "device", "pair", "session").view()
    assert value.result == "failed"
    assert value.exit_code() == 1
    assert_internal_diagnostic(value, job)
    assert "file.finish" not in device.commands
    assert not job.local.exists()


async def test_defect_after_publish_permission_keeps_pull_unknown(
    tmp_path, monkeypatch
):
    original_publish = Directory.publish

    def publish(parent, temporary, name, expected, *, replace, before_publish):
        def permit_then_fail():
            before_publish()
            raise RuntimeError("finish defect")

        return original_publish(
            parent,
            temporary,
            name,
            expected,
            replace=replace,
            before_publish=permit_then_fail,
        )

    monkeypatch.setattr(Directory, "publish", publish)
    device = Device(PUBLICATION_BYTES)
    job = start(FileJobs(), tmp_path, remote=device.call)
    await job.task
    value = job.view()
    assert value.result == "UNKNOWN"
    assert value.exit_code() == 3
    assert_internal_diagnostic(value, job)
    assert device.commands.count("file.finish") == 1
    assert not job.local.exists()


async def test_result_construction_defect_keeps_published_pull(tmp_path, monkeypatch):
    complete = file_jobs.completed

    def fail_once(command, data, **kwargs):
        monkeypatch.setattr(file_jobs, "completed", complete)
        raise RuntimeError("finish defect")

    monkeypatch.setattr(file_jobs, "completed", fail_once)
    device = Device(PUBLICATION_BYTES)
    job = start(FileJobs(), tmp_path, remote=device.call)
    await job.task
    value = job.view()
    assert value.result == "done"
    assert value.exit_code() == 3
    assert_internal_diagnostic(value, job)
    assert (value.data["size"], value.data["sha256"]) == (
        len(PUBLICATION_BYTES),
        PUBLICATION_HASH,
    )
    assert job.local.read_bytes() == PUBLICATION_BYTES
    assert device.commands.count("file.finish") == 1


async def test_finish_defect_with_weak_cleanup_keeps_push_unknown(tmp_path):
    class DefectiveFinish(PushDevice):
        async def call(self, command, parameters, **options):
            if command == "file.finish":
                self.commands.append("file.finish")
                raise RuntimeError("finish defect")
            return await super().call(command, parameters, **options)

    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = DefectiveFinish(PUBLICATION_BYTES)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await job.task
    value = job.view()
    assert value.result == "UNKNOWN"
    assert value.exit_code() == 3
    assert_internal_diagnostic(value, job)
    assert device.commands.count("file.finish") == 1
    assert source.read_bytes() == PUBLICATION_BYTES


async def test_finish_defect_with_strong_cleanup_keeps_confirmed_push(tmp_path):
    class ConfirmedOnCancel(PushDevice):
        async def call(self, command, parameters, **options):
            if command == "file.finish":
                self.commands.append(command)
                raise RuntimeError("finish defect")
            if command == "file.cancel":
                self.commands.append(command)
                return completed(
                    "push",
                    {
                        "job": "f-original",
                        "size": len(PUBLICATION_BYTES),
                        "sha256": PUBLICATION_HASH,
                        "resourcesActive": False,
                    },
                )
            return await super().call(command, parameters, **options)

    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = ConfirmedOnCancel(PUBLICATION_BYTES)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await job.task
    value = job.view()
    assert value.result == "done"
    assert value.exit_code() == 3
    assert_internal_diagnostic(value, job)
    assert "Original push confirmed during cleanup" in value.reason
    assert (value.data["size"], value.data["sha256"]) == (
        len(PUBLICATION_BYTES),
        PUBLICATION_HASH,
    )
    assert device.commands.count("file.finish") == 1
    assert source.read_bytes() == PUBLICATION_BYTES


async def test_result_construction_defect_keeps_confirmed_push(tmp_path, monkeypatch):
    complete = file_jobs.completed

    def fail_once(command, data, **kwargs):
        monkeypatch.setattr(file_jobs, "completed", complete)
        raise RuntimeError("finish defect")

    monkeypatch.setattr(file_jobs, "completed", fail_once)
    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = PushDevice(PUBLICATION_BYTES)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await job.task
    value = job.view()
    assert value.result == "done"
    assert value.exit_code() == 3
    assert_internal_diagnostic(value, job)
    assert (value.data["size"], value.data["sha256"]) == (
        len(PUBLICATION_BYTES),
        PUBLICATION_HASH,
    )
    assert source.read_bytes() == PUBLICATION_BYTES
    assert device.commands.count("file.finish") == 1
    assert "file.cancel" not in device.commands


@pytest.mark.parametrize("direction", ["push", "pull"])
@pytest.mark.parametrize("receipt_job", [None, "different"])
async def test_normal_finish_accepts_matching_size_hash_without_matching_job(
    tmp_path, direction, receipt_job
):
    device = (
        PushDevice(PUBLICATION_BYTES)
        if direction == "push"
        else Device(PUBLICATION_BYTES)
    )

    async def remote(command, parameters, **options):
        result = await device.call(command, parameters, **options)
        if command == "file.finish":
            if receipt_job is None:
                del result.data["job"]
            else:
                result.data["job"] = receipt_job
        return result

    local = tmp_path / "final"
    if direction == "push":
        local.write_bytes(PUBLICATION_BYTES)
    job = start(FileJobs(), tmp_path, remote=remote, direction=direction)
    await job.task
    assert job.view().result == "done"
    assert job.view().data["sha256"] == PUBLICATION_HASH
    assert local.read_bytes() == PUBLICATION_BYTES
    assert_released(job)


@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_finish_metadata_mismatch_keeps_direction_publication_fact(
    tmp_path, direction
):
    device = (
        PushDevice(PUBLICATION_BYTES)
        if direction == "push"
        else Device(PUBLICATION_BYTES)
    )

    async def remote(command, parameters, **options):
        result = await device.call(command, parameters, **options)
        if command == "file.finish":
            result.data["sha256"] = "0" * 64
        return result

    local = tmp_path / "final"
    if direction == "push":
        local.write_bytes(PUBLICATION_BYTES)
    job = start(FileJobs(), tmp_path, remote=remote, direction=direction)
    await job.task
    assert job.view().result == ("UNKNOWN" if direction == "push" else "failed")
    assert job.view().error.code == "hash"
    assert local.exists() is (direction == "push")
    assert_released(job)


@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_digest_construction_defect_remains_in_opening_stage(
    tmp_path, monkeypatch, direction
):
    def fail_digest():
        raise RuntimeError("digest construction defect")

    monkeypatch.setattr(file_jobs.hashlib, "sha256", fail_digest)

    async def remote(command, parameters, **options):
        if command == "file.begin":
            return Result(
                command=direction,
                result="still running",
                data={"job": "f-original", "ready": True, "size": 0},
            )
        assert command == "file.cancel"
        return completed(direction, {"resourcesActive": False})

    local = tmp_path / "final"
    if direction == "push":
        local.write_bytes(b"")
    job = start(FileJobs(), tmp_path, remote=remote, direction=direction)
    await job.task
    result = job.view()
    assert result.result == "failed"
    assert result.error.code == "internal"
    assert result.error.stage == "host.file.opening"
    assert_released(job)


class PendingFinish(Device):
    async def call(self, command, parameters, **options):
        if command == "file.cancel":
            self.commands.append(command)
            return completed("push", {"job": "f-original", "resourcesActive": False})
        result = await super().call(command, parameters, **options)
        if command == "file.finish":
            return Result(
                command="push", result="still running", data={"job": "f-original"}
            )
        return result


@pytest.mark.parametrize("direction", ["push", "pull"])
@pytest.mark.parametrize(
    ("reply_at", "original_job"),
    [("finish", False), ("query", False), ("query", True)],
)
async def test_finish_unknown_preserves_device_or_query_diagnostic(
    tmp_path, direction, reply_at, original_job
):
    problem = Problem(
        code="deviceIo" if original_job else "connectionFailed",
        message="Original diagnostic",
        stage="device.file" if original_job else "tcp",
        diagnostic_truncated=True,
    )
    diagnostic = Result(
        command=direction if original_job else "file.get",
        result="UNKNOWN",
        reason="Original failure reason",
        error=problem,
        data={"job": "f-original", "resourcesActive": False} if original_job else {},
    )

    class UnknownFinish(PendingFinish):
        async def call(self, command, parameters, **options):
            if command == "file.get" and self.fact is not None:
                self.commands.append(command)
                return diagnostic
            result = await super().call(command, parameters, **options)
            if command == "file.finish" and reply_at == "finish":
                return diagnostic
            return result

    local = tmp_path / "final"
    if direction == "push":
        local.write_bytes(b"")
    device = UnknownFinish(body=b"")
    job = start(FileJobs(), tmp_path, remote=device.call, direction=direction)
    await job.task
    value = job.manager.find(job.id, "device", "pair", "session").view()
    expected_reason = "Original failure reason"
    if reply_at == "query" and not original_job:
        expected_reason = (
            "Device finish was not confirmed by subsequent query; " + expected_reason
        )
    if direction == "pull":
        expected_reason += "; target not published"
    assert value.result == ("UNKNOWN" if direction == "push" else "failed")
    assert value.exit_code() == (3 if direction == "push" else 1)
    assert value.reason == expected_reason
    assert value.error.wire() == problem.wire()
    assert value.data["job"] == job.id
    assert value.data["deviceJob"] == "f-original"
    assert diagnostic.reason == "Original failure reason"
    assert device.commands.count("file.finish") == 1
    assert device.commands.count("file.get") == (2 if reply_at == "query" else 1)
    assert local.exists() is (direction == "push")
    assert_released(job)


async def test_finish_query_local_cancellation_is_unknown_and_never_resends_finish(
    tmp_path,
):
    class CancelledAfterFinish(PendingFinish):
        async def call(self, command, parameters, **options):
            result = await super().call(command, parameters, **options)
            if command == "file.finish":
                job.request_stop()
            return result

    device = CancelledAfterFinish(body=b"")
    manager = FileJobs()
    (tmp_path / "source").write_bytes(b"")
    job = start(
        manager,
        tmp_path,
        remote=device.call,
        direction="push",
        local=str(tmp_path / "source"),
    )
    await job.task
    assert job.view().result == "UNKNOWN"
    assert job.view().reason == (
        "Device finish was not confirmed by subsequent query; "
        "Transfer cancelled or deadline reached"
    )
    assert job.view().error.code == "cancelled"
    assert job.view().data["stopRequested"]
    assert device.commands.count("file.finish") == 1
    assert job.view().data["job"] == job.id
    assert job.ended


async def test_rejected_finish_query_is_unknown_and_never_resends_finish(tmp_path):
    query_result = Result(
        command="file.get",
        result="not sent",
        reason="query unavailable after publication",
        error=Problem(code="connectionBusy", message="query unavailable"),
    )

    class RejectedQuery(PendingFinish):
        async def call(self, command, parameters, **options):
            if command == "file.get" and self.fact is not None:
                self.commands.append("file.get")
                return query_result
            return await super().call(command, parameters, **options)

    device = RejectedQuery(body=b"")
    (tmp_path / "source").write_bytes(b"")
    job = start(
        FileJobs(),
        tmp_path,
        remote=device.call,
        direction="push",
        local=str(tmp_path / "source"),
    )
    await job.task
    assert job.view().result == "UNKNOWN"
    assert job.view().reason == (
        "Device finish was not confirmed by subsequent query; "
        "query unavailable after publication"
    )
    assert job.view().error == query_result.error
    assert query_result.result == "not sent"
    assert query_result.reason == "query unavailable after publication"
    assert device.commands.count("file.finish") == 1
    assert job.view().data["job"] == job.id
    assert job.ended


def test_finish_rejected_before_send_is_failed_not_unknown(tmp_path):
    class NotSentFinish(Device):
        async def call(self, command, parameters, **options):
            if command == "file.finish":
                self.commands.append("file.finish")
                return Result(
                    command="file.finish",
                    result="not sent",
                    reason="zero-send finish",
                    error=Problem(code="connectionBusy", message="zero-send finish"),
                )
            return await super().call(command, parameters, **options)

    async def run():
        device = NotSentFinish(b"")
        (tmp_path / "source").write_bytes(b"")
        job = start(
            FileJobs(),
            tmp_path,
            remote=device.call,
            direction="push",
            local=str(tmp_path / "source"),
        )
        await job.task
        assert job.view().result == "failed"
        assert job.view().reason == "zero-send finish"
        assert job.view().error.code == "connectionBusy"
        assert device.commands.count("file.finish") == 1
        assert job.ended

    asyncio.run(run())


class CleanupReceipt(PushDevice):
    def __init__(self, receipt):
        super().__init__(PUBLICATION_BYTES)
        self.receipt = receipt
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, command, parameters, **options):
        if command == "file.finish":
            self.commands.append(command)
            return Result(command="push", result="UNKNOWN")
        if command == "file.cancel":
            self.commands.append(command)
            self.arrived.set()
            await self.release.wait()
            return self.receipt
        return await super().call(command, parameters, **options)


@pytest.mark.parametrize(
    ("receipt", "expected_result"),
    [
        pytest.param(
            completed(
                "push",
                {
                    "job": "f-original",
                    "size": len(PUBLICATION_BYTES),
                    "sha256": PUBLICATION_HASH,
                    "resourcesActive": False,
                },
            ),
            "done",
            id="confirmed",
        ),
        pytest.param(
            completed(
                "push",
                {
                    "job": "f-original",
                    "size": len(PUBLICATION_BYTES),
                    "resourcesActive": False,
                },
            ),
            "UNKNOWN",
            id="missing-hash",
        ),
        pytest.param(
            completed(
                "push",
                {
                    "job": "another",
                    "size": len(PUBLICATION_BYTES),
                    "sha256": PUBLICATION_HASH,
                    "resourcesActive": False,
                },
            ),
            "UNKNOWN",
            id="different-job",
        ),
        pytest.param(
            completed(
                "push",
                {
                    "job": "f-original",
                    "size": len(PUBLICATION_BYTES) + 1,
                    "sha256": PUBLICATION_HASH,
                    "resourcesActive": False,
                },
            ),
            "UNKNOWN",
            id="different-size",
        ),
        pytest.param(
            completed(
                "push",
                {
                    "job": "f-original",
                    "size": len(PUBLICATION_BYTES),
                    "sha256": "0" * 64,
                    "resourcesActive": False,
                },
            ),
            "UNKNOWN",
            id="different-hash",
        ),
        pytest.param(
            Result(
                command="push",
                result="failed",
                data={
                    "job": "f-original",
                    "size": len(PUBLICATION_BYTES),
                    "sha256": PUBLICATION_HASH,
                    "resourcesActive": False,
                },
            ),
            "UNKNOWN",
            id="not-completed",
        ),
    ],
)
async def test_cleanup_receipt_converges_only_the_original_push(
    tmp_path, receipt, expected_result
):
    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = CleanupReceipt(receipt)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await asyncio.wait_for(device.arrived.wait(), 2)
    delivered = job.view()
    assert delivered.result == "UNKNOWN"
    device.release.set()
    await job.task
    final = job.view()
    assert final.result == expected_result
    assert (final.reason == "Original push confirmed during cleanup") is (
        expected_result == "done"
    )
    assert final.exit_code() == (0 if expected_result == "done" else 3)
    assert delivered.result == "UNKNOWN"
    assert device.commands == [
        "file.begin",
        "file.get",
        "file.chunk",
        "file.finish",
        "file.cancel",
    ]
    assert_released(job)


async def test_cleanup_error_after_strong_receipt_preserves_confirmed_push(tmp_path):
    class RevokedAfterReceipt(CleanupReceipt):
        async def call(self, command, parameters, **options):
            if command == "file.get" and self.arrived.is_set():
                self.commands.append("file.get")
                return Result(
                    command="file.get",
                    result="not sent",
                    reason="cleanup authorization revoked after receipt",
                    error=Problem(
                        code="revoked",
                        message="cleanup authorization revoked after receipt",
                    ),
                )
            return await super().call(command, parameters, **options)

    receipt = completed(
        "push",
        {
            "job": "f-original",
            "size": len(PUBLICATION_BYTES),
            "sha256": PUBLICATION_HASH,
            "resourcesActive": True,
        },
    )
    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = RevokedAfterReceipt(receipt)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await asyncio.wait_for(device.arrived.wait(), 2)
    delivered = job.view()
    assert delivered.result == "UNKNOWN"
    device.release.set()
    await job.task
    final = job.view()
    assert final.result == "done"
    assert final.reason == "Original push confirmed during cleanup"
    assert "cleanup authorization revoked" in final.data["cleanupError"]
    assert final.exit_code() == 3
    assert delivered.result == "UNKNOWN"
    assert device.commands == [
        "file.begin",
        "file.get",
        "file.chunk",
        "file.finish",
        "file.cancel",
        "file.get",
    ]
    assert_released(job)


async def test_cleanup_query_can_confirm_after_weak_cancel_receipt(tmp_path):
    class ConfirmedOnQuery(CleanupReceipt):
        async def call(self, command, parameters, **options):
            if command == "file.get" and self.arrived.is_set():
                self.commands.append("file.get")
                return completed(
                    "push",
                    {
                        "job": "f-original",
                        "size": len(PUBLICATION_BYTES),
                        "sha256": PUBLICATION_HASH,
                        "resourcesActive": False,
                    },
                )
            return await super().call(command, parameters, **options)

    receipt = completed(
        "push",
        {"job": "f-original", "size": len(PUBLICATION_BYTES), "resourcesActive": True},
    )
    source = tmp_path / "source"
    source.write_bytes(PUBLICATION_BYTES)
    device = ConfirmedOnQuery(receipt)
    job = start(
        FileJobs(), tmp_path, remote=device.call, direction="push", local=str(source)
    )
    await asyncio.wait_for(device.arrived.wait(), 2)
    delivered = job.view()
    assert delivered.result == "UNKNOWN"
    device.release.set()
    await job.task
    final = job.view()
    assert final.result == "done"
    assert final.reason == "Original push confirmed during cleanup"
    assert final.exit_code() == 0
    assert delivered.result == "UNKNOWN"
    assert device.commands == [
        "file.begin",
        "file.get",
        "file.chunk",
        "file.finish",
        "file.cancel",
        "file.get",
    ]
    assert_released(job)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("cleanup_lost", [False, True])
def test_pull_lost_finish_preserves_unpublished_destination(
    tmp_path, existing, cleanup_lost
):
    target = tmp_path / "final"
    if existing:
        target.write_bytes(b"old target")

    class Lost(Device):
        async def call(self, command, parameters, **options):
            if command == "file.finish":
                self.commands.append(command)
                return Result(command="pull", result="UNKNOWN")
            if command == "file.cancel" and cleanup_lost:
                self.commands.append(command)
                return Result(
                    command=command,
                    result="UNKNOWN",
                    reason="cleanup reply unavailable",
                    error=Problem(code="offline", message="cleanup reply unavailable"),
                )
            return await super().call(command, parameters, **options)

    async def run():
        device = Lost()
        job = start(FileJobs(), tmp_path, remote=device.call, replace=existing)
        await job.task
        result = job.view()
        assert result.result == "failed"
        assert result.reason == "target not published"
        assert result.exit_code() == (3 if cleanup_lost else 1)
        assert target.read_bytes() == b"old target" if existing else not target.exists()
        assert device.commands.count("file.finish") == 1
        assert not list(tmp_path.glob(".androidctl-*.tmp"))

    asyncio.run(run())
