from __future__ import annotations

import json
from pathlib import Path

import pytest

from androidctld.device.types import DeviceEndpoint, RuntimeTransport
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import ScreenState, WorkspaceRuntime
from androidctld.schema.core import SchemaDecodeError

from ..support.runtime_store import runtime_store_for_workspace


def _assert_complete_live_shape(runtime: WorkspaceRuntime) -> None:
    assert runtime.connection is None
    assert runtime.device_token is None
    assert runtime.device_capabilities is None
    assert runtime.transport is None
    assert runtime.latest_snapshot is None
    assert runtime.previous_snapshot is None
    assert runtime.screen_state is None
    assert runtime.ref_registry.bindings == {}
    assert runtime.progress_occupant_kind is None
    acquired = runtime.progress_lock.acquire(blocking=False)
    try:
        assert acquired is True
    finally:
        if acquired:
            runtime.progress_lock.release()


def test_runtime_store_creates_workspace_runtime_under_fixed_paths(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)

    runtime = store.get_runtime()

    assert runtime.workspace_root == tmp_path.resolve()
    assert runtime.artifact_root == tmp_path.resolve() / ".androidctl"
    assert runtime.runtime_path == tmp_path.resolve() / ".androidctl" / "runtime.json"
    assert runtime.status is RuntimeStatus.NEW


def test_runtime_store_creates_runtime_with_complete_live_shape(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)

    runtime = store.get_runtime()

    _assert_complete_live_shape(runtime)


def test_runtime_store_persists_under_workspace_runtime_json(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)

    runtime = store.get_runtime()
    store.persist_runtime(runtime)

    runtime_path = tmp_path / ".androidctl" / "runtime.json"
    assert runtime_path.exists()


def test_runtime_store_persists_canonical_json_without_rebinding_live_paths(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = store.get_runtime()
    stale_workspace_root = tmp_path / "stale"
    stale_artifact_root = tmp_path / "stale-artifacts"
    stale_runtime_path = tmp_path / "stale-runtime.json"
    runtime.workspace_root = stale_workspace_root
    runtime.artifact_root = stale_artifact_root
    runtime.runtime_path = stale_runtime_path
    runtime.status = RuntimeStatus.READY
    runtime.screen_sequence = 4
    runtime.current_screen_id = "screen-00004"
    transport_closed = False

    def close_transport() -> None:
        nonlocal transport_closed
        transport_closed = True

    transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=close_transport,
    )
    screen_state = ScreenState(public_screen=None)
    runtime.transport = transport
    runtime.screen_state = screen_state

    store.persist_runtime(runtime)

    assert runtime.workspace_root == stale_workspace_root
    assert runtime.artifact_root == stale_artifact_root
    assert runtime.runtime_path == stale_runtime_path
    assert runtime.transport is transport
    assert runtime.screen_state is screen_state
    assert transport_closed is False

    assert not stale_runtime_path.exists()
    payload = json.loads(
        (tmp_path / ".androidctl" / "runtime.json").read_text(encoding="utf-8")
    )
    assert payload == {
        "schemaVersion": 1,
        "status": "broken",
        "screenSequence": 4,
        "updatedAt": payload["updatedAt"],
    }
    assert runtime.status is RuntimeStatus.READY
    assert runtime.current_screen_id == "screen-00004"

    reloaded = runtime_store_for_workspace(tmp_path).get_runtime()

    assert reloaded.workspace_root == tmp_path.resolve()
    assert reloaded.artifact_root == tmp_path.resolve() / ".androidctl"
    assert reloaded.runtime_path == tmp_path.resolve() / ".androidctl" / "runtime.json"
    assert reloaded.status is RuntimeStatus.BROKEN
    assert reloaded.screen_sequence == 4
    assert reloaded.current_screen_id is None


def test_runtime_store_persists_ready_as_restart_valid_broken(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.screen_sequence = 7
    runtime.current_screen_id = "screen-123"
    store.persist_runtime(runtime)

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["status"] == "broken"
    assert payload["screenSequence"] == 7
    assert "currentScreenId" not in payload
    assert runtime.status is RuntimeStatus.READY
    assert runtime.current_screen_id == "screen-123"

    reloaded = runtime_store_for_workspace(tmp_path).get_runtime()

    assert reloaded.status is RuntimeStatus.BROKEN
    assert reloaded.screen_sequence == 7
    assert reloaded.current_screen_id is None


@pytest.mark.parametrize(
    "status",
    [RuntimeStatus.CONNECTED, RuntimeStatus.BOOTSTRAPPING],
)
def test_runtime_store_preserves_non_ready_statuses_but_clears_current_screen(
    tmp_path: Path,
    status: RuntimeStatus,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = store.get_runtime()
    runtime.status = status
    runtime.screen_sequence = 7
    runtime.current_screen_id = "screen-123"
    store.persist_runtime(runtime)

    payload = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert payload["status"] == status.value
    assert "currentScreenId" not in payload
    assert runtime.status is status
    assert runtime.current_screen_id == "screen-123"

    reloaded = runtime_store_for_workspace(tmp_path).get_runtime()

    assert reloaded.status is status
    assert reloaded.screen_sequence == 7
    assert reloaded.current_screen_id is None


def test_runtime_store_rejects_ready_runtime_json_status(
    tmp_path: Path,
) -> None:
    runtime_path = tmp_path / ".androidctl" / "runtime.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "ready",
                "screenSequence": 7,
                "updatedAt": "2026-04-21T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SchemaDecodeError) as error:
        runtime_store_for_workspace(tmp_path).get_runtime()

    assert error.value.field == "runtime.status"
    assert error.value.problem == "persisted runtime status cannot be ready"


def test_runtime_store_reloads_runtime_with_complete_live_shape(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    runtime = store.get_runtime()
    runtime.status = RuntimeStatus.READY
    store.persist_runtime(runtime)

    reloaded = runtime_store_for_workspace(tmp_path).get_runtime()

    _assert_complete_live_shape(reloaded)


def test_runtime_store_rejects_unknown_runtime_json_fields(
    tmp_path: Path,
) -> None:
    runtime_path = tmp_path / ".androidctl" / "runtime.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "broken",
                "screenSequence": 0,
                "updatedAt": "2026-04-21T00:00:00Z",
                "unexpectedField": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SchemaDecodeError) as error:
        runtime_store_for_workspace(tmp_path).get_runtime()

    assert error.value.field == "runtime"
    assert error.value.problem == "has unsupported fields"
