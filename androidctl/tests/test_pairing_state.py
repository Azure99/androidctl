import json
import time

import pytest

from androidctl import app as cli
from androidctl.coordinator import Coordinator
from androidctl.identity import DeviceIdentity
from androidctl.private_files import (
    PublishedWriteError,
    private_directory,
)
from androidctl.protocol import Fault, completed
from androidctl.state import HostState
from pairing_test_support import (
    Gateway,
    active_material,
    new_device_identity,
    save_active,
)


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
