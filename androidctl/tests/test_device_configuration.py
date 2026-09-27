import asyncio
import errno
import json
import time
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import coordinator as coordinator_module
from androidctl import local_call
from androidctl.coordinator import Coordinator
from androidctl.identity import DeviceIdentity
from androidctl.private_files import (
    PublishedWriteError,
    atomic_write,
    check_private,
    private_directory,
    write_json,
)
from androidctl.protocol import (
    Fault,
    Problem,
    completed,
)
from androidctl.state import HostState
from androidctl.transport import Peer
from certificate_test_support import certificate
from connection_test_support import connections, local_server
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


async def test_pending_pairing_can_be_repaired_but_not_executed_or_selected(tmp_path):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    identity = new_device_identity()
    record, _ = state.prepare_pairing(
        DeviceIdentity(identity["deviceId"], identity["certificate"]),
        identity["endpoint"],
        "pending",
    )
    coordinator = Coordinator(state, instance="pending-repair")
    try:
        for operation in (
            coordinator.device("pending"),
            coordinator.run(
                "device.select", {"device": "pending"}, time.monotonic() + 2
            ),
        ):
            with pytest.raises(Fault) as caught:
                await operation
            assert caught.value.problem.code == "pairingUnconfirmed"
        assert not state.selection.exists()
        assert coordinator.devices == {}
        endpoint = "wss://127.0.0.1:18879/androidctl"
        assert (
            await coordinator.address({"device": "pending", "endpoint": endpoint})
        ).result == "done"
        pem, der = certificate(ec.generate_private_key(ec.SECP256R1()), record.name)
        assert (
            await coordinator.trust({"device": "pending", "pem": pem})
        ).result == "done"
        saved = state.record(record.device_id)
        assert saved.active is None
        assert saved.candidate.endpoint == endpoint
        assert saved.certificate == der
    finally:
        await coordinator.close()


@pytest.mark.parametrize("failure", [None, "before", "published"])
@pytest.mark.parametrize("as_json", [False, True])
def test_device_selection_keeps_its_publication_fact(
    tmp_path, monkeypatch, failure, as_json
):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    old = paired(state, new_device_identity(), "old")
    new = paired(state, new_device_identity(), "new")
    write_json(state.selection, {"deviceId": old["deviceId"]})
    before = state.selection.read_bytes()

    def save(path, value):
        if failure == "before":
            raise OSError("controlled selection write failure")
        write_json(path, value)
        if failure == "published":
            raise PublishedWriteError("controlled directory sync failure")

    monkeypatch.setattr(coordinator_module, "write_json", save)

    async def run():
        coordinator = Coordinator(state, instance="selection")
        try:
            async with local_server(tmp_path / "ipc", coordinator.dispatch) as (
                saved,
                _,
            ):

                async def connect(*, deadline, start=True):
                    return await Peer.open(saved, source="local", deadline=deadline)

                monkeypatch.setattr(local_call, "connect_local", connect)
                response = await asyncio.to_thread(
                    CliRunner().invoke,
                    cli.app,
                    (["--json"] if as_json else []) + ["device", "select", "new"],
                )
                assert (
                    response.exit_code
                    == {None: 0, "before": 2, "published": 3}[failure]
                )
                outcome = "not sent" if failure == "before" else "done"
                if as_json:
                    value = json.loads(response.stdout)
                    assert value["command"] == "device select"
                    assert value["result"] == outcome
                    if failure is not None:
                        assert value["error"]["code"] == "localConfiguration"
                        assert value["error"]["stage"] == "local.configuration"
                    if failure != "before":
                        assert value["reason"] == "new"
                else:
                    assert f"device select: {outcome}; " in response.stdout
                    if failure != "before":
                        assert response.stdout.startswith("device select: done; new")
                    if failure is not None:
                        assert (
                            "code=localConfiguration; stage=local.configuration"
                            in response.stdout
                        )
                if failure == "before":
                    assert state.selection.read_bytes() == before
                    assert "controlled selection write failure" in response.stdout
                else:
                    assert state.selected_id() == new["deviceId"]
                if failure == "published":
                    assert (
                        "final file synchronization or cleanup failed"
                        in response.stdout
                    )
                    assert "controlled directory sync failure" in response.stdout
        finally:
            await coordinator.close()

    asyncio.run(run())


