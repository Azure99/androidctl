import asyncio
import json
import threading
import time

import pytest

from androidctl.coordinator import Coordinator
from androidctl.private_files import private_directory
from androidctl.protocol import Fault, completed
from androidctl.state import HostState
from androidctl.transport import Peer, send
from connection_test_support import connections, local_server


async def test_daemon_status_counts_other_running_and_waiting_requests(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(method, args):
        entered.set()
        await release.wait()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, _, open_local):
        peers = [await open_local(deadline=time.monotonic() + 3) for _ in range(3)]
        inspector, first, second = peers
        tasks = []
        try:
            assert (
                await inspector.request("daemon.status", deadline=time.monotonic() + 10)
            )["data"]["active"] == 0
            for peer in (first, second):
                tasks.append(
                    asyncio.create_task(
                        peer.request(
                            "call",
                            {"command": "click"},
                            deadline=time.monotonic() + 10,
                        )
                    )
                )
            await asyncio.wait_for(entered.wait(), 1)

            async def observe_both():
                while True:
                    result = await inspector.request(
                        "daemon.status", deadline=time.monotonic() + 10
                    )
                    if result["data"]["active"] == 2:
                        return result
                    await asyncio.sleep(0.01)

            result = await asyncio.wait_for(observe_both(), 1)
            assert result["data"]["connected"] == 1
            release.set()
            assert all(
                result["result"] == "done" for result in await asyncio.gather(*tasks)
            )
            assert (
                await inspector.request("daemon.status", deadline=time.monotonic() + 10)
            )["data"]["active"] == 0
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(peer.close() for peer in peers))


def test_real_io_retains_ownership_after_waiter_cancellation(tmp_path):
    started, finish = threading.Event(), threading.Event()

    def write():
        started.set()
        assert finish.wait(5)
        (tmp_path / "finished").write_text("finished")

    async def run():
        private_directory(tmp_path / "state")
        coordinator = Coordinator(HostState(tmp_path / "state"), instance="fixture")
        waiter = asyncio.create_task(coordinator.io(write))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            assert coordinator.io_task
            with pytest.raises(Fault, match="finish"):
                await coordinator.dispatch(
                    "daemon.stop", {}, deadline=time.monotonic() + 15
                )
        finally:
            finish.set()
            await coordinator.close()
        assert (tmp_path / "finished").read_text() == "finished"
        assert not coordinator.io_task

    asyncio.run(run())


async def test_stop_preserves_other_connections_and_half_tls(tmp_path):
    root = tmp_path / "state"
    private_directory(root)
    coordinator = Coordinator(HostState(root), instance="fixture")
    peers, raw_writer = [], None
    stopped = asyncio.Event()

    async def request_without_ack(peer, method):
        await send(
            peer.connection,
            peer.prepare_message(peer.request_body(method), time.monotonic() + 3),
        )
        return json.loads(await asyncio.wait_for(peer.connection.recv(), 3))

    async def wait_until(condition):
        async def poll():
            while not condition():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), 3)

    async with local_server(
        root,
        coordinator.dispatch,
        credential="credential",
        stopped=stopped,
    ) as (saved, server):
        try:
            for _ in range(3):
                peers.append(await Peer.open(saved, source="local"))
            a, b, c = peers
            first = await request_without_ack(a, "devices")
            assert first["result"]["result"] == "done"
            rejected = await request_without_ack(b, "daemon.stop")
            assert rejected["error"]["code"] == "busy"
            assert "blockId" not in first
            with pytest.raises(Fault, match="active"):
                await c.request("daemon.stop", deadline=time.monotonic() + 10)
            assert "blockId" not in rejected
            _, raw_writer = await asyncio.open_connection("127.0.0.1", server.port)
            await wait_until(lambda: len(server.tasks) > 3)
            with pytest.raises(Fault, match="active"):
                await c.request("daemon.stop", deadline=time.monotonic() + 10)
            raw_writer.close()
            await raw_writer.wait_closed()
            raw_writer = None
            await wait_until(lambda: len(server.tasks) == 3)
            with pytest.raises(Fault, match="connections"):
                await c.request("daemon.stop", deadline=time.monotonic() + 10)
            await a.close()
            await b.close()
            await wait_until(lambda: len(server.tasks) == 1)
            assert (await c.request("daemon.stop", deadline=time.monotonic() + 10))[
                "result"
            ] == "done"
            await asyncio.wait_for(stopped.wait(), 3)
            assert not server.server.accepting
        finally:
            if raw_writer is not None:
                raw_writer.close()
                await raw_writer.wait_closed()
            await asyncio.gather(*(peer.close() for peer in peers))
            await coordinator.close()
