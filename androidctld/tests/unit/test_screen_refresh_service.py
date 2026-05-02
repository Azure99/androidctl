from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import RuntimeStatus
from androidctld.refs.models import RefRegistry
from androidctld.refs.service import RefRegistryBuilder
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.models import ScreenState
from androidctld.semantics.compiler import CompiledScreen, SemanticCompiler
from androidctld.semantics.public_models import public_group_nodes
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import ScreenRefreshService

from ..support.runtime_store import runtime_store_for_workspace
from .support.semantic_screen import make_contract_snapshot, make_raw_node


def make_snapshot(*, snapshot_id: int, captured_at: str):
    return make_contract_snapshot(
        make_raw_node(
            rid="w1:0",
            window_id="w1",
            class_name="android.widget.Button",
            resource_id="android:id/button1",
            text="Wi-Fi",
            bounds=(10, 20, 90, 60),
            editable=False,
            actions=("click",),
        ),
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        windowless=True,
    )


def test_refresh_semantic_noop_committed_snapshot_still_materializes_generation(
    tmp_path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
    )

    first_snapshot = make_snapshot(
        snapshot_id=100,
        captured_at="2026-04-10T00:00:00Z",
    )
    service.refresh(
        runtime,
        first_snapshot,
    )

    second_snapshot = make_snapshot(
        snapshot_id=101,
        captured_at="2026-04-10T00:00:05Z",
    )
    _, second_public, second_artifacts = service.refresh(
        runtime,
        second_snapshot,
    )

    assert runtime.latest_snapshot is not None
    assert runtime.latest_snapshot.snapshot_id == second_snapshot.snapshot_id
    assert runtime.current_screen_id == second_public.screen_id
    assert runtime.screen_sequence == 2

    state = runtime.screen_state
    assert state is not None
    assert state.public_screen is not None
    assert state.compiled_screen is not None
    assert state.artifacts is not None
    assert state.public_screen.screen_id == state.compiled_screen.screen_id
    assert state.public_screen.screen_id == runtime.current_screen_id
    assert state.compiled_screen.sequence == runtime.screen_sequence
    assert (
        state.compiled_screen.source_snapshot_id == runtime.latest_snapshot.snapshot_id
    )
    assert state.artifacts.screen_xml == second_artifacts.screen_xml

    assert second_artifacts.screen_json is not None
    assert second_artifacts.screen_xml is not None
    assert Path(second_artifacts.screen_json).suffix == ".json"
    assert Path(second_artifacts.screen_xml).suffix == ".xml"
    assert Path(second_artifacts.screen_json).is_file()
    assert Path(second_artifacts.screen_xml).is_file()
    assert not (runtime.artifact_root / "artifacts" / "obs-00002.md").exists()
    assert not (runtime.artifact_root / "artifacts" / "obs-00002.xml").exists()
    assert not (
        runtime.artifact_root / "artifacts" / "screens" / "obs-00002.md"
    ).exists()

    artifact_payload = json.loads(
        Path(second_artifacts.screen_json).read_text(encoding="utf-8")
    )
    assert artifact_payload["screenId"] == second_public.screen_id
    assert artifact_payload["sequence"] == runtime.screen_sequence
    assert artifact_payload["sourceSnapshotId"] == second_snapshot.snapshot_id
    assert artifact_payload["repairBindings"]
    for binding_payload in artifact_payload["repairBindings"].values():
        assert "lastSeenScreenId" not in binding_payload

    assert runtime.ref_registry.bindings
    for binding in runtime.ref_registry.bindings.values():
        assert binding.handle.snapshot_id == second_snapshot.snapshot_id


def test_refresh_with_runtime_kernel_persists_runtime_state(tmp_path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
    )

    snapshot = make_snapshot(
        snapshot_id=110,
        captured_at="2026-04-10T00:00:00Z",
    )
    _, public_screen, _ = service.refresh(
        runtime,
        snapshot,
    )

    reloaded = runtime_store_for_workspace(tmp_path).get_runtime()

    assert runtime.status is RuntimeStatus.READY
    assert runtime.current_screen_id == public_screen.screen_id
    assert reloaded.status is RuntimeStatus.BROKEN
    assert reloaded.screen_sequence == 1
    assert reloaded.current_screen_id is None


def test_refresh_uses_explicit_finalized_result_for_public_and_runtime_state(
    tmp_path,
) -> None:
    class _FinalizeResult:
        def __init__(self, *, registry: RefRegistry, compiled_screen) -> None:
            self.registry = registry
            self.compiled_screen = compiled_screen

    class FinalizeOnlyRefRegistryBuilder:
        def __init__(self) -> None:
            self._delegate = RefRegistryBuilder()
            self.source_compiled_screen = None
            self.reconcile_called = False

        def reconcile(self, **_kwargs) -> RefRegistry:
            self.reconcile_called = True
            raise AssertionError("refresh() must not call reconcile() directly")

        def finalize_compiled_screen(
            self,
            *,
            compiled_screen,
            snapshot_id: int,
            previous_registry: RefRegistry | None,
        ) -> _FinalizeResult:
            self.source_compiled_screen = compiled_screen
            finalized = deepcopy(compiled_screen)
            registry = self._delegate.reconcile(
                compiled_screen=finalized,
                snapshot_id=snapshot_id,
                previous_registry=previous_registry,
            )
            return _FinalizeResult(registry=registry, compiled_screen=finalized)

    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    finalize_only_builder = FinalizeOnlyRefRegistryBuilder()
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
        ref_registry_builder=finalize_only_builder,
    )

    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:input",
            window_id="w1",
            class_name="android.widget.EditText",
            resource_id="android:id/input",
            text=None,
            hint_text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
            bounds=(10, 20, 500, 120),
        ),
        make_raw_node(
            rid="w1:button",
            window_id="w1",
            class_name="android.widget.Button",
            resource_id="android:id/button1",
            text="Search",
            editable=False,
            actions=("click",),
            focused=False,
            bounds=(10, 130, 260, 220),
        ),
        snapshot_id=120,
        captured_at="2026-04-10T00:00:00Z",
        windowless=True,
    )
    _, public_screen, _ = service.refresh(
        runtime,
        snapshot,
    )

    assert finalize_only_builder.reconcile_called is False
    assert finalize_only_builder.source_compiled_screen is not None
    assert all(
        candidate.ref == ""
        for candidate in finalize_only_builder.source_compiled_screen.ref_candidates()
    )

    state = runtime.screen_state
    assert state is not None
    assert runtime.ref_registry.bindings
    input_binding = next(
        binding
        for binding in runtime.ref_registry.bindings.values()
        if binding.fingerprint.role == "input"
    )
    assert public_screen.surface.focus.input_ref == input_binding.ref
    assert state.public_screen.surface.focus.input_ref == input_binding.ref

    compiled_input = next(
        candidate
        for candidate in state.compiled_screen.ref_candidates()
        if candidate.role == "input"
    )
    assert compiled_input.ref == input_binding.ref


