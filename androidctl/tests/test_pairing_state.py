import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from androidctl import app as cli
from androidctl.coordinator import Coordinator
from androidctl.identity import DeviceIdentity
from androidctl.private_files import (
    PublishedWriteError,
    private_directory,
)
from androidctl.protocol import Fault, completed
from androidctl.state import HostState
from certificate_test_support import certificate
from connection_test_support import connections
from pairing_test_support import (
    Gateway,
    active_material,
    new_device_identity,
    save_active,
)


@pytest.mark.parametrize("close_fails", [False, True])
async def test_connection_publication_survives_sync_failure_and_closes_old_peer(
    tmp_path, monkeypatch, close_fails
):
    calls = []

    async def dispatch(method, args):
        calls.append(method)
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, material, _):
        state = coordinator.state
        before, _ = state.prepare_pairing(
            DeviceIdentity(material["deviceId"], material["certificate"]),
            material["endpoint"],
            "pending-name",
        )
        device, _ = await coordinator.device(material["deviceId"])
        assert (
            await device.request(
                "snapshot", {}, session="test", deadline=time.monotonic() + 3
            )
        ).result == "done"
        peer = device._peers["mutation"]
        await peer.ack_task
        assert peer.opened
        save = state.save_record

        def sync_failure(record):
            save(record)
            raise PublishedWriteError("controlled directory sync failure")

        monkeypatch.setattr(state, "save_record", sync_failure)
        endpoint = "wss://updated.example:18843/androidctl"
        with monkeypatch.context() as close_patch:
            if close_fails:
                close = device.close

                async def failed_close(**kwargs):
                    await close(**kwargs)
                    raise OSError("controlled old connection close failure")

                close_patch.setattr(device, "close", failed_close)
            result = await coordinator.address(
                {"device": "phone", "endpoint": endpoint}
            )
        assert result.result == "done"
        assert result.exit_code() == 3
        assert result.error.code == "localConfiguration"
        assert result.error.stage == "local.configuration"
        assert result.error.message == "controlled directory sync failure"
        assert "final file synchronization or cleanup failed" in result.reason
        assert result.data["context"] == {
            "deviceId": material["deviceId"],
            "deviceSource": "args",
        }
        if close_fails:
            assert (
                result.data["cleanupError"] == "controlled old connection close failure"
            )
        else:
            assert "cleanupError" not in result.data
        current = state.record(material["deviceId"])
        assert current.active.endpoint == current.candidate.endpoint == endpoint
        assert current.active.credential == before.active.credential
        assert current.candidate.credential == before.candidate.credential
        assert device.record == current
        assert not peer.opened
        assert peer.connection.state.name == "CLOSED"
        assert not device._peers
        assert not device.in_use()
        assert not coordinator.changing
        assert not coordinator.io_task
        assert calls == ["snapshot"]


def test_device_summary_reports_only_the_active_pairing_id(tmp_path, capsys):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    material = new_device_identity()
    pending, _ = state.prepare_pairing(
        DeviceIdentity(material["deviceId"], material["certificate"]),
        material["endpoint"],
        "pending",
    )
    active = save_active(
        state,
        {
            **new_device_identity(),
            "alias": "active",
            "credential": "private",
            "pairingId": "real-pair",
        },
    )
    facts = state.devices()
    by_id = {item["deviceId"]: item for item in facts["devices"]}
    assert "pairingId" not in by_id[pending.device_id]
    assert by_id[active["deviceId"]]["pairingId"] == "real-pair"
    for as_json in (False, True):
        with pytest.raises(cli.typer.Exit) as caught:
            cli.emit(
                completed("devices", facts), as_json=as_json, render=cli.render_devices
            )
        assert caught.value.exit_code == 0
        output = capsys.readouterr().out
        assert "private" not in output
        if as_json:
            assert json.loads(output)["data"] == facts
        else:
            lines = output.splitlines()
            assert "pairingId=" not in next(
                line for line in lines if pending.device_id in line
            )
            assert "pairingId=real-pair" in next(
                line for line in lines if active["deviceId"] in line
            )


@pytest.mark.parametrize("alias", [" ", "x" * 101, "bad\nname", "bad\x00name"])
def test_state_rejects_invalid_alias_without_saving_candidate(tmp_path, alias):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    material = new_device_identity()
    with pytest.raises(Fault) as caught:
        state.prepare_pairing(
            DeviceIdentity(material["deviceId"], material["certificate"]),
            material["endpoint"],
            alias,
        )
    assert caught.value.problem.code == "deviceName"
    assert state.devices()["devices"] == []


