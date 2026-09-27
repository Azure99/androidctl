import asyncio
import base64
import hashlib
import time

import pytest

from androidctl.file_jobs import FileJob
from androidctl.protocol import CHUNK_BYTES, Fault, Result, completed
from connection_test_support import connections


def test_bound_device_survives_chunk_gap_and_holds_maintenance_gate(
    tmp_path, monkeypatch
):
    body = b"bound transfer\0" * (CHUNK_BYTES // 5)
    source = tmp_path / "source"
    source.write_bytes(body)

    async def run():
        gap, release = asyncio.Event(), asyncio.Event()
        received = bytearray()
        methods = []

        async def dispatch(method, args):
            methods.append(method)
            if method == "file.begin":
                return Result(
                    command="push",
                    result="still running",
                    data={"job": "bound", "ready": True, "size": len(body)},
                ).wire()
            if method == "file.chunk":
                assert args["offset"] == len(received)
                received.extend(base64.b64decode(args["bytes"]))
                return completed(method, {"offset": len(received)}).wire()
            if method == "file.finish":
                return completed(
                    "push",
                    {
                        "job": "bound",
                        "size": len(received),
                        "sha256": hashlib.sha256(received).hexdigest(),
                        "resourcesActive": False,
                    },
                ).wire()
            raise AssertionError(method)

        original_io = FileJob.io

        async def io(job, operation, *args, **kwargs):
            if operation.__name__ == "read" and job.offset == CHUNK_BYTES:
                gap.set()
                await release.wait()
            return await original_io(job, operation, *args, **kwargs)

        monkeypatch.setattr(FileJob, "io", io)
        async with connections(tmp_path, dispatch) as (coordinator, material, _):
            first = await coordinator.dispatch(
                "call",
                {
                    "device": "phone",
                    "session": "original",
                    "command": "file.start",
                    "args": {
                        "timeoutMs": 15000,
                        "direction": "push",
                        "local": str(source),
                        "remote": "/bound",
                        "runMs": 5000,
                    },
                },
                deadline=time.monotonic() + 15,
            )
            job = coordinator.files.items[first["data"]["job"]]
            try:
                await asyncio.wait_for(gap.wait(), 2)
                device = coordinator.devices[material["deviceId"]]
                if device._peers["mutation"].ack_task is not None:
                    await asyncio.wait_for(
                        asyncio.shield(device._peers["mutation"].ack_task), 3
                    )
                assert device.in_use()
                assert not device.busy()
                for method, args in [
                    (
                        "pair",
                        {
                            "name": "phone",
                            "endpoint": material["endpoint"],
                            "code": "000001",
                            "timeoutMs": 20000,
                        },
                    ),
                    (
                        "device.address",
                        {"device": "phone", "endpoint": material["endpoint"]},
                    ),
                    (
                        "device.trust",
                        {"device": "phone", "pem": material["certificate"]},
                    ),
                    ("device.forget", {"device": "phone"}),
                ]:
                    with pytest.raises(Fault) as rejected:
                        await coordinator.dispatch(
                            method, args, deadline=time.monotonic() + 15
                        )
                    assert rejected.value.problem.code == "busy", method
                release.set()
                await asyncio.wait_for(job.task, 2)
                assert job.view().result == "done"
                assert bytes(received) == body
                if device._peers["mutation"].ack_task is not None:
                    await asyncio.wait_for(
                        asyncio.shield(device._peers["mutation"].ack_task), 3
                    )
                assert not device.in_use()
                assert not device.busy()
                assert methods.count("file.begin") == methods.count("file.finish") == 1
                assert methods.count("file.chunk") >= 3
                assert (
                    await coordinator.dispatch(
                        "device.address",
                        {"device": "phone", "endpoint": material["endpoint"]},
                        deadline=time.monotonic() + 15,
                    )
                )["result"] == "done"
            finally:
                release.set()
                await job.task

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
async def test_file_chunk_gap_waits_for_other_owner_then_rechecks_cancellation(
    tmp_path, monkeypatch, cancel
):
    body = b"nonempty file\x00\xff" * 6000
    source = tmp_path / "source"
    source.write_bytes(body)
    payload, received = bytearray(), []
    first, advance, occupied, release, queued = (asyncio.Event() for _ in range(5))

    async def dispatch(method, args):
        received.append(method)
        if method == "file.begin":
            return Result(
                command="push",
                result="still running",
                data={"job": "device-file", "ready": True, "size": len(body)},
            ).wire()
        if method == "hold":
            occupied.set()
            await release.wait()
            return completed(method).wire()
        if method == "file.chunk":
            assert args["offset"] == len(payload)
            payload.extend(base64.b64decode(args["bytes"], validate=True))
            return completed(method, {"offset": len(payload)}).wire()
        if method == "file.finish":
            return completed(
                method,
                {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "resourcesActive": False,
                },
            ).wire()
        if method == "file.cancel":
            return completed(method, {"resourcesActive": False}).wire()
        raise AssertionError(method)

    async with connections(tmp_path, dispatch) as (coordinator, material, _):
        job = FileJob(
            coordinator.files,
            material["deviceId"],
            "pair",
            "A",
            {
                "direction": "push",
                "local": str(source),
                "remote": "/file",
                "runMs": 5000,
            },
            (await coordinator.device(None))[0].request,
        )
        coordinator.files.items[job.id] = job
        original = job.chunk

        async def between_chunks(args):
            value = await original(args)
            if args["offset"] == 0:
                peer = next(iter(coordinator.devices.values()))._peers["mutation"]
                await peer.ack_task
                first.set()
                await advance.wait()
            return value

        monkeypatch.setattr(job, "chunk", between_chunks)
        running = asyncio.create_task(job.run())
        competitor = None
        try:
            await asyncio.wait_for(first.wait(), 2)
            competitor = asyncio.create_task(
                coordinator.dispatch(
                    "call",
                    {
                        "command": "hold",
                        "session": "B",
                        "args": {"timeoutMs": 3000},
                    },
                    deadline=time.monotonic() + 3,
                )
            )
            await asyncio.wait_for(occupied.wait(), 1)
            peer = next(iter(coordinator.devices.values()))._peers["mutation"]
            acquire = peer.lock.acquire

            async def waiting():
                queued.set()
                return await acquire()

            monkeypatch.setattr(peer.lock, "acquire", waiting)
            advance.set()
            await asyncio.wait_for(queued.wait(), 1)
            if cancel:
                await coordinator.dispatch(
                    "call",
                    {
                        "command": "job.cancel",
                        "session": "A",
                        "args": {"timeoutMs": 15000, "job": job.id},
                    },
                    deadline=time.monotonic() + 15,
                )
            release.set()
            await competitor
            await asyncio.wait_for(asyncio.shield(running), 3)
            assert bytes(payload) == (body[:32768] if cancel else body)
            assert received.count("file.finish") == (0 if cancel else 1)
            assert job.view().result == ("failed" if cancel else "done")
            assert job.ended
            assert job.stream is None
            assert job.parent is None
        finally:
            advance.set()
            release.set()
            await asyncio.gather(running, *([competitor] if competitor else []))