def test_refresh_finalizes_refs_from_captured_previous_registry(
    tmp_path,
) -> None:
    class MutatingCompiler(SemanticCompiler):
        def __init__(self) -> None:
            super().__init__()
            self.mutate_live_registry = False

        def compile(
            self,
            sequence: int,
            snapshot: RawSnapshot,
        ) -> CompiledScreen:
            compiled_screen = super().compile(sequence, snapshot)
            if self.mutate_live_registry:
                runtime.ref_registry.bindings.clear()
            return compiled_screen

    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    compiler = MutatingCompiler()
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
        semantic_compiler=compiler,
    )

    first_snapshot = make_snapshot(
        snapshot_id=130,
        captured_at="2026-04-10T00:00:00Z",
    )
    _, _first_public, _ = service.refresh(
        runtime,
        first_snapshot,
    )
    existing_binding = deepcopy(next(iter(runtime.ref_registry.bindings.values())))
    existing_binding.ref = "n7"
    runtime.ref_registry = RefRegistry(bindings={"n7": existing_binding})

    compiler.mutate_live_registry = True
    second_snapshot = make_snapshot(
        snapshot_id=131,
        captured_at="2026-04-10T00:00:05Z",
    )
    _, second_public, _ = service.refresh(
        runtime,
        second_snapshot,
    )

    assert "n7" in runtime.ref_registry.bindings
    assert runtime.ref_registry.bindings["n7"].reused is True
    assert public_group_nodes(second_public, "targets")[0].ref == "n7"


def test_refresh_with_stale_lifecycle_lease_before_commit_cancels_without_mutation(
    tmp_path,
) -> None:
    class StalingCompiler(SemanticCompiler):
        def compile(
            self,
            sequence: int,
            snapshot: RawSnapshot,
        ) -> CompiledScreen:
            compiled_screen = super().compile(sequence, snapshot)
            runtime.lifecycle_revision += 1
            return compiled_screen

    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
        semantic_compiler=StalingCompiler(),
    )
    lifecycle_lease = runtime_kernel.capture_lifecycle_lease(runtime)
    snapshot = make_snapshot(
        snapshot_id=140,
        captured_at="2026-04-10T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        service.refresh(
            runtime,
            snapshot,
            lifecycle_lease=lifecycle_lease,
        )

    assert error.value.code is DaemonErrorCode.COMMAND_CANCELLED
    assert runtime.status is RuntimeStatus.NEW
    assert runtime.screen_sequence == 0
    assert runtime.current_screen_id is None
    assert runtime.latest_snapshot is None
    assert runtime.screen_state is None
    assert runtime.ref_registry.bindings == {}
    screens_dir = runtime.artifact_root / "screens"
    assert not screens_dir.exists() or list(screens_dir.iterdir()) == []
    public_screens_dir = runtime.artifact_root / "artifacts" / "screens"
    assert not public_screens_dir.exists() or list(public_screens_dir.iterdir()) == []


def test_refresh_persist_failure_rolls_back_runtime_and_artifacts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime_kernel = RuntimeKernel(runtime_store)
    service = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
    )
    previous_snapshot = make_snapshot(
        snapshot_id=150,
        captured_at="2026-04-10T00:00:00Z",
    )
    previous_screen_state = ScreenState(public_screen=None)
    previous_registry = RefRegistry()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.screen_sequence = 2
    runtime.current_screen_id = "screen-00002"
    runtime.latest_snapshot = previous_snapshot
    runtime.screen_state = previous_screen_state
    runtime.ref_registry = previous_registry

    def _fail_persist(_runtime: object) -> None:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(runtime_kernel, "commit_runtime", _fail_persist)
    snapshot = make_snapshot(
        snapshot_id=151,
        captured_at="2026-04-10T00:00:05Z",
    )

    with pytest.raises(RuntimeError, match="persist failed"):
        service.refresh(
            runtime,
            snapshot,
        )

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.screen_sequence == 2
    assert runtime.current_screen_id == "screen-00002"
    assert runtime.latest_snapshot is previous_snapshot
    assert runtime.screen_state is previous_screen_state
    assert runtime.ref_registry is previous_registry
    screens_dir = runtime.artifact_root / "screens"
    assert screens_dir.exists()
    assert list(screens_dir.iterdir()) == []
    public_screens_dir = runtime.artifact_root / "artifacts" / "screens"
    assert public_screens_dir.exists()
    assert list(public_screens_dir.iterdir()) == []
