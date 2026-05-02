from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from androidctld.actions.repair import ActionCommandRepairer
from androidctld.actions.request_builder import (
    build_action_request,
)
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.screen_payloads import (
    ScreenArtifactPayload,
    build_screen_artifact_payload,
)
from androidctld.commands.command_models import TapCommand
from androidctld.commands.handlers.action import ActionCommandHandler
from androidctld.commands.models import CommandRecord, CommandStatus
from androidctld.errors import DaemonError
from androidctld.protocol import CommandKind
from androidctld.refs import repair as ref_repair_module
from androidctld.refs.models import NodeHandle, RefRegistry
from androidctld.refs.repair import ref_stale_error
from androidctld.refs.service import (
    best_candidate_for_binding,
    best_candidate_for_source_signature,
    binding_for_candidate,
    source_signature_from_binding,
)
from androidctld.runtime import capture_lifecycle_lease
from androidctld.semantics.compiler import SemanticCompiler, SemanticNode
from androidctld.semantics.models import SemanticMeta

from .support.doubles import (
    CallbackScreenRefresh,
    PassiveRuntimeKernel,
    StaticSnapshotService,
)
from .support.runtime import build_runtime, install_screen_state
from .support.semantic_screen import (
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
    make_raw_node,
)

_REPAIR_REF = "n1"
_SOURCE_SCREEN_ID = "screen-00041"
_CURRENT_SCREEN_ID = "screen-00042"
_PUBLIC_REF_STALE_MESSAGE = (
    "The referenced element is no longer available on the current screen."
)
_REPAIR_DIAGNOSTIC_TOKENS = {
    "details",
    "sourceArtifactStatus",
    "repairDecision",
    "repairStatus",
    "sourceArtifact",
    "source_unavailable",
    "invalid_artifact",
    "repair_failed",
}


def make_candidate(
    *,
    rid: str,
    role: str = "button",
    label: str = "Wi-Fi",
    state: tuple[str, ...] = (),
    actions: tuple[str, ...] = ("tap",),
    resource_id: str | None = "android:id/button1",
    class_name: str = "android.widget.Button",
    parent_role: str = "container",
    parent_label: str = "Network",
    sibling_labels: tuple[str, ...] = ("Bluetooth",),
    relative_bounds: tuple[int, int, int, int] = (10, 20, 50, 30),
) -> SemanticNode:
    return SemanticNode(
        raw_rid=rid,
        role=role,
        label=label,
        state=list(state),
        actions=list(actions),
        bounds=(0, 0, 0, 0),
        meta=SemanticMeta(resource_id=resource_id, class_name=class_name),
        targetable=True,
        score=100,
        group="targets",
        parent_role=parent_role,
        parent_label=parent_label,
        sibling_labels=list(sibling_labels),
        relative_bounds=relative_bounds,
    )


def test_screen_artifact_payload_rejects_unknown_debug_fields():
    payload = {
        "screenId": "screen-00001",
        "sequence": 1,
        "sourceSnapshotId": 100,
        "capturedAt": "2026-04-03T00:00:00Z",
        "packageName": "com.android.settings",
        "activityName": "com.android.settings.Settings",
        "keyboardVisible": False,
        "groups": {
            "targets": [],
            "context": [],
            "dialog": [],
            "keyboard": [],
            "system": [],
        },
        "repairBindings": {},
        "debugOnly": {"score": 7},
    }
    with pytest.raises(ValidationError, match="debugOnly"):
        ScreenArtifactPayload.model_validate_json(json.dumps(payload))


def make_ready_runtime(tmp_path: Path):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=42,
        current_screen_id=_CURRENT_SCREEN_ID,
    )
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:0.1",
            class_name="android.widget.Button",
            text="Wi-Fi",
            editable=False,
            focusable=False,
            actions=("click",),
        )
    )
    compiled_screen = SemanticCompiler().compile(42, snapshot)
    current_screen_path = runtime.artifact_root / "screens" / "obs-00042.json"
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_contract_screen(
            screen_id=_CURRENT_SCREEN_ID,
            sequence=42,
            source_snapshot_id=snapshot.snapshot_id,
            captured_at=snapshot.captured_at,
            targets=(
                make_public_node(
                    ref=_REPAIR_REF,
                    role="input",
                    label="Wi-Fi",
                    actions=("tap",),
                ),
            ),
        ),
        compiled_screen=compiled_screen,
        artifacts=ScreenArtifacts(screen_json=current_screen_path.as_posix()),
    )
    return runtime


