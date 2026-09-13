import asyncio
import json
import time

import pytest
from websockets.asyncio.client import connect

from androidctl.identity import trust_context
from androidctl.protocol import (
    VERSION,
    Fault,
    Result,
    TransportFailure,
    completed,
)
from androidctl.transport import Peer
from connection_test_support import connections, local_server
from pairing_test_support import Gateway


async def test_other_session_is_rejected_during_ack_handoff(tmp_path, monkeypatch):
    release_ack, entered, leave = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received, competitors = [], []

    async def dispatch(method, args):
        received.append(method)
        if method == "next":
            entered.set()
            await leave.wait()
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, _):
        peer = await Peer.open(material)
        original = peer.acknowledge
        first_ack = True

        async def acknowledge(value):
            nonlocal first_ack
            if first_ack:
                first_ack = False
                await release_ack.wait()
                await original(value)
                # Acquire between ACK release and the waiter's wakeup.
                asyncio.get_running_loop().call_soon(
                    lambda: competitors.append(
                        asyncio.create_task(
                            peer.request(
                                "other",
                                session="other",
                                deadline=time.monotonic() + (1),
                            )
                        )
                    )
                )
            else:
                await original(value)

        monkeypatch.setattr(peer, "acknowledge", acknowledge)
        waiting = None
        try:
            assert (
                await peer.request(
                    "first", session="ours", deadline=time.monotonic() + 10
                )
            )["result"] == "done"
            queued = asyncio.Event()
            acquire = peer.lock.acquire

            async def observed_acquire():
                queued.set()
                return await acquire()

            monkeypatch.setattr(peer.lock, "acquire", observed_acquire)
            waiting = asyncio.create_task(
                peer.request("next", session="ours", deadline=time.monotonic() + (5))
            )
            await asyncio.wait_for(queued.wait(), 3)
            release_ack.set()
            await asyncio.wait_for(entered.wait(), 3)
            assert len(competitors) == 1
            with pytest.raises(Fault) as error:
                await asyncio.wait_for(competitors[0], 3)
            assert error.value.problem.code == "connectionBusy"
            assert received == ["first", "next"]
            leave.set()
            assert (await waiting)["result"] == "done"
        finally:
            release_ack.set()
            leave.set()
            await asyncio.gather(
                *competitors,
                *([waiting] if waiting else []),
                return_exceptions=True,
            )
            await peer.close()
        assert not peer.busy
        assert peer.owner is None


async def test_local_replies_allow_pipelined_requests_without_ack(tmp_path):
    calls = []

    async def dispatch(method, args, deadline):
        calls.append(method)
        return completed(method).wire()

    async with (
        local_server(tmp_path / "tls", dispatch) as (material, _),
        connect(
            material["endpoint"],
            ssl=trust_context(material["certificate"]),
            server_hostname=material["name"],
            additional_headers={
                "X-AndroidCtl-Version": VERSION,
                "Authorization": "Bearer " + material["credential"],
            },
            proxy=None,
        ) as socket,
    ):
        welcome = json.loads(await asyncio.wait_for(socket.recv(), 3))
        assert welcome["event"] == "connected"
        assert "blockId" not in welcome
        for name in ("first", "second"):
            await socket.send(
                json.dumps({"id": name, "method": name, "args": {}, "budgetMs": 3000})
            )
        for name in ("first", "second"):
            reply = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert reply["id"] == name
            assert reply["result"]["result"] == "done"
            assert "blockId" not in reply
        assert calls == ["first", "second"]


async def test_ack_owns_queue_across_mixed_sessions_and_cancelled_waiter(
    tmp_path, monkeypatch
):
    received = []

    async def dispatch(method, args):
        received.append(method)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (_, material, _):
        peer = await Peer.open(material)
        release = asyncio.Event()
        waiting = asyncio.Queue()
        acknowledge, acquire = peer.acknowledge, peer.lock.acquire

        async def held_ack(value):
            await release.wait()
            await acknowledge(value)

        async def observed_acquire():
            waiting.put_nowait(True)
            return await acquire()

        monkeypatch.setattr(peer, "acknowledge", held_ack)
        monkeypatch.setattr(peer.lock, "acquire", observed_acquire)
        tasks = []
        try:
            await peer.request("first", session="A", deadline=time.monotonic() + 10)
            await waiting.get()
            tasks.append(
                asyncio.create_task(
                    peer.request(
                        "B",
                        session="B",
                        wait_for_owner=True,
                        deadline=time.monotonic() + 10,
                    )
                )
            )
            await asyncio.wait_for(waiting.get(), 1)
            tasks.append(
                asyncio.create_task(
                    peer.request("A-next", session="A", deadline=time.monotonic() + 10)
                )
            )
            await asyncio.wait_for(waiting.get(), 1)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert peer.owner == "A"
            with pytest.raises(Fault, match="occupied"):
                await peer.request("C", session="C", deadline=time.monotonic() + 10)
            assert received == ["first"]
            release.set()
            assert (await tasks[1])["result"] == "done"
            await peer.ack_task
            assert received == ["first", "A-next"]
            assert not peer.busy
            assert peer.owner is None
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await peer.close()


@pytest.mark.parametrize("fault", ["ack", "wrong-id", "invalid-result", "welcome"])
async def test_real_tls_preserves_only_validated_facts_before_ack(
    tmp_path, fault, monkeypatch
):
    root = tmp_path / "private"
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return {
            "command": "click",
            "result": "bogus" if fault == "invalid-result" else "done",
            "data": {},
        }

    original_ack = Peer.acknowledge

    async def acknowledge(self, value):
        if (
            fault == "ack"
            and "result" in value
            or fault == "welcome"
            and value.get("event") == "connected"
        ):
            self.connection.transport.abort()
            await self.connection.wait_closed()
            raise OSError("injected ACK disconnect")
        await original_ack(self, value)

    monkeypatch.setattr(Peer, "acknowledge", acknowledge)
    peer = None
    gateway = Gateway(root, dispatch=dispatch)
    gateway.credentials["test"] = "pair"
    async with gateway.listening():
        saved = {**gateway.identity, "credential": "test"}
        server = gateway
        original_reply = server.reply

        async def reply(connection, value):
            if fault == "wrong-id" and "id" in value:
                value = {**value, "id": "different-request"}
            await original_reply(connection, value)

        monkeypatch.setattr(server, "reply", reply)
        try:
            if fault == "welcome":
                with pytest.raises(Fault):
                    await Peer.open(saved)
                assert not calls
            else:
                peer = await Peer.open(saved)
                if fault == "ack":
                    result = (
                        await peer.request(
                            "click",
                            validate=Result.model_validate,
                            deadline=time.monotonic() + 10,
                        )
                    ).wire()
                    assert result["result"] == "done"
                    await peer.ack_task
                    assert not peer.opened
                else:
                    with pytest.raises(TransportFailure):
                        (
                            await peer.request(
                                "click",
                                validate=Result.model_validate,
                                deadline=time.monotonic() + 10,
                            )
                        ).wire()
                assert calls == ["click"]
        finally:
            if peer is not None:
                await peer.close()