@pytest.mark.parametrize("command", ["select", "forget", "address", "trust"])
async def test_device_management_changes_only_the_positional_target(
    tmp_path, monkeypatch, command
):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    first = paired(state, new_device_identity(), "first")
    second = paired(state, new_device_identity(), "second")
    write_json(state.selection, {"deviceId": first["deviceId"]})
    first_path = state.pairings / (first["deviceId"] + ".json")
    second_path = state.pairings / (second["deviceId"] + ".json")
    first_before = first_path.read_bytes()
    second_before = second_path.read_bytes()
    monkeypatch.setenv("ANDROIDCTL_DEVICE", "first")
    arguments = []
    identifier = "second"
    if command == "address":
        arguments = ["127.0.0.1:18879"]
    elif command == "trust":
        pem, der = certificate(ec.generate_private_key(ec.SECP256R1()), second["name"])
        path = tmp_path / "certificate.pem"
        path.write_text(pem, encoding="ascii")
        arguments = [str(path)]
        identifier = second["deviceId"]

    coordinator = Coordinator(state, instance="positional-target")
    try:
        async with local_server(tmp_path / "ipc", coordinator.dispatch) as (saved, _):

            async def connect(*, deadline, start=True):
                return await Peer.open(saved, source="local", deadline=deadline)

            monkeypatch.setattr(local_call, "connect_local", connect)
            response = await asyncio.to_thread(
                CliRunner().invoke,
                cli.app,
                [
                    "--json",
                    "--device",
                    "first",
                    "device",
                    command,
                    identifier,
                    *arguments,
                ],
            )
        assert response.exit_code == 0, response.output
        value = json.loads(response.stdout)
        assert value["command"] == "device " + command
        assert value["result"] == "done"
        assert first_path.read_bytes() == first_before
        if command == "select":
            assert state.selected_id() == second["deviceId"]
            assert second_path.read_bytes() == second_before
        else:
            assert state.selected_id() == first["deviceId"]
            if command == "forget":
                assert not second_path.exists()
            elif command == "address":
                assert (
                    active_material(state, "second")["endpoint"]
                    == "wss://127.0.0.1:18879/androidctl"
                )
            else:
                assert state.record(second["deviceId"]).certificate == der
    finally:
        await coordinator.close()


def test_forget_removes_only_the_explicit_bad_record(tmp_path, monkeypatch):
    root = tmp_path / "state"
    private_directory(root)
    unrelated = root / "notes.json"
    atomic_write(unrelated, b"user notes")
    state = HostState(root)
    assert state.devices()["devices"] == []
    good = paired(state, new_device_identity(), "good")
    before = (state.pairings / (good["deviceId"] + ".json")).read_bytes()
    bad = state.pairings / (str(uuid.uuid4()) + ".json")
    atomic_write(bad, b"{")
    write_json(state.selection, {"deviceId": bad.stem})

    async def run():
        coordinator = Coordinator(state, instance="test")
        try:
            with pytest.raises(Fault):
                await coordinator.forget("../notes")
            async with local_server(tmp_path / "ipc", coordinator.dispatch) as (
                saved,
                _,
            ):

                async def connect(*, deadline, start=True):
                    return await Peer.open(saved, source="local", deadline=deadline)

                monkeypatch.setattr(local_call, "connect_local", connect)
                response = await asyncio.to_thread(
                    CliRunner().invoke,
                    cli.app,
                    ["--json", "device", "forget", str(bad)],
                )
            assert response.exit_code == 0, response.output
            result = json.loads(response.stdout)
            assert result["result"] == "done"
            assert result["data"]["selectionCleared"]
            assert not state.selection.exists()
            assert "not revoked" in result["reason"]
            assert not bad.exists()
            assert (
                state.pairings / (good["deviceId"] + ".json")
            ).read_bytes() == before
        finally:
            await coordinator.close()

    asyncio.run(run())
    assert unrelated.read_bytes() == b"user notes"