def write_repair_artifact(
    runtime,
    *,
    source_screen_id: str,
    sequence: int,
    extra_fields: dict[str, object] | None = None,
) -> Path:
    assert runtime.screen_state is not None
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    candidate = compiled_screen.ref_candidates()[0]
    source_screen = compiled_screen.to_public_screen().model_copy(
        update={
            "screen_id": source_screen_id,
        }
    )
    source_registry = RefRegistry(
        bindings={
            _REPAIR_REF: binding_for_candidate(
                ref=_REPAIR_REF,
                candidate=candidate,
                snapshot_id=sequence,
                reused=False,
            )
        }
    )
    payload = build_screen_artifact_payload(
        source_screen,
        source_registry,
        sequence=sequence,
        source_snapshot_id=sequence,
        captured_at="2026-04-08T00:00:00Z",
    )
    if extra_fields:
        payload.update(extra_fields)
    screens_dir = runtime.artifact_root / "screens"
    screens_dir.mkdir(parents=True, exist_ok=True)
    path = screens_dir / f"obs-{sequence:05d}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_command_record(*, kind: CommandKind = CommandKind.TAP) -> CommandRecord:
    return CommandRecord(
        command_id="cmd-00001",
        kind=kind,
        status=CommandStatus.RUNNING,
        started_at="2026-04-27T00:00:00Z",
    )


def bind_current_repair_ref(runtime) -> NodeHandle:
    assert runtime.screen_state is not None
    assert runtime.latest_snapshot is not None
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    candidate = compiled_screen.ref_candidates()[0]
    binding = binding_for_candidate(
        ref=_REPAIR_REF,
        candidate=candidate,
        snapshot_id=runtime.latest_snapshot.snapshot_id,
        reused=False,
    )
    runtime.ref_registry = RefRegistry(bindings={_REPAIR_REF: binding})
    return binding.handle


def make_refreshed_repairer(runtime, *, refreshed_rid: str = "w1:0.9"):
    refreshed_snapshot = make_contract_snapshot(
        make_raw_node(
            rid=refreshed_rid,
            class_name="android.widget.Button",
            text="Wi-Fi",
            editable=False,
            focusable=False,
            actions=("click",),
        ),
        snapshot_id=43,
    )

    def refresh_runtime(session, snapshot, **kwargs):
        del kwargs
        compiled_screen = SemanticCompiler().compile(43, snapshot)
        install_screen_state(
            session,
            snapshot=snapshot,
            public_screen=compiled_screen.to_public_screen(),
            compiled_screen=compiled_screen,
            artifacts=ScreenArtifacts(
                screen_json=(
                    session.artifact_root / "screens" / "obs-00043.json"
                ).as_posix()
            ),
        )

    return ActionCommandRepairer(
        snapshot_service=StaticSnapshotService(refreshed_snapshot),
        screen_refresh=CallbackScreenRefresh(callback=refresh_runtime),
    )


