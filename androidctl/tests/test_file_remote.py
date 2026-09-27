import asyncio
import base64
import time

import pytest

from androidctl.protocol import Fault, Result, completed
from connection_test_support import connections
from pairing_test_support import Gateway


async def test_expired_device_request_returns_not_sent_without_dispatch(tmp_path):
    received = []

    async def dispatch(method, args):
        received.append(method)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, _):
        device, _ = await coordinator.device(None)
        result = await device.request(
            "file.begin",
            {"path": "/unused"},
            session="original",
            deadline=time.monotonic() - 1,
        )
        assert result.result == "not sent"
        assert result.error.code == "timeout"
        assert result.reason == "Request budget exhausted before device dispatch"
        assert not received
        result = await device.request(
            "snapshot", {}, session="original", deadline=time.monotonic() + 3
        )
        assert result.result == "done"
        assert received == ["snapshot"]


@pytest.mark.parametrize(
    ("phase", "reply", "expected"),
    [
        ("file.begin", "rejected", "not sent"),
        ("file.begin", "lost", "failed"),
        ("file.finish", "rejected", "failed"),
        ("file.finish", "lost", "UNKNOWN"),
        ("file.finish", "confirmed", "done"),
    ],
)
async def test_file_remote_rejection_and_lost_reply_keep_transfer_facts(
    tmp_path, monkeypatch, phase, reply, expected
):
    source, target = tmp_path / "source", tmp_path / "device-target"
    source.write_bytes(b"transfer bytes")
    target.write_bytes(b"old target")
    received, payload = [], bytearray()
    finish = None

    async def dispatch(method, args):
        nonlocal finish
        received.append(method)
        if method == phase and reply == "rejected":
            raise Fault("notReady", "Transfer rejected", "device.file")
        if method == "file.begin":
            return Result(
                command=method,
                result="still running",
                data={"job": "device-file", "ready": True, "size": args["size"]},
            ).wire()
        if method == "file.chunk":
            assert args["offset"] == len(payload)
            payload.extend(base64.b64decode(args["bytes"], validate=True))
            return completed(method, {"offset": len(payload)}).wire()
        if method == "file.finish":
            target.write_bytes(payload)
            finish = completed(
                method,
                {
                    "job": "device-file",
                    "size": len(payload),
                    "sha256": args["sha256"],
                    "resourcesActive": False,
                },
            ).wire()
            return finish
        assert method == "file.cancel"
        return (
            finish
            if reply == "confirmed"
            else completed(method, {"resourcesActive": False}).wire()
        )

    original_reply = Gateway.reply

    async def device_reply(gateway, connection, value):
        if (
            reply in {"lost", "confirmed"}
            and value.get("result", {}).get("command") == phase
        ):
            monkeypatch.setattr(Gateway, "reply", original_reply)
            await connection.close()
            return
        await original_reply(gateway, connection, value)

    monkeypatch.setattr(Gateway, "reply", device_reply)
    async with connections(tmp_path, dispatch) as (coordinator, _, _):
        started = await coordinator.dispatch(
            "call",
            {
                "command": "file.start",
                "args": {
                    "direction": "push",
                    "local": str(source),
                    "remote": "/target",
                },
            },
            deadline=time.monotonic() + 3,
        )
        job = coordinator.files.items[started["data"]["job"]]
        await asyncio.wait_for(asyncio.shield(job.task), 5)
        result = job.view()
        assert result.result == expected
        assert (
            result.exit_code()
            == {"done": 0, "not sent": 2, "UNKNOWN": 3, "failed": 1}[expected]
        )
        if reply == "rejected":
            assert result.error.code == "notReady"
            assert result.error.stage == "device.file"
        elif reply == "lost":
            assert result.error.code == "replyUnavailable"
            assert result.error.stage == "device.websocket"
        if phase == "file.begin" and reply == "lost":
            assert "transfer may exist on device" in result.reason
            assert "deviceJob" not in result.data
        if reply == "confirmed":
            assert result.reason == "Original push confirmed during cleanup"
        assert result.data["resourcesActive"] is False
        assert received.count("file.begin") == 1
        assert received.count("file.finish") == (phase == "file.finish")
        assert received.count("file.cancel") == (phase == "file.finish")
        assert target.read_bytes() == (
            source.read_bytes()
            if phase == "file.finish" and reply != "rejected"
            else b"old target"
        )