@pytest.mark.parametrize(
    "stage",
    [
        "candidate",
        "candidate-published",
        "promotion",
        "published",
    ],
)
async def test_configuration_failures_preserve_actual_publication_fact(
    tmp_path, monkeypatch, stage
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        old = {
            **gateway.identity,
            "alias": "phone",
            "credential": "old",
            "pairingId": "old",
        }
        save_active(state, old)
        coordinator = Coordinator(state, instance="test")
        old_device, _ = await coordinator.device(old["deviceId"])
        original = state.save_record

        def save(record):
            candidate = record.candidate
            trigger = (
                stage.startswith("candidate")
                and candidate is not None
                or stage in ("promotion", "published")
                and candidate is None
            )
            if not trigger:
                return original(record)
            if stage.endswith("published"):
                original(record)
                raise PublishedWriteError("injected after replace")
            raise OSError("injected before replace")

        monkeypatch.setattr(state, "save_record", save)
        try:
            if stage.startswith("candidate"):
                with pytest.raises(Fault) as caught:
                    await coordinator.pair(
                        gateway.args(), deadline=time.monotonic() + 20
                    )
                assert caught.value.problem.stage == "local.configuration"
                assert gateway.registers == []
                assert (state.record(old["deviceId"]).candidate is not None) is (
                    stage == "candidate-published"
                )
            else:
                result = await coordinator.pair(
                    gateway.args(), deadline=time.monotonic() + 20
                )
                assert result.data["confirmed"]
                assert result.data["deviceId"] == old["deviceId"]
                assert result.data["activated"] is (stage == "published")
                assert result.result == ("done" if stage == "published" else "failed")
                assert result.exit_code() == (3 if stage == "published" else 1)
            if stage != "published":
                assert active_material(state, None) == old
                assert coordinator.devices[old["deviceId"]] is old_device
            else:
                assert (
                    state.resolve_selection(None)[0].active.credential
                    != old["credential"]
                )
                assert (
                    coordinator.devices[old["deviceId"]].record
                    == state.resolve_selection(None)[0]
                )
            assert not coordinator.changing
            assert not coordinator.io_task
        finally:
            await coordinator.close()


async def test_connection_updates_keep_candidate_and_published_result_on_close_failure(
    tmp_path, monkeypatch, capsys
):
    gateway = Gateway(tmp_path / "tls")
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    async with gateway.listening():
        old = {
            **gateway.identity,
            "alias": "phone",
            "credential": "old",
            "pairingId": "old",
        }
        save_active(state, old)
        record, _ = state.prepare_pairing(
            DeviceIdentity(old["deviceId"], old["certificate"]),
            old["endpoint"],
            "new-name",
        )
        candidate = record.candidate.model_dump(by_alias=True)
        coordinator = Coordinator(state, instance="test")
        device, _ = await coordinator.device(old["deviceId"])

        async def close_error(**kwargs):
            raise OSError("injected connection close")

        close = device.close
        monkeypatch.setattr(device, "close", close_error)
        try:
            endpoint = "wss://public.example:443/androidctl"
            result = await coordinator.address(
                {"device": "phone", "endpoint": endpoint}
            )
            assert result.result == "done"
            assert result.exit_code() == 3
            assert result.data["cleanupError"] == "injected connection close"
            assert result.data["context"] == {
                "deviceId": old["deviceId"],
                "deviceSource": "args",
            }
            assert result.error is None
            with pytest.raises(cli.typer.Exit) as stopped:
                cli.emit(result)
            assert stopped.value.exit_code == 3
            assert "injected connection close" in capsys.readouterr().out
            with pytest.raises(cli.typer.Exit):
                cli.emit(result, as_json=True)
            assert json.loads(capsys.readouterr().out) == result.wire()
            assert device.active.endpoint == endpoint
            record = state.record(old["deviceId"])
            assert record.candidate.model_dump(by_alias=True) == {
                **candidate,
                "endpoint": endpoint,
            }
            pem, der = certificate(ec.generate_private_key(ec.SECP256R1()), old["name"])
            result = await coordinator.trust({"device": "phone", "pem": pem})
            assert result.result == "done"
            assert result.exit_code() == 3
            assert result.data["cleanupError"] == "injected connection close"
            assert result.error is None
            record = state.record(old["deviceId"])
            assert record.certificate == der
            assert record == device.record
            assert record.candidate.credential == candidate["credential"]
        finally:
            monkeypatch.setattr(device, "close", close)
        assert (await coordinator.forget("phone")).result == "done"
        assert state.devices()["devices"] == []
        await coordinator.close()
