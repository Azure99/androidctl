import asyncio
import json
import sys
import time

import pytest

from androidctl.coordinator import Coordinator
from androidctl.identity import DeviceIdentity
from androidctl.private_files import atomic_write, private_directory
from androidctl.protocol import Fault, TransportFailure, completed
from androidctl.state import HostState
from androidctl.transport import Peer
from pairing_test_support import (
    Gateway,
    active_material,
    new_device_identity,
    paired,
    save_active,
)


async def test_concurrent_bootstrap_reuses_candidate_and_closes_every_peer(
    tmp_path,
):
    gateway = Gateway(tmp_path / "tls")
    first_entered, release_first = asyncio.Event(), asyncio.Event()
    connected = gateway.connected
    calls = 0

    async def gated(connection):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await asyncio.wait_for(release_first.wait(), 3)
        await connected(connection)

    gateway.connected = gated
    gateway.lose_registration = True
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="test")
    async with gateway.listening():
        first = asyncio.create_task(
            coordinator.pair(gateway.args(name="first"), deadline=time.monotonic() + 20)
        )
        try:
            await asyncio.wait_for(first_entered.wait(), 3)
            with pytest.raises(TransportFailure):
                await coordinator.pair(
                    gateway.args(name="second"), deadline=time.monotonic() + 20
                )
            release_first.set()
            result = await asyncio.wait_for(first, 3)
            assert result.result == "done"
            assert result.data["device"] == "second"
            assert len(gateway.registers) == 1
            assert len(gateway.connections) == 3
            await asyncio.wait_for(
                asyncio.gather(*(c.wait_closed() for c in gateway.connections)), 1
            )
            assert not coordinator.changing
        finally:
            release_first.set()
            await asyncio.gather(first, return_exceptions=True)
            await coordinator.close()


