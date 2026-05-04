from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.device.types import ConnectionSpec, DeviceEndpoint, RuntimeTransport
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.kernel import (
    has_live_public_screen,
    normalize_stale_ready_runtime,
)
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    AuthoritativeCurrentBasis,
    get_authoritative_current_basis,
)
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot

from ..support.runtime_store import runtime_store_for_workspace
from .support.runtime import (
    build_runtime,
    build_screen_artifacts,
    install_screen_state,
)
from .support.semantic_screen import (
    make_compiled_screen,
    make_public_screen,
    make_snapshot,
)


def _coherent_runtime(
    tmp_path: Path,
    *,
    screen_id: str = "screen-current",
    sequence: int = 7,
    snapshot_id: int = 42,
    status: RuntimeStatus = RuntimeStatus.READY,
) -> tuple[
    WorkspaceRuntime,
    RawSnapshot,
    PublicScreen,
    CompiledScreen,
    ScreenArtifacts,
]:
    runtime = build_runtime(tmp_path, status=status)
    snapshot = make_snapshot(
        snapshot_id=snapshot_id,
        captured_at="2026-04-13T00:00:42Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
    )
    public_screen = make_public_screen(
        screen_id,
        refs=("n1", "n2"),
        package_name="com.android.settings",
        activity_name="SettingsActivity",
    )
    compiled_screen = make_compiled_screen(
        screen_id,
        sequence=sequence,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
        ref="n1",
    )
    artifacts = build_screen_artifacts(runtime, screen_id=screen_id)
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )
    return runtime, snapshot, public_screen, compiled_screen, artifacts


def _connect_runtime(runtime: WorkspaceRuntime) -> None:
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )


def test_authoritative_current_basis_returns_copied_basis_and_public_refs(
    tmp_path: Path,
) -> None:
    runtime, snapshot, public_screen, compiled_screen, artifacts = _coherent_runtime(
        tmp_path
    )

    basis = get_authoritative_current_basis(runtime)

    assert basis == AuthoritativeCurrentBasis(
        screen_id=public_screen.screen_id,
        screen_sequence=compiled_screen.sequence,
        snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name=snapshot.package_name,
        activity_name=snapshot.activity_name,
        public_screen=basis.public_screen if basis is not None else public_screen,
        compiled_screen=basis.compiled_screen if basis is not None else compiled_screen,
        artifacts=basis.artifacts if basis is not None else artifacts,
        public_refs=frozenset({"n1", "n2"}),
    )
    assert basis is not None
    assert basis.public_screen is not public_screen
    assert basis.compiled_screen is not compiled_screen
    assert basis.artifacts is not artifacts
    assert basis.public_refs == frozenset({"n1", "n2"})