async def test_forget_reports_bad_selection_without_deleting_it(tmp_path):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    coordinator = Coordinator(state, instance="test")
    try:
        for value in ({}, {"deviceId": []}, {"deviceId": "bad"}):
            saved = paired(state, new_device_identity(), "phone")
            write_json(state.selection, value)
            before = state.selection.read_bytes()
            with pytest.raises(Fault) as invalid:
                state.selected_id()
            result = await coordinator.forget("phone")
            assert result.result == "failed"
            assert result.exit_code() == 1
            assert result.data == {
                "forgotten": saved["deviceId"],
                "selectionCleared": False,
                "selectionError": invalid.value.problem.wire(),
                "discardedJobFacts": 0,
            }
            assert result.data["selectionError"]["code"] == "selectionInvalid"
            assert result.data["selectionError"]["stage"] == "local.configuration"
            assert state.selection.read_bytes() == before
            assert not state.devices()["devices"]
    finally:
        await coordinator.close()


@pytest.mark.parametrize("target", ["pairing", "selection"])
async def test_forget_reports_only_completed_deletions(tmp_path, monkeypatch, target):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    saved = paired(state, new_device_identity(), "phone")
    pairing = state.pairings / f"{saved['deviceId']}.json"
    write_json(state.selection, {"deviceId": saved["deviceId"]})
    before = state.selection.read_bytes()
    failed_path = pairing if target == "pairing" else state.selection
    unlink = Path.unlink

    def fail_selected_path(path, *args, **kwargs):
        if path == failed_path:
            raise OSError("controlled unlink failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_path)
    coordinator = Coordinator(state, instance="forget-unlink")
    try:
        result = await coordinator.forget("phone")
        assert result.result == ("not sent" if target == "pairing" else "failed")
        assert result.exit_code() == (2 if target == "pairing" else 1)
        assert state.selection.read_bytes() == before
        assert pairing.exists() == (target == "pairing")
        if target == "selection":
            assert result.data == {
                "forgotten": saved["deviceId"],
                "selectionCleared": False,
                "selectionError": {
                    "code": "localConfiguration",
                    "message": "controlled unlink failure",
                    "stage": "local.configuration",
                },
                "discardedJobFacts": 0,
            }
        else:
            assert result.reason == "controlled unlink failure"
            assert result.error == Problem(
                code="localConfiguration",
                message="controlled unlink failure",
                stage="local.configuration",
            )
            assert "forgotten" not in result.data
            assert "selectionError" not in result.data
    finally:
        await coordinator.close()


def test_forget_retains_the_original_selection_problem(tmp_path, monkeypatch):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    saved = paired(state, new_device_identity(), "phone")
    problem = Problem(
        code="selectionInvalid",
        message="Original selection diagnostic",
        stage="local.configuration",
        diagnostic_truncated=True,
    )

    def invalid_selection():
        raise Fault.from_problem(problem)

    monkeypatch.setattr(state, "selected_id", invalid_selection)
    result = state.forget(saved["deviceId"])
    assert result["selectionError"] == problem.wire()
    assert result["forgotten"] == saved["deviceId"]
    assert result["selectionCleared"] is False
    assert not (state.pairings / f"{saved['deviceId']}.json").exists()


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
        assert "device forget" in bad["error"]["message"]
        assert bad["path"] == str(path)
    state.forget(state.resolve_forget_key(str(path)))
    with pytest.raises(Fault, match="pair HOST:PORT CODE"):
        state.resolve_selection(None)[0]
    save_active(state, saved)
    write_json(state.selection, {})
    with pytest.raises(Fault, match="device select"):
        state.resolve_selection(None)[0]
    write_json(
        state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
    )
    assert active_material(state, None) == saved


@pytest.mark.parametrize(
    "address",
    [
        "wss://[::1/androidctl",
        "wss://[not-ip]:18843/androidctl",
        "wss://127.0.0.1:18843/ipc",
    ],
)
async def test_malformed_device_address_is_rejected_without_changing_pairing(
    tmp_path, address
):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    saved = paired(state, new_device_identity(), "phone")
    coordinator = Coordinator(state, instance="test")
    try:
        with pytest.raises(Fault) as caught:
            await coordinator.address({"device": "phone", "endpoint": address})
        assert caught.value.problem.code == "endpoint"
        assert active_material(state, "phone") == saved
    finally:
        await coordinator.close()


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