def write_current_poison_artifact(runtime) -> Path:
    screens_dir = runtime.artifact_root / "screens"
    screens_dir.mkdir(parents=True, exist_ok=True)
    path = screens_dir / "obs-00042.json"
    path.write_text(
        json.dumps(
            {
                "screenId": _CURRENT_SCREEN_ID,
                "sequence": 42,
                "sourceSnapshotId": 42,
                "capturedAt": "2026-04-27T00:00:00Z",
                "packageName": "com.android.settings",
                "activityName": "com.android.settings.Settings",
                "keyboardVisible": False,
                "groups": {
                    "targets": [],
                    "context": [],
                    "dialog": [],
                    "keyboard": [],
                    "system": [],
                },
                "repairBindings": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def assert_valid_repair_artifact(path: Path, *, source_screen_id: str) -> None:
    payload = ScreenArtifactPayload.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    assert payload.screen_id == source_screen_id
    assert _REPAIR_REF in payload.repair_bindings


def test_screen_artifact_payload_stores_profile_signals_without_identity(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    binding_payload = payload["repairBindings"][_REPAIR_REF]
    fingerprint_payload = binding_payload["fingerprint"]
    semantic_profile_payload = binding_payload["semanticProfile"]

    assert fingerprint_payload["role"] == "button"
    assert fingerprint_payload["normalizedLabel"] == "wi-fi"
    assert set(semantic_profile_payload) == {"state", "actions"}
    assert "role" not in semantic_profile_payload
    assert "label" not in semantic_profile_payload


def test_screen_artifact_payload_rejects_old_nested_semantic_profile_identity(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    semantic_profile_payload = payload["repairBindings"][_REPAIR_REF]["semanticProfile"]
    semantic_profile_payload["role"] = "button"
    semantic_profile_payload["label"] = "Wi-Fi"

    with pytest.raises(ValidationError) as error:
        ScreenArtifactPayload.model_validate_json(json.dumps(payload))
    validation_error = str(error.value)
    assert "role" in validation_error
    assert "label" in validation_error


def assert_no_repair_diagnostics(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in _REPAIR_DIAGNOSTIC_TOKENS
            assert_no_repair_diagnostics(item)
        return
    if isinstance(value, list):
        for item in value:
            assert_no_repair_diagnostics(item)
        return
    if isinstance(value, str):
        for token in _REPAIR_DIAGNOSTIC_TOKENS:
            assert token not in value


def test_build_action_request_current_source_uses_live_registry_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    expected_handle = bind_current_repair_ref(runtime)
    write_current_poison_artifact(runtime)

    def fail_load_source_artifact_binding_decision(*args, **kwargs):
        del args, kwargs
        raise AssertionError("current-source resolve must not load artifact bindings")

    monkeypatch.setattr(
        ref_repair_module,
        "load_source_artifact_binding_decision",
        fail_load_source_artifact_binding_decision,
    )

    def fail_repair_source_signature_to_current_snapshot(*args, **kwargs):
        del args, kwargs
        raise AssertionError("current-source direct resolve must not repair")

    monkeypatch.setattr(
        ref_repair_module,
        "repair_source_signature_to_current_snapshot",
        fail_repair_source_signature_to_current_snapshot,
    )

    request = build_action_request(
        runtime,
        TapCommand(ref=_REPAIR_REF, source_screen_id=_CURRENT_SCREEN_ID),
    )

    assert request.request_handle == expected_handle


def test_action_request_repair_current_source_uses_live_registry_without_artifact_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    old_handle = bind_current_repair_ref(runtime)
    write_current_poison_artifact(runtime)
    repairer = make_refreshed_repairer(runtime)

    def fail_load_source_artifact_binding_decision(*args, **kwargs):
        del args, kwargs
        raise AssertionError("current-source retry must not load artifact bindings")

    monkeypatch.setattr(
        ref_repair_module,
        "load_source_artifact_binding_decision",
        fail_load_source_artifact_binding_decision,
    )
    original_read_text = Path.read_text

    def fail_screen_artifact_read(self: Path, encoding: str = "utf-8") -> str:
        try:
            self.relative_to(runtime.artifact_root / "screens")
        except ValueError:
            return original_read_text(self, encoding=encoding)
        raise AssertionError("current-source retry must not read screen artifacts")

    monkeypatch.setattr(Path, "read_text", fail_screen_artifact_read)
    repaired_source_signatures = []
    original_repair_source_signature_to_current_snapshot = (
        ref_repair_module.repair_source_signature_to_current_snapshot
    )

    def spy_repair_source_signature_to_current_snapshot(
        source_signature, *, compiled_screen, snapshot_id
    ):
        repaired_source_signatures.append(source_signature)
        return original_repair_source_signature_to_current_snapshot(
            source_signature,
            compiled_screen=compiled_screen,
            snapshot_id=snapshot_id,
        )

    monkeypatch.setattr(
        ref_repair_module,
        "repair_source_signature_to_current_snapshot",
        spy_repair_source_signature_to_current_snapshot,
    )

    request = repairer.repair_action_command(
        runtime,
        make_command_record(),
        TapCommand(ref=_REPAIR_REF, source_screen_id=_CURRENT_SCREEN_ID),
        lifecycle_lease=capture_lifecycle_lease(runtime),
    )

    assert request.request_handle == NodeHandle(snapshot_id=43, rid="w1:0.9")
    assert request.request_handle != old_handle
    assert len(repaired_source_signatures) == 1
    assert repaired_source_signatures[0].ref == _REPAIR_REF
    assert not hasattr(repaired_source_signatures[0], "handle")
    assert not hasattr(repaired_source_signatures[0], "last_seen_screen_id")


def test_action_request_repair_current_source_missing_live_ref_fails_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    write_current_poison_artifact(runtime)
    repairer = make_refreshed_repairer(runtime)

    def fail_load_source_artifact_binding_decision(*args, **kwargs):
        del args, kwargs
        raise AssertionError("current-source retry must not load artifact bindings")

    monkeypatch.setattr(
        ref_repair_module,
        "load_source_artifact_binding_decision",
        fail_load_source_artifact_binding_decision,
    )

    with pytest.raises(DaemonError) as error:
        repairer.repair_action_command(
            runtime,
            make_command_record(),
            TapCommand(ref=_REPAIR_REF, source_screen_id=_CURRENT_SCREEN_ID),
            lifecycle_lease=capture_lifecycle_lease(runtime),
        )

    assert error.value.code == "REF_RESOLUTION_FAILED"
    assert error.value.details == {"ref": _REPAIR_REF}


def test_action_request_repair_stale_source_uses_artifact_signature_over_live_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    source_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    source_artifact_payload = ScreenArtifactPayload.model_validate_json(
        source_artifact_path.read_text(encoding="utf-8")
    )
    expected_binding_payload = source_artifact_payload.repair_bindings[_REPAIR_REF]
    conflicting_candidate = make_candidate(
        rid="w1:0.7",
        label="Bluetooth",
        resource_id="android:id/button2",
        relative_bounds=(200, 200, 260, 240),
    )
    conflicting_handle = NodeHandle(snapshot_id=42, rid="w1:0.7")
    runtime.ref_registry = RefRegistry(
        bindings={
            _REPAIR_REF: binding_for_candidate(
                ref=_REPAIR_REF,
                candidate=conflicting_candidate,
                snapshot_id=conflicting_handle.snapshot_id,
                reused=False,
            )
        }
    )
    repairer = make_refreshed_repairer(runtime)
    load_source_artifact_binding_decision_calls: list[tuple[object, str, str]] = []
    original_load_source_artifact_binding_decision = (
        ref_repair_module.load_source_artifact_binding_decision
    )

    def spy_load_source_artifact_binding_decision(session, ref, source_screen_id):
        load_source_artifact_binding_decision_calls.append(
            (session, source_screen_id, ref)
        )
        return original_load_source_artifact_binding_decision(
            session, ref, source_screen_id
        )

    source_signature_from_artifact_payload_calls: list[tuple[str, object]] = []
    original_source_signature_from_artifact_payload = (
        ref_repair_module.source_signature_from_artifact_payload
    )

    def spy_source_signature_from_artifact_payload(ref, payload):
        source_signature_from_artifact_payload_calls.append((ref, payload))
        source_signature = original_source_signature_from_artifact_payload(ref, payload)
        assert not hasattr(source_signature, "handle")
        assert not hasattr(source_signature, "last_seen_screen_id")
        return source_signature

    monkeypatch.setattr(
        ref_repair_module,
        "load_source_artifact_binding_decision",
        spy_load_source_artifact_binding_decision,
    )
    monkeypatch.setattr(
        ref_repair_module,
        "source_signature_from_artifact_payload",
        spy_source_signature_from_artifact_payload,
    )

    request = repairer.repair_action_command(
        runtime,
        make_command_record(),
        TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        lifecycle_lease=capture_lifecycle_lease(runtime),
    )

    assert request.request_handle == NodeHandle(snapshot_id=43, rid="w1:0.9")
    assert request.request_handle != conflicting_handle
    assert load_source_artifact_binding_decision_calls == [
        (runtime, _SOURCE_SCREEN_ID, _REPAIR_REF)
    ]
    assert source_signature_from_artifact_payload_calls == [
        (_REPAIR_REF, expected_binding_payload)
    ]


def test_load_source_artifact_binding_decision_resolves_after_lookup_delete_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    source_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    original_read_text = Path.read_text
    selected_reads = 0

    def unlink_after_selected_lookup_read(
        self: Path,
        encoding: str = "utf-8",
    ) -> str:
        nonlocal selected_reads
        content = original_read_text(self, encoding=encoding)
        if self == source_artifact_path:
            selected_reads += 1
            if selected_reads == 1:
                self.unlink()
        return content

    monkeypatch.setattr(Path, "read_text", unlink_after_selected_lookup_read)

    decision = ref_repair_module.load_source_artifact_binding_decision(
        runtime,
        _REPAIR_REF,
        _SOURCE_SCREEN_ID,
    )

    assert decision.is_resolved
    assert decision.binding is None
    assert decision.source_signature is not None
    assert decision.source_signature.ref == _REPAIR_REF
    assert not hasattr(decision.source_signature, "handle")
    assert not hasattr(decision.source_signature, "last_seen_screen_id")
    assert selected_reads == 1
    assert not source_artifact_path.exists()


def test_load_source_artifact_binding_decision_does_not_second_read_source_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    source_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    original_lookup_source_screen_artifact = (
        ref_repair_module.lookup_source_screen_artifact
    )
    original_read_text = Path.read_text

    def guarded_lookup_source_screen_artifact(session, source_screen_id):
        lookup = original_lookup_source_screen_artifact(session, source_screen_id)
        assert lookup.status == "found"
        assert lookup.path == source_artifact_path
        assert lookup.payload is not None

        def fail_selected_source_artifact_read(
            self: Path,
            encoding: str = "utf-8",
        ) -> str:
            if self == source_artifact_path:
                raise AssertionError("ref repair must not second-read source artifact")
            return original_read_text(self, encoding=encoding)

        monkeypatch.setattr(Path, "read_text", fail_selected_source_artifact_read)
        return lookup

    monkeypatch.setattr(
        ref_repair_module,
        "lookup_source_screen_artifact",
        guarded_lookup_source_screen_artifact,
    )

    decision = ref_repair_module.load_source_artifact_binding_decision(
        runtime,
        _REPAIR_REF,
        _SOURCE_SCREEN_ID,
    )

    assert decision.is_resolved
    assert decision.binding is None
    assert decision.source_signature is not None
    assert decision.source_signature.ref == _REPAIR_REF
    assert not hasattr(decision.source_signature, "handle")
    assert not hasattr(decision.source_signature, "last_seen_screen_id")


def test_action_request_repair_missing_source_artifact_uses_shared_diagnostics(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    repairer = make_refreshed_repairer(runtime)

    with pytest.raises(DaemonError) as error:
        repairer.repair_action_command(
            runtime,
            make_command_record(),
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
            lifecycle_lease=capture_lifecycle_lease(runtime),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.retryable is False
    assert error.value.details["sourceScreenId"] == _SOURCE_SCREEN_ID
    assert error.value.details["sourceArtifactStatus"] == "source_unavailable"
    assert "screen" in error.value.details
    assert "artifacts" in error.value.details


def test_public_action_result_omits_repair_diagnostics(tmp_path: Path) -> None:
    runtime = make_ready_runtime(tmp_path)

    class _RefStaleActionExecutor:
        def execute(self, runtime, record, command, lifecycle_lease):
            del runtime, record, command, lifecycle_lease
            raise ref_stale_error(
                _REPAIR_REF,
                source_screen_id=_SOURCE_SCREEN_ID,
                source_artifact_status="repair_failed",
            )

    handler = ActionCommandHandler(
        runtime_kernel=PassiveRuntimeKernel(runtime),
        action_executor=_RefStaleActionExecutor(),
    )

    payload = handler.handle_ref_action(
        command=TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
    )
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["ok"] is False
    assert payload["code"] == "REF_STALE"
    assert payload["message"] == _PUBLIC_REF_STALE_MESSAGE
    assert payload["payloadMode"] == "full"
    assert payload["truth"]["continuityStatus"] == "stale"
    assert set(payload["artifacts"]).issubset({"screenshotPng", "screenXml"})
    assert "/.androidctl/screens/" not in serialized
    assert_no_repair_diagnostics(payload)


def test_build_action_request_leaves_unrelated_invalid_artifact_during_repair(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    invalid_artifact_path = runtime.artifact_root / "screens" / "obs-00099.json"
    invalid_artifact_path.write_text(
        json.dumps(
            {
                "screenId": "screen-99999",
                "sequence": 99,
                "sourceSnapshotId": 99,
                "capturedAt": "2026-04-27T00:00:00Z",
                "packageName": "com.android.settings",
                "activityName": "com.android.settings.Settings",
                "keyboardVisible": False,
                "groups": {
                    "targets": [],
                    "context": [],
                    "dialog": [],
                    "keyboard": [],
                    "system": [],
                },
                "repairBindings": {},
                "debugOnly": {"score": 7},
            }
        ),
        encoding="utf-8",
    )

    request = build_action_request(
        runtime,
        TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
    )

    assert request.request_handle is not None
    assert invalid_artifact_path.exists()


def test_build_action_request_ignores_non_obs_sidecar_in_screens_dir(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    sidecar_path = runtime.artifact_root / "screens" / "latest.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text("{not json}", encoding="utf-8")
    runtime.screen_state.artifacts = ScreenArtifacts(
        screen_json=sidecar_path.as_posix()
    )

    request = build_action_request(
        runtime,
        TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
    )

    assert request.request_handle is not None
    assert sidecar_path.exists()


def test_build_action_request_fails_closed_when_newest_source_artifact_is_invalid(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    invalid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=42,
        extra_fields={"debugOnly": {"score": 7}},
    )

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "invalid_artifact"
    assert invalid_artifact_path.exists()


def test_build_action_request_repair_failed_status_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )

    def reject_repair(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(
        ref_repair_module,
        "repair_source_signature_to_current_snapshot",
        reject_repair,
    )

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.retryable is True
    assert error.value.details["sourceScreenId"] == _SOURCE_SCREEN_ID
    assert error.value.details["sourceArtifactStatus"] == "repair_failed"


def test_action_request_repair_repair_failed_status_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    repairer = make_refreshed_repairer(runtime)

    def reject_repair(*args, **kwargs):
        del args, kwargs
        return None

    monkeypatch.setattr(
        ref_repair_module,
        "repair_source_signature_to_current_snapshot",
        reject_repair,
    )

    with pytest.raises(DaemonError) as error:
        repairer.repair_action_command(
            runtime,
            make_command_record(),
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
            lifecycle_lease=capture_lifecycle_lease(runtime),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.retryable is True
    assert error.value.details["sourceScreenId"] == _SOURCE_SCREEN_ID
    assert error.value.details["sourceArtifactStatus"] == "repair_failed"


def test_build_action_request_leaves_unrelated_malformed_before_invalid_raise(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    invalid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=42,
        extra_fields={"debugOnly": {"score": 7}},
    )
    unrelated_malformed_path = runtime.artifact_root / "screens" / "obs-00099.json"
    unrelated_malformed_path.write_text(
        json.dumps(
            {
                "screenId": "screen-99999",
                "sequence": 99,
                "sourceSnapshotId": 99,
                "capturedAt": "2026-04-27T00:00:00Z",
                "packageName": "com.android.settings",
                "activityName": "com.android.settings.Settings",
                "keyboardVisible": False,
                "groups": {
                    "targets": [],
                    "context": [],
                    "dialog": [],
                    "keyboard": [],
                    "system": [],
                },
                "repairBindings": {},
                "debugOnly": {"score": 7},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "invalid_artifact"
    assert unrelated_malformed_path.exists()
    assert invalid_artifact_path.exists()


def test_build_action_request_source_unavailable_for_unrelated_non_json(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    corrupt_artifact_path = runtime.artifact_root / "screens" / "obs-00099.json"
    corrupt_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_artifact_path.write_text("{not json}", encoding="utf-8")

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "source_unavailable"
    assert corrupt_artifact_path.exists()


def test_build_action_request_source_unavailable_for_unrelated_malformed(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    malformed_artifact_path = runtime.artifact_root / "screens" / "obs-00099.json"
    malformed_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    malformed_artifact_path.write_text(
        json.dumps(
            {
                "screenId": "screen-99999",
                "sequence": 99,
                "sourceSnapshotId": 99,
                "capturedAt": "2026-04-27T00:00:00Z",
                "packageName": "com.android.settings",
                "activityName": "com.android.settings.Settings",
                "keyboardVisible": False,
                "groups": {
                    "targets": [],
                    "context": [],
                    "dialog": [],
                    "keyboard": [],
                    "system": [],
                },
                "repairBindings": {},
                "debugOnly": {"score": 7},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "source_unavailable"
    assert malformed_artifact_path.exists()


def test_build_action_request_source_unavailable_for_unreadable_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    unreadable_artifact_path = runtime.artifact_root / "screens" / "obs-00099.json"
    unreadable_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    unreadable_artifact_path.write_text("{}", encoding="utf-8")

    original_read_text = Path.read_text

    def unreadable_read_text(self: Path, encoding: str = "utf-8") -> str:
        if self == unreadable_artifact_path:
            raise PermissionError("denied")
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", unreadable_read_text)

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "source_unavailable"
    assert unreadable_artifact_path.exists()


def test_build_action_request_fails_closed_when_newest_source_artifact_is_non_json(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    invalid_artifact_path = runtime.artifact_root / "screens" / "obs-00042.json"
    invalid_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_artifact_path.write_text("{not json}", encoding="utf-8")

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "invalid_artifact"
    assert invalid_artifact_path.exists()


def test_build_action_request_fails_closed_when_newest_source_artifact_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = None
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    unreadable_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=42,
    )

    original_read_text = Path.read_text

    def unreadable_read_text(self: Path, encoding: str = "utf-8") -> str:
        if self == unreadable_artifact_path:
            raise PermissionError("denied")
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", unreadable_read_text)

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "invalid_artifact"
    assert unreadable_artifact_path.exists()


def test_build_action_request_fails_closed_when_current_screen_pointer_is_non_json(
    tmp_path: Path,
) -> None:
    runtime = make_ready_runtime(tmp_path)
    valid_artifact_path = write_repair_artifact(
        runtime,
        source_screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    assert_valid_repair_artifact(
        valid_artifact_path,
        source_screen_id=_SOURCE_SCREEN_ID,
    )
    invalid_artifact_path = runtime.artifact_root / "screens" / "obs-00042.json"
    invalid_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_artifact_path.write_text("{not json}", encoding="utf-8")
    assert runtime.screen_state is not None
    runtime.screen_state.artifacts = ScreenArtifacts(
        screen_json=invalid_artifact_path.as_posix()
    )

    with pytest.raises(DaemonError) as error:
        build_action_request(
            runtime,
            TapCommand(ref=_REPAIR_REF, source_screen_id=_SOURCE_SCREEN_ID),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.details["sourceArtifactStatus"] == "invalid_artifact"
    assert invalid_artifact_path.exists()


@pytest.mark.parametrize("status", ["source_unavailable", "invalid_artifact"])
def test_ref_stale_error_marks_source_input_status_non_retryable(status: str):
    error = ref_stale_error(
        "n1",
        source_screen_id="screen-00001",
        source_artifact_status=status,
    )
    assert not error.retryable
    assert error.details["sourceArtifactStatus"] == status
    assert "screen" in error.details
    assert "artifacts" in error.details


def test_ref_stale_error_keeps_repair_failed_retryable():
    error = ref_stale_error(
        "n1", source_screen_id="screen-00001", source_artifact_status="repair_failed"
    )
    assert error.retryable
    assert "screen" in error.details
    assert "artifacts" in error.details


def test_ref_stale_error_rejects_unknown_source_artifact_status():
    with pytest.raises(ValueError, match="unknown sourceArtifactStatus"):
        ref_stale_error(
            "n1",
            source_screen_id="screen-00001",
            source_artifact_status="unknown_status",
        )


def test_ref_binding_runtime_support_value_objects_do_not_expose_to_json():
    binding = binding_for_candidate(
        ref="n0",
        candidate=make_candidate(rid="w1:0.1"),
        snapshot_id=100,
        reused=False,
    )
    assert not hasattr(binding.handle, "to_json")
    assert not hasattr(binding.semantic_profile, "to_json")


def test_source_signature_scorer_matches_live_binding_wrapper():
    binding = binding_for_candidate(
        ref="n1",
        candidate=make_candidate(rid="w1:0.1"),
        snapshot_id=100,
        reused=False,
    )
    candidates = [
        make_candidate(rid="w1:0.9"),
        make_candidate(
            rid="w1:0.8",
            label="Bluetooth",
            resource_id="android:id/button2",
            relative_bounds=(10, 50, 50, 60),
        ),
    ]

    live_match = best_candidate_for_binding(binding, candidates)
    signature_match = best_candidate_for_source_signature(
        source_signature_from_binding(binding),
        candidates,
    )

    assert live_match is not None
    assert signature_match is not None
    live_candidate, live_confidence = live_match
    signature_candidate, signature_confidence = signature_match
    assert signature_candidate is live_candidate
    assert signature_confidence == live_confidence


def test_best_candidate_accepts_high_confidence_unique_match():
    binding = binding_for_candidate(
        ref="n1",
        candidate=make_candidate(rid="w1:0.1"),
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(rid="w1:0.9")
    other_candidate = make_candidate(
        rid="w1:0.8",
        label="Bluetooth",
        resource_id="android:id/button2",
        relative_bounds=(10, 50, 50, 60),
    )
    match = best_candidate_for_binding(binding, [repaired_candidate, other_candidate])
    assert match is not None
    candidate, confidence = match
    assert candidate.raw_rid == "w1:0.9"
    assert confidence.is_high_confidence


def test_best_candidate_accepts_contextual_high_confidence_match_without_identity():
    original = make_candidate(
        rid="w1:0.1",
        label="",
        state=("selected",),
        actions=("tap", "focus"),
        resource_id=None,
        class_name="android.widget.ImageButton",
        parent_role="toolbar",
        parent_label="Search",
        sibling_labels=("Back", "More"),
        relative_bounds=(70, 0, 90, 20),
    )
    binding = binding_for_candidate(
        ref="n2",
        candidate=original,
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(
        rid="w1:0.5",
        label="",
        state=("selected",),
        actions=("tap", "focus"),
        resource_id=None,
        class_name="android.widget.ImageButton",
        parent_role="toolbar",
        parent_label="Search",
        sibling_labels=("Back", "More"),
        relative_bounds=(70, 0, 90, 20),
    )
    match = best_candidate_for_binding(binding, [repaired_candidate])
    assert match is not None
    candidate, confidence = match
    assert candidate.raw_rid == "w1:0.5"
    assert confidence.is_high_confidence


def test_best_candidate_rejects_unique_low_confidence_class_parent_only_match():
    original = make_candidate(
        rid="w1:0.1",
        label="",
        state=(),
        actions=(),
        resource_id=None,
        class_name="android.widget.ImageButton",
        parent_role="toolbar",
        parent_label="Search",
        sibling_labels=(),
        relative_bounds=(70, 0, 90, 20),
    )
    binding = binding_for_candidate(
        ref="n3",
        candidate=original,
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(
        rid="w1:0.7",
        label="",
        state=(),
        actions=(),
        resource_id=None,
        class_name="android.widget.ImageButton",
        parent_role="toolbar",
        parent_label="Search",
        sibling_labels=(),
        relative_bounds=(10, 40, 30, 60),
    )
    match = best_candidate_for_binding(binding, [repaired_candidate])
    assert match is None


def test_best_candidate_rejects_close_runner_up_candidates():
    binding = binding_for_candidate(
        ref="n4",
        candidate=make_candidate(rid="w1:0.1"),
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(rid="w1:0.2")
    close_runner_up = make_candidate(rid="w1:0.3", relative_bounds=(12, 20, 52, 30))
    match = best_candidate_for_binding(binding, [repaired_candidate, close_runner_up])
    assert match is None


def test_best_candidate_accepts_state_change_when_identity_stays_stable():
    original = make_candidate(
        rid="w1:0.1",
        role="switch",
        label="Wi-Fi",
        state=("unchecked",),
        actions=("tap",),
        resource_id="android:id/switch_widget",
        class_name="android.widget.Switch",
        parent_role="container",
        parent_label="Network",
        sibling_labels=("Bluetooth",),
    )
    binding = binding_for_candidate(
        ref="n5",
        candidate=original,
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(
        rid="w1:0.9",
        role="switch",
        label="Wi-Fi",
        state=("checked",),
        actions=("tap",),
        resource_id="android:id/switch_widget",
        class_name="android.widget.Switch",
        parent_role="container",
        parent_label="Network",
        sibling_labels=("Bluetooth",),
    )
    other_candidate = make_candidate(
        rid="w1:1.0",
        role="switch",
        label="Bluetooth",
        state=("checked",),
        actions=("tap",),
        resource_id="android:id/switch_widget_bluetooth",
        class_name="android.widget.Switch",
        parent_role="container",
        parent_label="Network",
        sibling_labels=("Wi-Fi",),
        relative_bounds=(10, 50, 50, 60),
    )
    match = best_candidate_for_binding(binding, [repaired_candidate, other_candidate])
    assert match is not None
    candidate, confidence = match
    assert candidate.raw_rid == "w1:0.9"
    assert confidence.is_high_confidence


def test_repair_rejects_role_mismatch_even_with_matching_identity():
    binding = binding_for_candidate(
        ref="n6",
        candidate=make_candidate(
            rid="w1:0.1", role="button", label="Wi-Fi", resource_id="android:id/button1"
        ),
        snapshot_id=100,
        reused=False,
    )
    repaired_candidate = make_candidate(
        rid="w1:0.9",
        role="switch",
        label="Wi-Fi",
        resource_id="android:id/button1",
        class_name="android.widget.Switch",
    )
    match = best_candidate_for_binding(binding, [repaired_candidate])
    assert match is None
