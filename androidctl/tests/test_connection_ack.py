import asyncio
import json
import time

import pytest
from websockets.asyncio.client import connect

from androidctl.identity import trust_context
from androidctl.protocol import VERSION, Fault, Result, TransportFailure, completed
from androidctl.transport import Peer
from connection_test_support import local_server
from pairing_test_support import Gateway


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
