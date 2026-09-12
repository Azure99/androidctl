import uuid

import pytest

from androidctl.private_files import private_directory, write_json
from androidctl.protocol import Fault
from androidctl.state import HostState
from pairing_test_support import (
    active_material,
    new_device_identity,
    paired,
    save_active,
)


def test_device_list_selection_uses_the_listed_records(tmp_path, monkeypatch):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    first = paired(state, new_device_identity(), "first")
    replacement = {**new_device_identity(), "alias": "second", "credential": "second"}
    records = state.records

    def replace_after_read():
        yield from records()
        (state.pairings / f"{first['deviceId']}.json").unlink()
        save_active(state, replacement)

    monkeypatch.setattr(state, "records", replace_after_read)
    value = state.devices()
    assert value["selectionError"] is None
    assert value["selectionDevice"] == first["deviceId"]
    assert value["context"] == {
        "deviceId": first["deviceId"],
        "deviceSource": "sole",
    }
    assert "selectionSource" not in value
    assert [(item["alias"], item["selected"]) for item in value["devices"]] == [
        ("first", True)
    ]
    assert state.record(replacement["deviceId"]).active.alias == "second"


def test_broken_candidate_and_active_are_explicitly_recoverable(tmp_path):
    private_directory(tmp_path / "host")
    state = HostState(tmp_path / "host")
    saved = paired(state, new_device_identity(), "phone")
    path = state.pairings / (saved["deviceId"] + ".json")
    original = state.record(saved["deviceId"]).model_dump(by_alias=True)
    for patch in (
        {"candidate": {**saved, "credential": None}},
        {"active": {**saved, "pairingId": None}},
    ):
        write_json(path, {**original, **patch})
        bad = state.devices()["devices"][0]
        assert bad["error"]["code"] == "pairingInvalid"
        assert bad["path"] == str(path)
    state.forget(state.resolve_forget_key(str(path)))
    with pytest.raises(Fault) as missing:
        state.resolve_selection(None)[0]
    assert missing.value.problem.code == "deviceRequired"
    save_active(state, saved)
    write_json(state.selection, {})
    with pytest.raises(Fault):
        state.resolve_selection(None)[0]
    write_json(
        state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
    )
    assert active_material(state, None) == saved


def test_stale_selected_id_cannot_resolve_to_another_devices_alias(tmp_path):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    stale = str(uuid.uuid4())
    paired(
        state,
        new_device_identity(),
        stale,
    )
    write_json(state.selection, {"deviceId": stale})
    with pytest.raises(Fault, match="No unique"):
        state.resolve_selection(None)[0]
    assert state.resolve_selection(stale)[0].active.alias == stale


def test_device_alias_cannot_shadow_another_device_id(tmp_path):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    first, second = new_device_identity(), new_device_identity()
    paired(state, first, second["deviceId"])
    with pytest.raises(Fault, match="already paired"):
        paired(state, second, "two")
    with pytest.raises(Fault, match="already paired"):
        paired(state, new_device_identity(), first["deviceId"])
