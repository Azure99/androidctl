import asyncio
import time

import pytest

from androidctl.protocol import Fault, TransportFailure, completed
from androidctl.transport import Peer, encode_message
from connection_test_support import local_server


@pytest.mark.parametrize("budget", [None, True, 0, -1, 1.5, "1000"])
async def test_local_envelope_rejects_invalid_budget_before_dispatch(tmp_path, budget):
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            request = {"id": "invalid", "method": "devices", "args": {}}
            if budget is not None:
                request["budgetMs"] = budget
            await peer.connection.send(encode_message(request))
            reply = await asyncio.wait_for(peer.receive(), 3)
            assert reply == {
                "id": "invalid",
                "error": {
                    "code": "invalidRequest",
                    "message": "Expected positive integer budgetMs",
                },
            }
            assert not received
            assert (await peer.request("devices", deadline=time.monotonic() + 3))[
                "result"
            ] == "done"
            assert received == ["devices"]
        finally:
            await peer.close()


async def test_local_slot_wait_spends_envelope_budget_and_keeps_parameters(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    async def dispatch(method, args, deadline):
        received.append((method, args, deadline - time.monotonic()))
        if method == "hold":
            entered.set()
            await release.wait()
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        held = asyncio.create_task(peer.request("hold", deadline=time.monotonic() + 3))
        timer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            timer = asyncio.get_running_loop().call_later(0.2, release.set)
            parameters = {"timeoutMs": 9000, "args": {"timeoutMs": 8000}}
            assert (
                await peer.request("pair", parameters, deadline=time.monotonic() + 0.8)
            )["result"] == "done"
            await held
            assert received[1][:2] == ("pair", parameters)
            assert 0 < received[1][2] < 0.55
            assert parameters == {"timeoutMs": 9000, "args": {"timeoutMs": 8000}}
        finally:
            if timer is not None:
                timer.cancel()
            release.set()
            await held
            await peer.close()


async def test_local_message_limit_rejects_before_dispatch_and_releases_slot(tmp_path):
    received = []

    async def dispatch(method, args, deadline):
        received.append(method)
        return completed(method).wire()

    async with local_server(tmp_path / "ipc", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            with pytest.raises(Fault, match="message limit") as caught:
                await peer.request(
                    "call",
                    {"command": "shell.start", "args": {"script": "\x01" * 50000}},
                    deadline=time.monotonic() + 3,
                )
            assert caught.value.problem.code == "messageLimit"
            assert not isinstance(caught.value, TransportFailure)
            assert not received
            assert (await peer.request("devices", deadline=time.monotonic() + 3))[
                "result"
            ] == "done"
            assert received == ["devices"]
        finally:
            await peer.close()
