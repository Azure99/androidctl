from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any, BinaryIO, Literal, NamedTuple, Protocol, TypeVar

from .private_files import check_private
from .protocol import (
    CHUNK_BYTES,
    LOCAL_JOB_PREFIX,
    MAX_RUN_MS,
    RECENT_JOBS,
    RPC_SECONDS,
    UNCERTAIN_EXIT,
    Fault,
    Problem,
    Result,
    completed,
    decode_chunk,
    uncertain,
)
from .user_files import DestinationKey, Directory, FileIdentity, file_path, write_all

T = TypeVar("T")
CLEANUP_SECONDS = 5.0


class Remote(Protocol):
    async def __call__(
        self,
        command: str,
        parameters: dict[str, Any],
        *,
        session: str,
        deadline: float,
        before_send: Callable[[], None] | None = None,
        exchange_seconds: float | None = None,
    ) -> Result: ...


class FileFingerprint(NamedTuple):
    filesystem: int
    inode: int
    size: int
    modified_ns: int
    ctime_ns: int


def fingerprint(stream: BinaryIO) -> FileFingerprint:
    value = os.fstat(stream.fileno())
    return FileFingerprint(
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class FileJob:
    def __init__(
        self,
        manager: FileJobs,
        device: str,
        pairing: str,
        session: str,
        args: dict[str, Any],
        remote: Remote,
    ):
        self.manager, self.device, self.pairing, self.session = (
            manager,
            device,
            pairing,
            session,
        )
        self.id = LOCAL_JOB_PREFIX + uuid.uuid4().hex
        self.remote = remote
        self.direction = args["direction"]
        self.local = file_path(args["local"])
        self.remote_path = args["remote"]
        self.replace = args.get("replace", False)
        self.deadline = time.monotonic() + args.get("runMs", MAX_RUN_MS) / 1000
        self.cancelled = False
        self.ended = False
        self.offset = 0
        self.size = 0
        self.device_job_id: str | None = None
        self.device_ended = False
        self.device_evidence: Literal[
            "absent",
            "begin-entered",
            "finish-entered",
            "finish-reported",
            "finish-verified",
        ] = "absent"
        self.parent: Directory | None = None
        self.stream: BinaryIO | None = None
        self.temporary = ".androidctl-" + uuid.uuid4().hex + ".tmp"
        self.owned: FileIdentity | None = None
        self.local_publication: Literal["unchanged", "attempted", "published"] = (
            "unchanged"
        )
        self.target_key: DestinationKey | None = None
        self.stage = "opening"
        self.fact: Result | None = None
        self.cleanup_error: str | None = None
        self.expected_digest: str | None = None
        self.task: asyncio.Task[None] | None = None

    def view(self) -> Result:
        value = (
            self.fact.model_copy(deep=True)
            if self.fact
            else Result(
                command=self.direction,
                result="still running",
                data={
                    "job": self.id,
                    "stage": self.stage,
                    "size": self.size,
                    "offset": self.offset,
                },
            )
        )
        value.data["resourcesActive"] = not self.ended
        if self.device_job_id:
            value.data["deviceJob"] = self.device_job_id
        if self.cleanup_error:
            value.data["cleanupError"] = self.cleanup_error
            value.exit = UNCERTAIN_EXIT
        if self.cancelled:
            value.data["stopRequested"] = True
        return value

    async def io(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        # The owner survives cancellation of a waiter until the actual I/O ends.
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self.cancelled = True

    def check(self) -> None:
        if self.cancelled or time.monotonic() >= self.deadline:
            raise Fault("cancelled", "Transfer cancelled or deadline reached")

    def request_stop(self) -> None:
        if not self.ended:
            self.cancelled = True

    async def call(
        self,
        method: str,
        parameters: dict[str, Any],
        *,
        cleanup_until: float | None = None,
    ) -> Result:
        if cleanup_until is None:
            self.check()
        result = await self.remote(
            method,
            parameters,
            session=self.session,
            deadline=cleanup_until or self.deadline,
            before_send=self.check if cleanup_until is None else None,
            # A run spans many requests; each exchange has its own finite wait.
            exchange_seconds=RPC_SECONDS,
        )
        if result.data.get("resourcesActive") is False:
            self.device_ended = True
        if result.data.get("cleanupError"):
            self.cleanup_failed(result.data["cleanupError"])
        return result

    def transfer_fault(self, result: Result, fallback: str | None = None) -> Fault:
        error = Fault("transfer", result.reason or fallback or result.result)
        if result.error is not None:
            error.problem = result.error
        return error

    def require_done(self, result: Result) -> dict[str, Any]:
        if result.result != "done":
            raise self.transfer_fault(result)
        return result.data

    async def chunk(self, parameters: dict[str, Any]) -> dict[str, Any]:
        result = await self.call(
            "file.chunk", {"job": self.device_job_id, **parameters}
        )
        while result.result == "still running":
            await asyncio.sleep(0.2)
            result = await self.call("file.get", {"job": self.device_job_id})
            step = result.data.get("step")
            if (
                result.result == "still running"
                and step
                and step["offset"] == parameters["offset"]
            ):
                result = Result.model_validate(step["result"])
        return self.require_done(result)

    async def begin(self) -> Result | None:
        self.check()
        self.device_evidence = "begin-entered"
        try:
            started = await self.call(
                "file.begin",
                {
                    "direction": self.direction,
                    "path": self.remote_path,
                    "replace": self.replace,
                    "size": self.size,
                    "runMs": max(1, int((self.deadline - time.monotonic()) * 1000)),
                },
            )
        except Fault:
            self.device_evidence = "absent"
            raise
        self.device_job_id = started.data.get("job")
        if started.result == "not sent":
            self.device_evidence = "absent"
        if not self.device_job_id:
            if self.device_evidence != "absent":
                return self.begin_unconfirmed(started.error)
            raise self.transfer_fault(started, "Device did not return a transfer ID")
        state = started
        while True:
            if state.result != "still running":
                if (
                    state.result == "not sent"
                    and state.data.get("job") == self.device_job_id
                ):
                    self.device_evidence = "absent"
                raise self.transfer_fault(state)
            if state.data.get("ready"):
                self.size = state.data["size"]
                return None
            await asyncio.sleep(0.1)
            state = await self.call("file.get", {"job": self.device_job_id})

    def begin_unconfirmed(self, problem: Problem | None) -> Result:
        return Result(
            command=self.direction,
            result="failed",
            reason=(
                "Device begin result was not received; transfer may exist "
                "on device; begin will not be repeated"
            ),
            error=problem,
            data={"job": self.id},
        )

    @property
    def opened(self) -> BinaryIO:
        assert self.stream is not None, "Transfer stream must be open"  # noqa: S101
        return self.stream

    def write(self, data: bytes) -> None:
        write_all(self.opened, data)

    async def push(self) -> Result:
        self.parent = await self.io(Directory, self.local.parent)
        self.stream = await self.io(self.parent.open, self.local.name)
        source = await self.io(fingerprint, self.stream)
        self.size = source.size
        unconfirmed_begin = await self.begin()
        if unconfirmed_begin is not None:
            return unconfirmed_begin

        digest = hashlib.sha256()
        self.stage = "copying"
        stream = self.opened
        while self.offset < self.size:
            self.check()
            data = await self.io(stream.read, min(CHUNK_BYTES, self.size - self.offset))
            if not data:
                raise Fault("sourceChanged", "Source ended before its declared size")
            block = await self.chunk(
                {"offset": self.offset, "bytes": base64.b64encode(data).decode()}
            )
            if block.get("offset") != self.offset + len(data):
                raise Fault("offset", "Device returned an unexpected file offset")
            digest.update(data)
            self.offset += len(data)
        if await self.io(stream.read, 1):
            raise Fault(
                "sourceChanged", "Source contains bytes beyond its declared size"
            )
        if await self.io(fingerprint, stream) != source:
            raise Fault("sourceChanged", "Source metadata changed during transfer")

        result = await self.finish(digest.hexdigest())
        if result.result == "done":
            self.verify_finish(result)
        return self.finish_result(result)

    async def pull(self) -> Result:
        self.parent = await self.io(Directory, self.local.parent)
        self.target_key = self.parent.key(self.local.name)
        self.manager.occupy(self.target_key, self.id)
        if not self.replace and await self.io(self.parent.exists, self.local.name):
            raise Fault("fileExists", "Destination exists; choose --replace explicitly")
        self.stream, self.owned = await self.io(self.parent.create, self.temporary)
        if sys.platform == "win32":
            await self.io(check_private, self.parent.path / self.temporary)
        unconfirmed_begin = await self.begin()
        if unconfirmed_begin is not None:
            return unconfirmed_begin

        digest = hashlib.sha256()
        self.stage = "copying"
        while self.offset < self.size:
            self.check()
            block = await self.chunk({"offset": self.offset})
            data = decode_chunk(block["bytes"], self.offset, self.size, code="chunk")
            if block.get("offset") != self.offset:
                raise Fault("chunk", "Invalid file chunk")
            await self.io(self.write, data)
            digest.update(data)
            self.offset += len(data)

        self.expected_digest = digest.hexdigest()
        self.stage = "finishing"
        await self.io(self.flush)
        result = await self.finish(self.expected_digest)
        if result.result == "done":
            self.verify_finish(result)
            self.stage = "publishing"
            self.check()
            await self.io(self.publish)
        return self.finish_result(result)

    def verify_finish(self, result: Result) -> None:
        if (result.data.get("size"), result.data.get("sha256")) != (
            self.size,
            self.expected_digest,
        ):
            raise Fault("hash", "Device finish metadata does not match")
        self.device_evidence = "finish-verified"

    def flush(self) -> None:
        stream = self.opened
        stream.flush()
        os.fsync(stream.fileno())

    def publish(self) -> None:
        assert (  # noqa: S101, PT018
            self.parent is not None and self.owned is not None
        ), "Pull must create its temporary file before publication"

        def before_publish() -> None:
            self.check()
            self.local_publication = "attempted"

        self.parent.publish(
            self.temporary,
            self.local.name,
            self.owned,
            replace=self.replace,
            before_publish=before_publish,
        )
        self.local_publication = "published"

    async def finish(self, digest: str) -> Result:
        self.expected_digest = digest
        self.device_evidence = "finish-entered"
        self.stage = "finishing"
        self.check()
        result = await self.call(
            "file.finish", {"job": self.device_job_id, "sha256": digest}
        )
        while result.result == "still running":
            await asyncio.sleep(0.2)
            try:
                result = await self.call("file.get", {"job": self.device_job_id})
            except Fault as error:
                result = uncertain(self.direction, error)
            if (
                result.result in ("not sent", "UNKNOWN")
                and result.data.get("job") != self.device_job_id
            ):
                reason = "Device finish was not confirmed by subsequent query"
                if result.reason:
                    reason += "; " + result.reason
                result = result.model_copy(
                    update={"result": "UNKNOWN", "reason": reason}
                )
        if result.result == "done":
            self.device_evidence = "finish-reported"
        return result

    def publication_attempted(self) -> bool:
        if self.direction == "push":
            return self.device_evidence in ("finish-reported", "finish-verified")
        return self.local_publication != "unchanged"

    def publication_confirmed(self) -> bool:
        if self.direction == "push":
            return self.device_evidence == "finish-verified"
        return self.local_publication == "published"

    def result_after_error(self, error: Exception) -> Result:
        classified = isinstance(
            error, (Fault, OSError, ValueError, KeyError, TypeError)
        )
        if classified:
            known = isinstance(error, (FileExistsError, PermissionError)) or (
                self.direction == "pull"
                and isinstance(error, Fault)
                and error.problem.code == "publishUnsupported"
            )
            publication_unknown = self.publication_attempted() and not known
        else:
            if self.publication_confirmed():
                return self.success()
            publication_unknown = self.publication_attempted() or (
                self.direction == "push" and self.device_evidence == "finish-entered"
            )
        return Result(
            command=self.direction,
            result="UNKNOWN" if publication_unknown else "failed",
            reason=str(error),
            error=error.problem if isinstance(error, Fault) else None,
            data={"job": self.id},
        )

    def finish_result(self, result: Result) -> Result:
        if result.result == "UNKNOWN":
            unpublished_pull = self.direction == "pull"
            return Result(
                command=self.direction,
                result="failed" if unpublished_pull else "UNKNOWN",
                reason=(
                    "; ".join(filter(None, (result.reason, "target not published")))
                    if unpublished_pull
                    else result.reason
                ),
                error=result.error,
                data={"job": self.id},
            )
        if result.result != "done":
            return Result(
                command=self.direction,
                result="failed",
                reason=result.reason or result.result,
                error=result.error,
                data={"job": self.id},
            )
        return self.success()

    def success(self, reason: str | None = None) -> Result:
        return completed(
            self.direction,
            {"job": self.id, "size": self.size, "sha256": self.expected_digest},
            reason=reason,
        )

    def cleanup_failed(self, message: str) -> None:
        self.cleanup_error = "; ".join(filter(None, (self.cleanup_error, message)))

    def record_confirmed_push(self, state: Result) -> None:
        if (
            self.direction == "push"
            and self.expected_digest is not None
            and state.result == "done"
            and (
                state.data.get("job"),
                state.data.get("size"),
                state.data.get("sha256"),
            )
            == (self.device_job_id, self.size, self.expected_digest)
        ):
            self.device_evidence = "finish-verified"
            self.fact = self.success("Original push confirmed during cleanup")

    async def cleanup_device(self) -> None:
        if self.device_job_id and not self.device_ended:
            until = time.monotonic() + CLEANUP_SECONDS
            try:
                # Cancellation cannot recall a publication that has already started.
                state = await self.call(
                    "file.cancel", {"job": self.device_job_id}, cleanup_until=until
                )
                self.record_confirmed_push(state)
                while state.data.get("resourcesActive") is True:
                    await asyncio.sleep(min(0.2, max(0, until - time.monotonic())))
                    state = await self.call(
                        "file.get", {"job": self.device_job_id}, cleanup_until=until
                    )
                    self.record_confirmed_push(state)
                if state.data.get("resourcesActive") is not False:
                    raise Fault("deviceCleanup", state.reason or state.result)
            except Exception as error:
                self.cleanup_failed("Device cleanup unconfirmed: " + str(error))

    async def cleanup(self) -> None:
        await self.cleanup_device()
        if self.stream is not None:
            try:
                await self.io(self.stream.close)
            except Exception as error:
                self.cleanup_failed("Local stream close failed: " + str(error))
            else:
                self.stream = None
        if self.parent is not None:
            try:
                if (
                    self.direction == "pull"
                    and self.local_publication != "published"
                    and self.owned is not None
                    and not await self.io(
                        self.parent.remove, self.temporary, self.owned
                    )
                ):
                    self.cleanup_failed("Temporary identity changed; left untouched")
            except Exception as error:
                self.cleanup_failed("Temporary cleanup failed: " + str(error))
            try:
                await self.io(self.parent.close)
            except Exception as error:
                self.cleanup_failed("Local directory close failed: " + str(error))
            else:
                self.parent = None

    async def run(self) -> None:
        not_sent = False
        internal: Problem | None = None
        try:
            if self.direction == "push":
                self.fact = await self.push()
            else:
                self.fact = await self.pull()
        except (Fault, OSError, ValueError, KeyError, TypeError) as error:
            creation_cleanup = (
                isinstance(error, Fault) and error.problem.code == "temporaryCleanup"
            )
            if creation_cleanup:
                self.cleanup_failed(str(error))
            not_sent = (
                self.device_evidence == "absent"
                and not self.offset
                and not creation_cleanup
            )
            self.fact = self.result_after_error(error)
        except Exception as error:
            internal = Problem(
                code="internal",
                message=str(error),
                stage=f"host.file.{self.stage}",
            )
            self.fact = self.result_after_error(error)
        finally:
            await self.cleanup()
            if internal is not None and self.fact is not None:
                if self.fact.error is None:
                    self.fact.error = internal
                self.fact.reason = "; ".join(
                    dict.fromkeys(filter(None, (self.fact.reason, internal.message)))
                )
                if self.fact.result in ("done", "UNKNOWN"):
                    self.fact.exit = UNCERTAIN_EXIT
            if self.fact is not None and not_sent and not self.cleanup_error:
                self.fact.result = "not sent"
                self.fact.reason = (
                    (self.fact.reason or "")
                    + "; no data transferred; no device object created; "
                    "target unchanged; host temporary file removed or absent"
                )
            if self.stream is None and self.parent is None:
                self.manager.release(self.target_key, self.id)
                self.ended = True


class FileJobs:
    def __init__(self) -> None:
        self.items: dict[str, FileJob] = {}
        self.targets: dict[DestinationKey, str] = {}

    def occupy(self, key: DestinationKey, identifier: str) -> None:
        if key in self.targets:
            raise Fault("busy", "Another transfer owns this destination")
        self.targets[key] = identifier

    def release(self, key: DestinationKey | None, identifier: str) -> None:
        if key is not None and self.targets.get(key) == identifier:
            del self.targets[key]

    def start(
        self,
        device: str,
        pairing: str,
        session: str,
        args: dict[str, Any],
        finished: Callable[[], None],
        *,
        remote: Remote,
    ) -> Result:
        if (
            args.get("direction") not in ("push", "pull")
            or not 1 <= args.get("runMs", MAX_RUN_MS) <= MAX_RUN_MS
        ):
            raise Fault("file", "Invalid direction or run budget")
        job = FileJob(self, device, pairing, session, args, remote)
        self.items[job.id] = job
        job.task = asyncio.create_task(job.run())

        def ended(task: asyncio.Task[None]) -> None:
            if not task.cancelled():
                task.exception()
            self.trim()
            finished()

        job.task.add_done_callback(ended)
        return job.view()

    def trim(self) -> None:
        completed_jobs = [key for key, value in self.items.items() if value.ended]
        for key in completed_jobs[:-RECENT_JOBS]:
            del self.items[key]

    def discard_device(self, device: str) -> int:
        identifiers = [key for key, job in self.items.items() if job.device == device]
        for identifier in identifiers:
            del self.items[identifier]
        return len(identifiers)

    def find(self, identifier: str, device: str, pairing: str, session: str) -> FileJob:
        job = self.items.get(identifier)
        if job is None:
            raise Fault("jobUnavailable", "File job is not retained in this daemon")
        if (job.device, job.pairing, job.session) != (device, pairing, session):
            raise Fault("jobOwner", "Job belongs to another device or pairing/session")
        return job

    async def close(self) -> None:
        for job in self.items.values():
            job.cancelled = True
        await asyncio.gather(
            *(job.task for job in self.items.values() if job.task),
            return_exceptions=True,
        )