async def test_activation_keeps_known_fact_when_old_connection_close_fails(
    tmp_path, monkeypatch
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        save_active(
            state,
            {
                **gateway.identity,
                "alias": "phone",
                "credential": "old",
                "pairingId": "old",
            },
        )
        coordinator = Coordinator(state, instance="test")
        original, _ = await coordinator.device(gateway.identity["deviceId"])

        async def failed_close(**kwargs):
            raise OSError("old connection cleanup failed")

        monkeypatch.setattr(original, "close", failed_close)
        try:
            result = await coordinator.pair(
                gateway.args(), deadline=time.monotonic() + 20
            )
            assert result.result == "done"
            assert result.data["registered"] is True
            assert result.data["confirmed"] is True
            assert result.data["activated"] is True
            assert result.exit_code() == 3
            assert result.data["cleanupError"] == "old connection cleanup failed"
            assert result.error is None
            assert (
                state.resolve_selection(None)[0]
                == coordinator.devices[gateway.identity["deviceId"]].record
            )
            assert state.record(gateway.identity["deviceId"]).candidate is None
            assert state.record(gateway.identity["deviceId"]).active.pairing_id != "old"
        finally:
            await coordinator.close()


def test_lost_register_recovers_in_new_process_state_without_code_replay(tmp_path):
    async def run():
        gateway = Gateway(tmp_path / "tls")
        private_directory(tmp_path / "state")
        state = HostState(tmp_path / "state")
        async with gateway.listening():
            coordinator = Coordinator(state, instance="first")
            gateway.lose_registration = True
            with pytest.raises(TransportFailure):
                await coordinator.pair(gateway.args(), deadline=time.monotonic() + 20)
            record = state.record(gateway.identity["deviceId"])
            assert record.active is None
            assert (
                record.candidate.credential
                == gateway.registers[0]["args"]["credential"]
            )
            await coordinator.close()
            script = """
import asyncio,json,sys,time
from pathlib import Path
from androidctl.coordinator import Coordinator
from androidctl.state import HostState
async def run():
    owner=Coordinator(HostState(Path(sys.argv[1])),instance='child')
    try:
        result=await owner.pair(json.loads(sys.argv[2]), time.monotonic() + 20)
        print(json.dumps(result.wire()))
    finally:
        await owner.close()
asyncio.run(run())
"""
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                str(state.root),
                json.dumps(gateway.args(name="changed-alias", code="999999")),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            output, error = await asyncio.wait_for(child.communicate(), 8)
            assert child.returncode == 0, error
            result = json.loads(output)
            assert result["result"] == "done"
            assert result["data"]["activated"]
            assert state.resolve_selection(None)[0].active.alias == "phone"
            assert state.record(gateway.identity["deviceId"]).candidate is None
            assert len(gateway.registers) == 1
            assert gateway.headers == [
                ("000042", ""),
                (None, "Bearer " + record.candidate.credential),
            ]

    asyncio.run(run())


async def test_failed_new_pairing_keeps_old_active_object_and_confirmation_candidate(
    tmp_path,
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        old = {
            **gateway.identity,
            "alias": "phone",
            "credential": "old",
            "pairingId": "old-pair",
        }
        save_active(state, old)
        gateway.credentials["old"] = "old-pair"
        coordinator = Coordinator(state, instance="test")
        old_device, _ = await coordinator.device(old["deviceId"])
        gateway.reject_confirmation = True
        try:
            result = await coordinator.pair(
                gateway.args(), deadline=time.monotonic() + 20
            )
            assert result.result == "failed"
            assert result.data["registered"]
            assert active_material(state, None) == old
            assert coordinator.devices[old["deviceId"]] is old_device
            gateway.reject_confirmation = False
            assert (
                await coordinator.pair(
                    gateway.args(code="111111"), deadline=time.monotonic() + 20
                )
            ).result == "done"
            assert len(gateway.registers) == 1
            assert state.resolve_selection(None)[0].active.credential != "old"
        finally:
            await coordinator.close()


@pytest.mark.parametrize("failure", ["missing-pairing-id", "close"])
async def test_confirmation_validation_and_close_faults_do_not_activate(
    tmp_path, monkeypatch, failure
):
    async def dispatch(method, args):
        return completed(method).wire()

    gateway = Gateway(tmp_path / "tls", dispatch=dispatch)
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    reply = gateway.reply

    async def welcome(connection, value):
        if (
            failure == "missing-pairing-id"
            and value.get("event") == "connected"
            and value.get("pairingId") == "pair-1"
        ):
            value = {key: item for key, item in value.items() if key != "pairingId"}
        await reply(connection, value)

    monkeypatch.setattr(gateway, "reply", welcome)
    close = Peer.close
    close_error = Fault("confirmationClose", "controlled confirmation close failure")

    async def close_confirmation(peer, **kwargs):
        await close(peer, **kwargs)
        if failure == "close" and peer.welcome.get("pairingId") == "pair-1":
            raise close_error

    monkeypatch.setattr(Peer, "close", close_confirmation)
    async with gateway.listening():
        old = save_active(
            state,
            {
                **gateway.identity,
                "alias": "phone",
                "credential": "old",
                "pairingId": "old-pair",
            },
        )
        gateway.credentials["old"] = "old-pair"
        coordinator = Coordinator(state, instance="test")
        old_device, _ = await coordinator.device(old["deviceId"])
        assert (
            await old_device.request(
                "snapshot", {}, session="test", deadline=time.monotonic() + 3
            )
        ).result == "done"
        old_peer = old_device._peers["mutation"]
        await old_peer.ack_task
        try:
            with pytest.raises(Fault) as caught:
                await coordinator.pair(gateway.args(), deadline=time.monotonic() + 20)
            if failure == "close":
                assert caught.value is close_error
            else:
                assert caught.value.problem.code == "pairingUnconfirmed"
            assert len(gateway.registers) == 1
            saved = state.record(old["deviceId"])
            assert (
                saved.candidate.credential == gateway.registers[0]["args"]["credential"]
            )
            assert saved.material(saved.active) == old
            assert coordinator.devices[old["deviceId"]] is old_device
            assert old_peer.opened
            await asyncio.wait_for(
                asyncio.gather(*(c.wait_closed() for c in gateway.connections[1:])),
                1,
            )
            assert not coordinator.changing
            assert not coordinator.io_task
        finally:
            await coordinator.close()


async def test_unregistered_candidate_recovers_without_losing_old_route_or_credential(
    tmp_path,
):
    async def dispatch(method, args):
        return completed(method).wire()

    gateway = Gateway(tmp_path / "tls", dispatch=dispatch)
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        old = save_active(
            state,
            {
                **gateway.identity,
                "alias": "old",
                "credential": "old-credential",
                "pairingId": "old-pair",
            },
        )
        old_endpoint = old["endpoint"]
        gateway.credentials["old-credential"] = "old-pair"
        coordinator = Coordinator(state, instance="test")
        async with gateway.listening():
            record, _ = state.prepare_pairing(
                DeviceIdentity(
                    gateway.identity["deviceId"], gateway.identity["certificate"]
                ),
                gateway.identity["endpoint"],
                "new",
            )
            credential = record.candidate.credential
            try:
                with pytest.raises(Fault) as caught:
                    await coordinator.pair(
                        gateway.args(name="new", code="999999"),
                        deadline=time.monotonic() + 20,
                    )
                assert caught.value.problem.code == "unauthorized"
                assert not gateway.registers
                assert state.record(record.device_id).candidate.credential == credential
                assert active_material(state, "old") == old
                result = await coordinator.dispatch(
                    "call",
                    {
                        "args": {"timeoutMs": 15000},
                        "command": "status",
                        "device": "old",
                    },
                    deadline=time.monotonic() + 15,
                )
                assert result["result"] == "done"
                assert result["data"]["device"] == "old"
                old_port = gateway.connections[-1].local_address[1]
                assert old_endpoint == f"wss://127.0.0.1:{old_port}/androidctl"
                transfer = await coordinator.dispatch(
                    "call",
                    {
                        "device": "old",
                        "command": "file.start",
                        "args": {
                            "timeoutMs": 15000,
                            "direction": "push",
                            "local": str(tmp_path / "missing-source"),
                            "remote": "/unused",
                        },
                    },
                    deadline=time.monotonic() + 15,
                )
                job = coordinator.files.items[transfer["data"]["job"]]
                await job.task
                query = {
                    "device": "old",
                    "command": "job.get",
                    "args": {"job": job.id, "timeoutMs": 15000},
                }
                observed = await coordinator.dispatch(
                    "call", query, deadline=time.monotonic() + 15
                )
                assert observed["result"] == "not sent"
                assert observed["data"]["job"] == job.id
                assert (
                    await coordinator.pair(
                        gateway.args(name="new"), deadline=time.monotonic() + 20
                    )
                ).result == "done"
                active = state.resolve_selection("new")[0].active
                assert active.credential == credential
                assert active.pairing_id == "pair-1"
                assert active.endpoint == gateway.identity["endpoint"]
                assert len(gateway.registers) == 1
                assert gateway.registers[0]["args"]["credential"] == credential
                result = await coordinator.dispatch(
                    "call",
                    {
                        "args": {"timeoutMs": 15000},
                        "command": "status",
                        "device": "new",
                    },
                    deadline=time.monotonic() + 15,
                )
                assert result["result"] == "done"
                assert result["data"]["device"] == "new"
                assert gateway.connections[-1].local_address[1] != old_port
                with pytest.raises(Fault) as caught:
                    await coordinator.dispatch(
                        "call",
                        {**query, "device": "new"},
                        deadline=time.monotonic() + 15,
                    )
                assert caught.value.problem.code == "jobOwner"
            finally:
                await coordinator.close()


@pytest.mark.parametrize("failure", ["pin", "welcome", "budget", "connection"])
def test_pending_confirmation_failure_does_not_register(tmp_path, failure):
    async def run():
        gateway = Gateway(tmp_path / "tls")
        private_directory(tmp_path / "state")
        state = HostState(tmp_path / "state")
        async with gateway.listening():
            record, _ = state.prepare_pairing(
                DeviceIdentity(
                    gateway.identity["deviceId"], gateway.identity["certificate"]
                ),
                gateway.identity["endpoint"],
                "phone",
            )
            gateway.credentials[record.candidate.credential] = "original-pair"
            if failure == "pin":
                record.certificate = new_device_identity()["certificate"]
            if failure == "welcome":
                gateway.wrong_welcome = True
            if failure == "budget":
                gateway.welcome_release = asyncio.Event()
            state.save_record(record)
            args = gateway.args(timeoutMs=150 if failure == "budget" else 3000)
            if failure != "connection":
                await check(gateway, state, record, args)
        if failure == "connection":
            await check(gateway, state, record, args)

    async def check(gateway, state, record, args):
        coordinator = Coordinator(state, instance="test")
        try:
            with pytest.raises(Fault) as caught:
                await coordinator.pair(
                    args, deadline=time.monotonic() + args["timeoutMs"] / 1000
                )
            assert caught.value.problem.code != "unauthorized"
            assert not gateway.registers
            assert state.record(record.device_id) == record
            assert all(code is None for code, _ in gateway.headers)
        finally:
            if gateway.welcome_release is not None:
                gateway.welcome_release.set()
            await coordinator.close()

    asyncio.run(run())


@pytest.mark.parametrize("variant", ["expired", "future", "san", "welcome"])
async def test_bootstrap_identity_checks_before_register(tmp_path, variant):
    gateway = Gateway(tmp_path / "tls", variant=variant)
    gateway.wrong_welcome = variant == "welcome"
    async with gateway.listening():
        private_directory(tmp_path / "state")
        coordinator = Coordinator(HostState(tmp_path / "state"), instance="test")
        try:
            with pytest.raises(Fault) as caught:
                await coordinator.pair(gateway.args(), deadline=time.monotonic() + 20)
            assert caught.value.problem.stage == (
                "device.authentication" if variant == "welcome" else "device.tls"
            )
            assert gateway.registers == []
            assert len(gateway.headers) == (1 if variant == "welcome" else 0)
        finally:
            await coordinator.close()


async def test_known_uuid_changed_leaf_gets_no_code_or_bearer(tmp_path):
    first = Gateway(tmp_path / "first")
    second = Gateway(tmp_path / "second", identifier=first.identity["deviceId"])
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with first.listening():
        coordinator = Coordinator(state, instance="test")
        try:
            assert (
                await coordinator.pair(first.args(), deadline=time.monotonic() + 20)
            ).result == "done"
            async with second.listening():
                for name in ("phone", "other"):
                    with pytest.raises(Fault, match="certificate"):
                        await coordinator.pair(
                            second.args(name=name), deadline=time.monotonic() + 20
                        )
                    assert second.headers == []
                    assert second.registers == []
        finally:
            await coordinator.close()


async def test_bootstrap_keeps_local_configuration_error_source(tmp_path):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    path = state.pairings / f"{gateway.identity['deviceId']}.json"
    atomic_write(path, b"{}")
    async with gateway.listening():
        coordinator = Coordinator(state, instance="test")
        try:
            with pytest.raises(Fault) as caught:
                await coordinator.pair(
                    gateway.args(name="new-alias"), deadline=time.monotonic() + 20
                )
            assert caught.value.problem.code == "pairingInvalid"
            assert caught.value.problem.stage == "local.configuration"
            assert gateway.headers == []
            assert gateway.registers == []
            assert path.read_bytes() == b"{}"
        finally:
            await coordinator.close()


async def test_failed_repair_keeps_active_credential_and_device(tmp_path, monkeypatch):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    original = new_device_identity()
    before = paired(state, original, "phone")
    coordinator = Coordinator(state, instance="fixture")
    original_device, _ = await coordinator.device(before["deviceId"])
    attempted = []

    async def unavailable(cls, saved, **kwargs):
        attempted.append(saved["credential"])
        raise Fault("connectionFailed", "Injected before connection")

    monkeypatch.setattr(Peer, "open", classmethod(unavailable))
    with pytest.raises(Fault, match="Injected"):
        await coordinator.pair(
            {
                "endpoint": original["endpoint"],
                "code": "000042",
                "name": "phone",
                "timeoutMs": 20000,
            },
            deadline=time.monotonic() + 20,
        )
    saved = state.resolve_selection("phone")[0]
    assert saved.material(saved.active) == before
    assert coordinator.devices[before["deviceId"]] is original_device
    candidate = state.record(before["deviceId"]).candidate
    result = await coordinator.dispatch(
        "call",
        {"args": {"timeoutMs": 15000}, "command": "status"},
        deadline=time.monotonic() + 15,
    )
    assert result["result"] == "failed"
    assert result["error"]["code"] == "connectionFailed"
    assert "Injected" in result["reason"]
    assert [check["result"] for check in result["data"]["checks"]] == [
        "done",
        "failed",
        "not checked",
    ]
    assert attempted == [candidate.credential, before["credential"]]
    await coordinator.close()