def test_authoritative_current_basis_is_stable_after_runtime_mutation(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    basis = get_authoritative_current_basis(runtime)
    assert basis is not None
    assert runtime.screen_state is not None
    assert runtime.screen_state.public_screen is not None
    assert runtime.screen_state.compiled_screen is not None

    runtime.current_screen_id = "screen-mutated"
    runtime.screen_state.public_screen.app.package_name = "com.example.changed"
    runtime.screen_state.compiled_screen.targets[0].label = "Changed"
    runtime.screen_state.artifacts = ScreenArtifacts(screen_json="/tmp/changed.json")

    assert basis.screen_id == "screen-current"
    assert basis.public_screen.app.package_name == "com.android.settings"
    assert basis.compiled_screen.targets[0].label == "Node"
    assert basis.artifacts is not None
    assert basis.artifacts.screen_json != "/tmp/changed.json"
    with pytest.raises(FrozenInstanceError):
        cast(object, basis).screen_id = "screen-reassigned"  # type: ignore[attr-defined]


def test_authoritative_current_basis_rejects_non_ready_with_stale_current_id(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path, status=RuntimeStatus.CONNECTED)

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_empty_current_id(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    runtime.current_screen_id = ""

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_missing_latest_snapshot(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    runtime.latest_snapshot = None

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_missing_screen_truth(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    runtime.screen_state = None

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_public_screen_id_mismatch(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    runtime.current_screen_id = "screen-other"

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_compiled_screen_id_mismatch(
    tmp_path: Path,
) -> None:
    runtime, snapshot, public_screen, _, artifacts = _coherent_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.compiled_screen = make_compiled_screen(
        "screen-other",
        sequence=runtime.screen_sequence,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name=public_screen.app.package_name,
        activity_name=public_screen.app.activity_name,
        fingerprint="other",
    )
    runtime.screen_state.artifacts = artifacts

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_stale_source_snapshot_id(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    assert runtime.screen_state is not None
    assert runtime.screen_state.compiled_screen is not None
    runtime.screen_state.compiled_screen.source_snapshot_id = 41

    assert get_authoritative_current_basis(runtime) is None


def test_authoritative_current_basis_rejects_sequence_mismatch(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    runtime.screen_sequence += 1

    assert get_authoritative_current_basis(runtime) is None


@pytest.mark.parametrize(
    "field_name",
    ["captured_at", "package_name", "activity_name"],
)
def test_authoritative_current_basis_rejects_snapshot_field_mismatch(
    tmp_path: Path,
    field_name: str,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    assert runtime.screen_state is not None
    assert runtime.screen_state.compiled_screen is not None
    setattr(runtime.screen_state.compiled_screen, field_name, "mismatch")

    assert get_authoritative_current_basis(runtime) is None


def test_has_live_public_screen_requires_transport_and_authoritative_basis(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)

    assert has_live_public_screen(runtime) is False

    _connect_runtime(runtime)

    assert has_live_public_screen(runtime) is True

    assert runtime.screen_state is not None
    assert runtime.screen_state.compiled_screen is not None
    runtime.screen_state.compiled_screen.source_snapshot_id = 41

    assert has_live_public_screen(runtime) is False


def test_drop_current_screen_authority_keeps_connection_and_artifacts(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(store)
    runtime, _, _, _, artifacts = _coherent_runtime(tmp_path)
    _connect_runtime(runtime)
    lease = kernel.capture_lifecycle_lease(runtime)
    old_sequence = runtime.screen_sequence

    assert kernel.drop_current_screen_authority(runtime, lease) is True

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is not None
    assert runtime.screen_sequence == old_sequence
    assert runtime.latest_snapshot is None
    assert runtime.previous_snapshot is None
    assert runtime.current_screen_id is None
    assert runtime.screen_state is None
    assert runtime.ref_registry.bindings == {}
    assert artifacts.screen_json is not None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "connected"
    assert "currentScreenId" not in persisted


def test_drop_current_screen_authority_preserves_rebootstrap_credentials_no_transport(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(store)
    runtime, _, _, _, artifacts = _coherent_runtime(tmp_path)
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = None
    lease = kernel.capture_lifecycle_lease(runtime)

    assert kernel.drop_current_screen_authority(runtime, lease) is True

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is None
    assert runtime.latest_snapshot is None
    assert runtime.previous_snapshot is None
    assert runtime.current_screen_id is None
    assert runtime.screen_state is None
    assert runtime.ref_registry.bindings == {}
    assert artifacts.screen_json is not None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "connected"
    assert "currentScreenId" not in persisted


def test_drop_current_screen_authority_can_discard_transport_for_rebootstrap(
    tmp_path: Path,
) -> None:
    store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(store)
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    _connect_runtime(runtime)
    recorder = []
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: recorder.append("closed"),
    )
    lease = kernel.capture_lifecycle_lease(runtime)

    assert (
        kernel.drop_current_screen_authority(
            runtime,
            lease,
            discard_transport=True,
        )
        is True
    )

    assert recorder == ["closed"]
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is None
    assert runtime.current_screen_id is None


def test_normalize_stale_ready_runtime_clears_public_current_mismatch(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    _connect_runtime(runtime)
    runtime.current_screen_id = "screen-other"

    assert normalize_stale_ready_runtime(runtime) is True

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.current_screen_id is None
    assert runtime.latest_snapshot is None
    assert runtime.screen_state is None


def test_normalize_stale_ready_runtime_clears_compiled_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    runtime, _, _, _, _ = _coherent_runtime(tmp_path)
    _connect_runtime(runtime)
    assert runtime.screen_state is not None
    assert runtime.screen_state.compiled_screen is not None
    runtime.screen_state.compiled_screen.source_snapshot_id = 41

    assert normalize_stale_ready_runtime(runtime) is True

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.current_screen_id is None
    assert runtime.latest_snapshot is None
    assert runtime.screen_state is None
