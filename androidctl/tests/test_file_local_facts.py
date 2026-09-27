import asyncio
import threading
import time

import pytest
from websockets.asyncio.server import serve

from androidctl import local_call
from androidctl.cli_options import CliOptions
from androidctl.coordinator import Coordinator
from androidctl.file_jobs import FileJob
from androidctl.job_commands import JobClient
from androidctl.private_files import private_directory, write_json
from androidctl.protocol import Fault
from androidctl.state import HostState
from androidctl.transport import Peer
from androidctl.user_files import Directory
from connection_test_support import local_server
from file_test_support import Device
from pairing_test_support import Gateway, new_device_identity, paired, save_active


def test_forget_keeps_unconfirmed_resources_and_stop_remains_available(
    tmp_path, monkeypatch
):
    close = Directory.close

    def unconfirmed(directory):
        close(directory)
        raise OSError("directory close unconfirmed")

    monkeypatch.setattr(Directory, "close", unconfirmed)

    async def run():
        private_directory(tmp_path / "state")
        state = HostState(tmp_path / "state")
        saved = paired(state, new_device_identity(), "phone")
        write_json(
            state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
        )
        coordinator = Coordinator(state, instance="test")
        value = coordinator.files.start(
            saved["deviceId"],
            saved["pairingId"],
            "ours",
            {"direction": "pull", "local": str(tmp_path / "final"), "remote": "/file"},
            lambda: None,
            remote=Device().call,
        )
        job = coordinator.files.items[value.data["job"]]
        try:
            await job.task
            request = {
                "command": "job.get",
                "device": saved["deviceId"],
                "session": "ours",
                "args": {"job": job.id, "timeoutMs": 15000},
            }
            before = await coordinator.dispatch(
                "call", request, deadline=time.monotonic() + 15
            )
            assert before["data"]["resourcesActive"]
            with pytest.raises(Fault) as caught:
                await coordinator.forget("phone")
            assert caught.value.problem.code == "busy"
            assert state.resolve_selection(None)[0].device_id == saved["deviceId"]
            assert (
                await coordinator.dispatch(
                    "call", request, deadline=time.monotonic() + 15
                )
                == before
            )
            assert coordinator.files.targets[job.target_key] == job.id
            assert (
                await coordinator.dispatch(
                    "daemon.stop", {}, deadline=time.monotonic() + 15
                )
            )["result"] == "done"
        finally:
            await coordinator.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "unavailable", ["revoked", "offline", "welcome-loss", "cleanup-rejected"]
)
def test_local_queries_and_stop_do_not_connect_but_cleanup_uses_a_fresh_budget(
    tmp_path, monkeypatch, unavailable
):
    entered, leave = threading.Event(), threading.Event()
    write = FileJob.write

    def blocked(job, data):
        entered.set()
        assert leave.wait(10)
        write(job, data)

    monkeypatch.setattr(FileJob, "write", blocked)

    async def run():
        for name in ("state", "ipc", "files"):
            private_directory(tmp_path / name)
        business_calls, handshakes = [], []
        device = Device()
        target = tmp_path / "files/final"
        target.write_bytes(b"old target")

        async def device_rpc(method, args):
            business_calls.append((method, args))
            if method == "file.cancel" and unavailable == "cleanup-rejected":
                raise Fault("revoked", "Cleanup rejected", "device.file")
            return (
                await device.call(
                    method,
                    args,
                    session="original-session",
                    deadline=time.monotonic() + 10,
                )
            ).wire()

        gateway = Gateway(tmp_path / "device-tls", dispatch=device_rpc)
        gateway.credentials["accepted"] = "original-pair"

        async def observed(connection, request):
            handshakes.append(request)
            return await gateway.opening(connection, request)

        async with serve(
            gateway.connected, "127.0.0.1", 0, ssl=gateway.tls, process_request=observed
        ) as remote:
            identity = {
                **gateway.identity,
                "endpoint": f"wss://127.0.0.1:{remote.sockets[0].getsockname()[1]}/androidctl",
            }
            state = HostState(tmp_path / "state")
            saved = paired(
                state,
                identity,
                "phone",
            )
            saved["pairingId"] = "original-pair"
            saved["credential"] = "accepted"
            save_active(state, saved)
            coordinator = Coordinator(state, instance="fixture")
            async with local_server(
                tmp_path / "ipc",
                coordinator.dispatch,
                credential="local-user",
                stopped=asyncio.Event(),
            ) as (ipc_material, _):

                async def connect(*, deadline, start=True):
                    return await Peer.open(
                        ipc_material, deadline=deadline, source="local"
                    )

                monkeypatch.setattr(local_call, "connect_local", connect)
                client = JobClient(CliOptions("phone", "original-session", False))
                wrong = JobClient(CliOptions("phone", "other-session", False))
                job = None
                try:
                    started = await client.call(
                        "file.start",
                        {
                            "direction": "pull",
                            "local": str(target),
                            "remote": "/file",
                            "replace": True,
                            "runMs": 1000,
                        },
                    )
                    job = coordinator.files.items[started.data["job"]]
                    assert started.data["context"]["deviceId"] == saved["deviceId"]
                    assert await asyncio.to_thread(entered.wait, 3)
                    connected, _ = await coordinator.device("phone")
                    await connected.close()
                    before_calls, before_handshakes = (
                        len(business_calls),
                        len(handshakes),
                    )
                    assert before_calls > 0
                    if unavailable == "revoked":
                        gateway.credentials.clear()
                    elif unavailable == "offline":
                        remote.close()
                        await remote.wait_closed()
                    elif unavailable == "welcome-loss":
                        reply = gateway.reply

                        async def drop_welcome(connection, value):
                            if value.get("event") == "connected":
                                await connection.close()
                            else:
                                await reply(connection, value)

                        monkeypatch.setattr(gateway, "reply", drop_welcome)

                    attempts = []
                    peer_open = Peer.open

                    async def count_open(cls, material, **kwargs):
                        if material.get("deviceId") == saved["deviceId"]:
                            attempts.append(kwargs["deadline"] - time.monotonic())
                        return await peer_open(material, **kwargs)

                    monkeypatch.setattr(Peer, "open", classmethod(count_open))
                    # Expire the run budget while local I/O owns the target.
                    await asyncio.sleep(1.1)
                    with pytest.raises(Fault, match="belongs"):
                        await wrong.call("job.get", {"job": job.id})
                    result = await client.call("job.get", {"job": job.id})
                    assert result.result == "still running"
                    assert result.data["context"]["deviceId"] == saved["deviceId"]
                    result = await asyncio.wait_for(
                        client.call("job.cancel", {"job": job.id}), 0.5
                    )
                    assert result.data["resourcesActive"] is True
                    assert result.data["stopRequested"]
                    assert result.data["context"]["deviceId"] == saved["deviceId"]
                    result = await client.wait(result, 100)
                    assert result.data["resourcesActive"] is True
                    assert not attempts
                    assert len(handshakes) == before_handshakes
                    assert len(business_calls) == before_calls
                    assert job.stream is not None
                    assert not job.stream.closed
                    assert coordinator.files.targets
                    assert target.read_bytes() == b"old target"
                    leave.set()
                    await asyncio.wait_for(asyncio.shield(job.task), 3)
                    result = await client.call("job.get", {"job": job.id})
                    assert result.result == "failed"
                    assert result.error.code == "cancelled"
                    assert result.exit_code() == 3
                    assert not result.data["resourcesActive"]
                    assert result.data["stopRequested"] is True
                    assert "Device cleanup unconfirmed" in result.data["cleanupError"]
                    assert len(attempts) == 1
                    assert 4 < attempts[0] <= 5
                    assert len(handshakes) - before_handshakes == (
                        0 if unavailable == "offline" else 1
                    )
                    assert [method for method, _ in business_calls[before_calls:]] == (
                        ["file.cancel"] if unavailable == "cleanup-rejected" else []
                    )
                    if unavailable == "cleanup-rejected":
                        assert "Cleanup rejected" in result.data["cleanupError"]
                    assert target.read_bytes() == b"old target"
                    assert list((tmp_path / "files").iterdir()) == [target]
                    assert not coordinator.files.targets
                finally:
                    leave.set()
                    if job is not None:
                        await job.task
                    for value in (client, wrong):
                        await value.close()
                    await coordinator.close()
            assert not remote.connections

    asyncio.run(run())
