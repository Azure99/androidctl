import asyncio
import errno
import time
import uuid

import pytest

from androidctl.coordinator import Coordinator
from androidctl.private_files import (
    atomic_write,
    check_private,
    private_directory,
    write_json,
)
from androidctl.protocol import (
    Fault,
    completed,
)
from androidctl.state import HostState
from androidctl.transport import Peer
from connection_test_support import connections
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


async def test_first_device_use_reads_current_saved_configuration(tmp_path):
    async def dispatch(method, args):
        return completed(method).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, _):
        record = coordinator.state.resolve_selection(None)[0]
        record.active.alias = "renamed-before-first-use"
        coordinator.state.save_record(record)
        result = await coordinator.run(
            "call",
            {"command": "status", "args": {"timeoutMs": 15000}},
            deadline=time.monotonic() + 15,
        )
        assert result.result == "done"
        assert result.data["device"] == "renamed-before-first-use"


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
        assert bad["error"]["message"]
        assert bad["path"] == str(path)
    state.forget(state.resolve_forget_key(str(path)))
    with pytest.raises(Fault, match="pair HOST:PORT CODE"):
        state.resolve_selection(None)[0]
    save_active(state, saved)
    write_json(state.selection, {})
    with pytest.raises(Fault):
        state.resolve_selection(None)[0]
    write_json(
        state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
    )
    assert active_material(state, None) == saved


@pytest.mark.parametrize(
    "corruption", ["filename", "json", "fields", "certificate", "endpoint"]
)
def test_bad_pairing_never_silently_selects_the_good_device(
    tmp_path, monkeypatch, corruption
):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    saved = paired(
        state,
        new_device_identity(),
        "good",
    )
    path = state.pairings / (
        ("invalid" if corruption == "filename" else str(uuid.uuid4())) + ".json"
    )
    atomic_write(path, b"{" if corruption == "json" else b"{}")
    if corruption in {"certificate", "endpoint"}:
        record = state.record(saved["deviceId"]).model_dump(by_alias=True)
        if corruption == "endpoint":
            record["active"]["endpoint"] = "wss://127.0.0.1:18843/ipc"
        else:
            record["certificate"] = "fixture"
        write_json(
            path,
            {
                **record,
                "deviceId": path.stem,
            },
        )
    attempted = []

    async def open_peer(cls, material, **kwargs):
        attempted.append(material["deviceId"])
        raise Fault("connectionFailed", "Fixture connect boundary reached")

    monkeypatch.setattr(Peer, "open", classmethod(open_peer))

    async def run():
        coordinator = Coordinator(state, instance="fixture")

        async def local_failure(code, detail):
            result = await coordinator.dispatch(
                "call",
                {"args": {"timeoutMs": 15000}, "command": "status"},
                deadline=time.monotonic() + 15,
            )
            assert result["result"] == "failed"
            assert result["error"]["code"] == code
            assert result["error"]["message"] == result["reason"]
            assert detail in result["reason"]
            assert [check["result"] for check in result["data"]["checks"]] == [
                "failed",
                "not checked",
                "not checked",
            ]

        try:
            result = await coordinator.dispatch(
                "devices", {}, deadline=time.monotonic() + 15
            )
            broken = [item for item in result["data"]["devices"] if item.get("error")]
            assert len(broken) == 1
            assert broken[0]["path"] == str(path)
            assert broken[0]["error"]["code"] == "pairingInvalid"
            assert result["data"]["context"] == {"deviceSource": "default"}
            assert result["data"]["selectionDevice"] is None
            assert not any(item["selected"] for item in result["data"]["devices"])
            await local_failure("pairingInvalid", "Unusable")
            write_json(state.selection, {"deviceId": "good"})
            await local_failure("selectionInvalid", str(state.selection))
            write_json(state.selection, {"deviceId": str(uuid.uuid4())})
            await local_failure("deviceNotFound", "No unique")
            atomic_write(state.selection, b"{")
            invalid = await coordinator.dispatch(
                "devices", {}, deadline=time.monotonic() + 15
            )
            assert invalid["data"]["selectionError"]["code"] == "selectionInvalid"
            assert invalid["data"]["context"] == {"deviceSource": "default"}
            await local_failure("selectionInvalid", str(state.selection))
            assert attempted == []
            result = await coordinator.dispatch(
                "call",
                {"args": {"timeoutMs": 15000}, "command": "status", "device": "good"},
                deadline=time.monotonic() + 15,
            )
            assert result["result"] == "failed"
            assert result["error"]["code"] == "connectionFailed"
            assert "boundary reached" in result["reason"]
            assert [check["result"] for check in result["data"]["checks"]] == [
                "done",
                "failed",
                "not checked",
            ]
            assert attempted == [saved["deviceId"]]
        finally:
            await coordinator.close()

    asyncio.run(run())


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


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_configuration_io_failure_is_proven_before_device_dispatch(
    tmp_path, monkeypatch, code
):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="fixture")

    def fail(identifier):
        raise OSError(code, "injected state read")

    monkeypatch.setattr(state, "resolve_selection", fail)
    with pytest.raises(Fault) as caught:
        asyncio.run(
            coordinator.dispatch(
                "call",
                {"args": {"timeoutMs": 15000}, "command": "click"},
                deadline=time.monotonic() + 15,
            )
        )
    assert caught.value.problem.code == "localConfiguration"
    assert coordinator.devices == {}


def test_pairing_configuration_is_private_and_selection_is_explicit(tmp_path):
    root = tmp_path / "private"
    private_directory(root)
    state = HostState(root)
    first = paired(state, new_device_identity(), "phone")
    assert state.resolve_selection(None)[0].device_id == first["deviceId"]
    second = paired(state, new_device_identity(), "tablet")
    with pytest.raises(Fault, match="Select"):
        state.resolve_selection(None)[0]
    assert state.resolve_selection("tablet")[0].device_id == second["deviceId"]
    write_json(
        state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
    )
    assert state.resolve_selection(None)[0].device_id == first["deviceId"]
    Coordinator(state, instance="fixture")
    for path in state.pairings.iterdir():
        check_private(path)


def test_device_alias_cannot_shadow_another_device_id(tmp_path):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    first, second = new_device_identity(), new_device_identity()
    paired(state, first, second["deviceId"])
    with pytest.raises(Fault, match="already paired"):
        paired(state, second, "two")
    with pytest.raises(Fault, match="already paired"):
        paired(state, new_device_identity(), first["deviceId"])
