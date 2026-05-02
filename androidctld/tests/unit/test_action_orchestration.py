from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path

import pytest

from androidctld.actions import postconditions as postconditions_module
from androidctld.actions.executor import ActionExecutionFailure, ActionExecutor
from androidctld.actions.postconditions import (
    PostconditionOutcome,
    RefActionPostconditionContext,
)
from androidctld.actions.request_builder import (
    build_action_request,
    build_action_request_for_binding,
)
from androidctld.actions.settle import SettledSnapshot
from androidctld.actions.type_confirmation import TypeConfirmationCandidate
from androidctld.artifacts.screen_payloads import build_screen_artifact_payload
from androidctld.commands.command_models import (
    FocusCommand,
    GlobalCommand,
    LongTapCommand,
    OpenCommand,
    RefBoundActionCommand,
    ScrollCommand,
    SubmitCommand,
    TapCommand,
    TypeCommand,
)
from androidctld.commands.models import CommandRecord, CommandStatus
from androidctld.commands.open_targets import OpenUrlTarget
from androidctld.device.action_models import (
    BuiltDeviceActionRequest,
    HandleTarget,
    LongTapActionRequest,
    NodeActionRequest,
    ScrollActionRequest,
    TapActionRequest,
    TypeActionRequest,
)
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    ConnectionSpec,
    DeviceCapabilities,
    DeviceEndpoint,
    ResolvedHandleTarget,
    ResolvedNoneTarget,
    RuntimeTransport,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import (
    CommandKind,
    ConnectionMode,
    DeviceRpcErrorCode,
    RuntimeStatus,
)
from androidctld.refs.models import NodeHandle, RefRegistry
from androidctld.refs.service import RefRegistryBuilder, binding_for_candidate
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_compiled_screen,
    get_authoritative_current_basis,
)
from androidctld.runtime_policy import action_timeout_ms
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.semantics.public_models import PublicNode, TransientItem
from androidctld.snapshots.models import RawSnapshot, RawWindow
from androidctld.snapshots.refresh import ScreenRefreshService, settle_screen_signature

from ..support.runtime_store import runtime_store_for_workspace
from .support.doubles import StaticScreenRefresh
from .support.runtime import (
    build_runtime,
    build_screen_artifacts,
    install_screen_state,
)
from .support.semantic_screen import (
    make_compiled_screen,
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
    make_semantic_node,
)
from .support.semantic_screen import (
    make_raw_node as _make_raw_node,
)


def test_progress_lane_conflict_uses_runtime_busy(tmp_path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    kernel.acquire_progress_lane(runtime, occupant_kind="tracked")
    try:
        with pytest.raises(DaemonError) as error:
            kernel.acquire_progress_lane(
                runtime,
                occupant_kind="untracked",
            )
        assert error.value.code == "RUNTIME_BUSY"
    finally:
        kernel.release_progress_lane(runtime)


def test_action_executor_uses_runtime_not_connected_code(tmp_path) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    executor = ActionExecutor(
        device_client_factory=lambda runtime, *, lifecycle_lease=None: None,
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.OPEN,
        status=CommandStatus.RUNNING,
        started_at="2026-04-07T00:00:00Z",
    )
    command = OpenCommand(
        target=OpenUrlTarget(url="https://example.com"),
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "RUNTIME_NOT_CONNECTED"


def test_action_executor_maps_open_action_failed_rpc_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="open"),
    )

    class FailingOpenClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            raise DaemonError(
                code="DEVICE_RPC_FAILED",
                message="open rejected",
                retryable=False,
                details={"deviceCode": DeviceRpcErrorCode.ACTION_FAILED.value},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            FailingOpenClient()
        ),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00002",
        kind=CommandKind.OPEN,
        status=CommandStatus.RUNNING,
        started_at="2026-04-13T00:00:00Z",
    )
    command = OpenCommand(target=OpenUrlTarget(url="https://example.com"))

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "OPEN_FAILED"
    assert error.value.details["deviceCode"] == DeviceRpcErrorCode.ACTION_FAILED.value


def test_action_executor_rebootstraps_no_transport_before_capability_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = build_runtime(
        tmp_path,
        status=RuntimeStatus.CONNECTED,
        connection=ConnectionSpec(
            mode=ConnectionMode.LAN,
            host="127.0.0.1",
            port=17171,
        ),
        device_token="device-token",
    )
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["tap"],
    )
    refreshed_snapshot = make_snapshot(snapshot_id=43)
    calls: list[str] = []
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            calls.append("dispatch")
            return ActionPerformResult(
                action_id="act-open",
                status=ActionStatus.DONE,
                resolved_target=ResolvedNoneTarget(),
            )

    def rebootstrap_factory(session, *, lifecycle_lease=None):
        del lifecycle_lease
        calls.append("factory")
        assert session.transport is None
        session.transport = RuntimeTransport(
            endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
            close=lambda: None,
        )
        session.device_capabilities = DeviceCapabilities(
            supports_events_poll=True,
            supports_screenshot=True,
            action_kinds=["openUrl"],
        )
        return RecordingClient()

    executor = ActionExecutor(
        device_client_factory=rebootstrap_factory,
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_REFRESHED_SCREEN_ID,
                sequence=43,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )

    result = executor.execute(
        runtime,
        CommandRecord(
            command_id="cmd-open-rebootstrap",
            kind=CommandKind.OPEN,
            status=CommandStatus.RUNNING,
            started_at="2026-05-07T00:00:00Z",
        ),
        OpenCommand(target=OpenUrlTarget(url="https://example.com")),
        runtime_store_lease(runtime),
    )

    assert result.app_payload is not None
    assert calls == ["factory", "dispatch"]


def runtime_store_lease(runtime):
    from androidctld.runtime import capture_lifecycle_lease

    return capture_lifecycle_lease(runtime)


make_raw_node = partial(
    _make_raw_node,
    text="Search settings",
    actions=("focus", "setText", "click"),
)

make_snapshot = make_contract_snapshot

_FOCUS_REF = "n1"
_REQUEST_HANDLE = NodeHandle(snapshot_id=42, rid="w1:0.5")
_REFRESHED_HANDLE = NodeHandle(snapshot_id=43, rid="w1:0.5")
_PREVIOUS_FOCUS_SCREEN_ID = "screen-00041"
_FOCUS_SCREEN_ID = "screen-00042"
_REFRESHED_SCREEN_ID = "screen-00043"
_FOCUS_SEQUENCE = 42
_DEFAULT_FOCUS_TARGETS = (
    make_public_node(
        ref=_FOCUS_REF,
        role="input",
        label="Search settings",
        state=(),
        actions=("tap", "type", "focus", "submit"),
    ),
)


def make_device_request(*, action: str) -> BuiltDeviceActionRequest:
    return BuiltDeviceActionRequest(
        payload=NodeActionRequest(
            target=HandleTarget(_REQUEST_HANDLE),
            action=action,
            timeout_ms=1000,
        ),
        request_handle=_REQUEST_HANDLE,
    )


def make_focus_runtime(tmp_path, *, previous_focused: bool):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_FOCUS_SEQUENCE,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    snapshot = make_snapshot(make_raw_node(rid="w1:0.5", focused=previous_focused))
    compiled_screen = SemanticCompiler().compile(_FOCUS_SEQUENCE, snapshot)
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_contract_screen(
            screen_id=_FOCUS_SCREEN_ID,
            sequence=_FOCUS_SEQUENCE,
            targets=_DEFAULT_FOCUS_TARGETS,
            input_ref=_FOCUS_REF,
            keyboard_visible=False,
        ),
        compiled_screen=compiled_screen,
        artifacts=build_screen_artifacts(
            runtime,
            screen_id=_FOCUS_SCREEN_ID,
        ),
    )
    return runtime


def attach_runtime_transport(runtime: WorkspaceRuntime) -> None:
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )


def finalize_runtime_refs(runtime) -> None:
    assert runtime.screen_state is not None
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    latest_snapshot = runtime.latest_snapshot
    assert latest_snapshot is not None
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled_screen,
        snapshot_id=latest_snapshot.snapshot_id,
        previous_registry=None,
    )
    runtime.ref_registry = finalized.registry
    install_screen_state(
        runtime,
        public_screen=finalized.compiled_screen.to_public_screen(),
        compiled_screen=finalized.compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )


def make_current_submit_runtime(
    tmp_path,
    *,
    raw_actions: tuple[str, ...],
):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_FOCUS_SEQUENCE,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            focused=True,
            actions=raw_actions,
        ),
        snapshot_id=_FOCUS_SEQUENCE,
    )
    compiled_screen = SemanticCompiler().compile(_FOCUS_SEQUENCE, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled_screen,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    public_screen = finalized.compiled_screen.to_public_screen()
    runtime.ref_registry = finalized.registry
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=finalized.compiled_screen,
        artifacts=build_screen_artifacts(
            runtime,
            screen_id=public_screen.screen_id,
        ),
    )
    assert public_screen.surface.focus.input_ref == _FOCUS_REF
    return runtime, _FOCUS_REF, public_screen.screen_id


def make_scroll_runtime(
    tmp_path,
    *,
    raw_actions: tuple[str, ...] = ("scrollForward",),
):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_FOCUS_SEQUENCE,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    snapshot = make_snapshot(
        _make_raw_node(
            rid="w1:0.5",
            class_name="androidx.recyclerview.widget.RecyclerView",
            text="Results",
            editable=False,
            focusable=False,
            scrollable=True,
            actions=raw_actions,
        ),
        snapshot_id=_FOCUS_SEQUENCE,
    )
    compiled_screen = SemanticCompiler().compile(_FOCUS_SEQUENCE, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled_screen,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    public_screen = finalized.compiled_screen.to_public_screen()
    runtime.ref_registry = finalized.registry
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=finalized.compiled_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=public_screen.screen_id),
    )
    target = public_screen.groups[0].nodes[0]
    assert target.ref is not None
    binding = runtime.ref_registry.get(target.ref)
    assert binding is not None
    return runtime, target.ref, public_screen.screen_id, binding.handle


def make_long_tap_runtime(tmp_path):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_FOCUS_SEQUENCE,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    snapshot = make_snapshot(
        _make_raw_node(
            rid="w1:0.5",
            class_name="android.widget.TextView",
            text="Row",
            editable=False,
            focusable=False,
            actions=("longClick",),
        ),
        snapshot_id=_FOCUS_SEQUENCE,
    )
    compiled_screen = SemanticCompiler().compile(_FOCUS_SEQUENCE, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled_screen,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    public_screen = finalized.compiled_screen.to_public_screen()
    runtime.ref_registry = finalized.registry
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=finalized.compiled_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=public_screen.screen_id),
    )
    target = public_screen.groups[0].nodes[0]
    assert target.ref is not None
    assert "longTap" in target.actions
    binding = runtime.ref_registry.get(target.ref)
    assert binding is not None
    return runtime, target.ref, public_screen.screen_id, binding.handle


def make_attributed_submit_snapshot(
    *,
    snapshot_id: int = _FOCUS_SEQUENCE,
    input_actions: tuple[str, ...] = ("focus", "setText"),
    button_actions: tuple[str, ...] = ("click",),
    focused: bool = True,
):
    return make_snapshot(
        _make_raw_node(
            rid="w1:root",
            class_name="com.android.internal.policy.DecorView",
            text=None,
            editable=False,
            focusable=False,
            visible_to_user=False,
            important_for_accessibility=False,
            actions=(),
            child_rids=("w1:form",),
            bounds=(0, 0, 1080, 2400),
        ),
        _make_raw_node(
            rid="w1:form",
            parent_rid="w1:root",
            child_rids=("w1:0.5", "w1:submit"),
            class_name="android.widget.LinearLayout",
            resource_id="com.example:id/search_form",
            text=None,
            editable=False,
            focusable=False,
            important_for_accessibility=False,
            actions=(),
            bounds=(0, 0, 800, 400),
        ),
        replace(
            make_raw_node(
                rid="w1:0.5",
                focused=focused,
                actions=input_actions,
                bounds=(0, 0, 400, 80),
            ),
            parent_rid="w1:form",
        ),
        _make_raw_node(
            rid="w1:submit",
            parent_rid="w1:form",
            class_name="android.widget.Button",
            text="Search",
            editable=False,
            focusable=False,
            actions=button_actions,
            bounds=(420, 0, 560, 80),
        ),
        snapshot_id=snapshot_id,
    )


def make_attributed_submit_runtime(
    tmp_path,
    *,
    input_actions: tuple[str, ...] = ("focus", "setText"),
    button_actions: tuple[str, ...] = ("click",),
    focused: bool = True,
):
    runtime = build_runtime(tmp_path, screen_sequence=_FOCUS_SEQUENCE)
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    snapshot = make_attributed_submit_snapshot(
        snapshot_id=_FOCUS_SEQUENCE,
        input_actions=input_actions,
        button_actions=button_actions,
        focused=focused,
    )
    compiled_screen = SemanticCompiler().compile(_FOCUS_SEQUENCE, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled_screen,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    public_screen = finalized.compiled_screen.to_public_screen()
    runtime.ref_registry = finalized.registry
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=finalized.compiled_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=public_screen.screen_id),
    )
    input_ref = public_screen.surface.focus.input_ref
    assert input_ref is not None
    input_node = public_node_by_ref(public_screen, input_ref)
    assert input_node is not None
    assert input_node.submit_refs
    return runtime, input_ref, input_node.submit_refs[0], public_screen.screen_id


def public_node_by_ref(public_screen, ref: str):
    for group in public_screen.groups:
        for node in group.nodes:
            if node.ref == ref:
                return node
    return None


def ref_postcondition_context(public_screen, ref: str):
    return RefActionPostconditionContext(
        target_ref=ref,
        baseline_screen=public_screen,
        baseline_target=(
            None if public_screen is None else public_node_by_ref(public_screen, ref)
        ),
    )


def install_public_screen_with_target(runtime, target):
    assert runtime.screen_state is not None
    public_screen = runtime.screen_state.public_screen
    targets_group = public_screen.groups[0]
    targets = tuple(
        target if node.ref == target.ref else node for node in targets_group.nodes
    )
    groups = (
        targets_group.model_copy(update={"nodes": targets}),
        *public_screen.groups[1:],
    )
    install_screen_state(
        runtime,
        public_screen=public_screen.model_copy(update={"groups": groups}),
        compiled_screen=runtime.screen_state.compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )


def public_screen_with_target(public_screen, target):
    targets_group = public_screen.groups[0]
    targets = tuple(
        target if node.ref == target.ref else node for node in targets_group.nodes
    )
    groups = (
        targets_group.model_copy(update={"nodes": targets}),
        *public_screen.groups[1:],
    )
    return public_screen.model_copy(update={"groups": groups})


def public_screen_with_group(public_screen, group_name: str, nodes):
    groups = tuple(
        (
            group.model_copy(update={"nodes": tuple(nodes)})
            if group.name == group_name
            else group
        )
        for group in public_screen.groups
    )
    return public_screen.model_copy(update={"groups": groups})


def install_keyboard_blocking_surface(runtime) -> None:
    assert runtime.screen_state is not None
    public_screen = runtime.screen_state.public_screen
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    compiled_screen.keyboard_visible = True
    compiled_screen.blocking_group = "keyboard"
    install_screen_state(
        runtime,
        public_screen=public_screen.model_copy(
            update={
                "surface": public_screen.surface.model_copy(
                    update={
                        "keyboard_visible": True,
                        "blocking_group": "keyboard",
                    }
                )
            }
        ),
        compiled_screen=compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )


@dataclass
class FakeClient:
    result: ActionPerformResult

    def action_perform(self, payload, *, request_id: str):
        del payload, request_id
        return self.result


@dataclass
class FakeSettler:
    snapshot: RawSnapshot
    timed_out: bool = False

    def settle(self, session, client, kind, baseline_signature, **kwargs):
        del session, client, kind, baseline_signature, kwargs
        return SettledSnapshot(
            snapshot=self.snapshot,
            timed_out=self.timed_out,
        )


@dataclass
class FakeRepairer:
    request: BuiltDeviceActionRequest | None = None
    calls: list[str] = field(default_factory=list)

    def repair_action_command(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> BuiltDeviceActionRequest:
        handle = self.repair_action_binding(
            session,
            record,
            command,
            lifecycle_lease=lifecycle_lease,
        )
        if self.request is not None:
            return self.request
        return build_action_request_for_binding(
            handle,
            command,
        )

    def repair_action_binding(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> NodeHandle:
        del session, record, lifecycle_lease
        self.calls.append(command.kind.value)
        if self.request is not None:
            return self.request.request_handle
        return _REQUEST_HANDLE


class StrictRepairer:
    def repair_action_command(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> BuiltDeviceActionRequest:
        del session, record, command, lifecycle_lease
        raise AssertionError("non-ref commands must not call ref repair")

    def repair_action_binding(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> NodeHandle:
        del session, record, command, lifecycle_lease
        raise AssertionError("current submit commands must not call ref repair")


def write_repair_artifact(runtime, *, source_screen_id: str, sequence: int) -> Path:
    assert runtime.screen_state is not None
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    candidate = compiled_screen.ref_candidates()[0]
    source_screen = compiled_screen.to_public_screen().model_copy(
        update={
            "screen_id": source_screen_id,
            "sequence": sequence,
            "source_snapshot_id": sequence,
            "captured_at": "2026-04-08T00:00:00Z",
        }
    )
    source_registry = RefRegistry(
        bindings={
            _FOCUS_REF: binding_for_candidate(
                ref=_FOCUS_REF,
                candidate=candidate,
                snapshot_id=sequence,
                reused=False,
            )
        }
    )
    screens_dir = runtime.artifact_root / "screens"
    screens_dir.mkdir(parents=True, exist_ok=True)
    path = screens_dir / f"obs-{sequence:05d}.json"
    path.write_text(
        json.dumps(
            build_screen_artifact_payload(
                source_screen,
                source_registry,
                sequence=sequence,
                source_snapshot_id=sequence,
                captured_at="2026-04-08T00:00:00Z",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_action_executor_type_requires_matching_focused_input_before_dispatch(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    assert runtime.screen_state is not None
    install_screen_state(
        runtime,
        public_screen=make_contract_screen(
            screen_id=_FOCUS_SCREEN_ID,
            sequence=_FOCUS_SEQUENCE,
            targets=_DEFAULT_FOCUS_TARGETS,
            input_ref=None,
            keyboard_visible=False,
        ),
        compiled_screen=runtime.screen_state.compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            raise AssertionError("type should not dispatch without matching focus")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.TYPE,
        status=CommandStatus.RUNNING,
        started_at="2026-04-13T00:00:00Z",
    )
    command = TypeCommand(
        ref=_FOCUS_REF,
        source_screen_id=_FOCUS_SCREEN_ID,
        text="wifi",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "focus_mismatch"


def test_action_executor_scroll_direction_mismatch_rejects_before_dispatch(
    tmp_path,
) -> None:
    runtime, ref, screen_id, _handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    target = public_node_by_ref(runtime.screen_state.public_screen, ref)
    assert target is not None
    assert target.scroll_directions == ("down",)
    device_calls = []

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            device_calls.append("dispatch")
            raise AssertionError("scroll direction mismatch should not dispatch")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            CommandRecord(
                command_id="cmd-scroll-001",
                kind=CommandKind.SCROLL,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            ScrollCommand(ref=ref, source_screen_id=screen_id, direction="up"),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "scroll_direction_not_exposed"
    assert error.value.details["direction"] == "up"
    assert error.value.details["scrollDirections"] == ["down"]
    assert device_calls == []


def test_action_executor_repaired_scroll_direction_mismatch_rejects_before_dispatch(
    tmp_path,
) -> None:
    runtime, ref, _screen_id, handle = make_scroll_runtime(tmp_path)
    repairer = FakeRepairer(
        request=BuiltDeviceActionRequest(
            payload=ScrollActionRequest(
                target=HandleTarget(handle),
                direction="up",
                timeout_ms=1000,
            ),
            request_handle=handle,
        )
    )
    device_calls = []

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            device_calls.append("dispatch")
            raise AssertionError("repaired scroll mismatch should not dispatch")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=repairer,
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            CommandRecord(
                command_id="cmd-scroll-002",
                kind=CommandKind.SCROLL,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            ScrollCommand(
                ref=ref,
                source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
                direction="up",
            ),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "scroll_direction_not_exposed"
    assert error.value.details["direction"] == "up"
    assert error.value.details["scrollDirections"] == ["down"]
    assert repairer.calls == ["scroll"]
    assert device_calls == []


def test_action_executor_scroll_down_dispatches_and_confirms_changed_target_content(
    tmp_path,
) -> None:
    runtime, ref, screen_id, _handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    previous_screen = runtime.screen_state.public_screen
    target = public_node_by_ref(previous_screen, ref)
    assert target is not None
    refreshed_target = target.model_copy(
        update={"children": (PublicNode(kind="text", text="Second row"),)}
    )
    refreshed_screen = public_screen_with_target(previous_screen, refreshed_target)
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-scroll", status=ActionStatus.DONE)

    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=refreshed_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=43)),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-scroll-003",
            kind=CommandKind.SCROLL,
            status=CommandStatus.RUNNING,
            started_at="2026-05-07T00:00:00Z",
        ),
        ScrollCommand(ref=ref, source_screen_id=screen_id, direction="down"),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], ScrollActionRequest)
    assert device_calls[0].direction == "down"


def test_action_executor_scroll_dispatch_fails_closed_when_target_content_unchanged(
    tmp_path,
) -> None:
    runtime, ref, screen_id, _handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-scroll", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=99)),
        repairer=StrictRepairer(),
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            CommandRecord(
                command_id="cmd-scroll-004",
                kind=CommandKind.SCROLL,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            ScrollCommand(ref=ref, source_screen_id=screen_id, direction="down"),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details["reason"] == (
        "scroll_target_content_unchanged"
    )
    assert failure.value.dispatch_attempted is True
    assert len(device_calls) == 1


def test_action_executor_repaired_scroll_confirms_resolved_target_content_change(
    tmp_path,
) -> None:
    runtime, current_ref, _screen_id, handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    target = public_node_by_ref(runtime.screen_state.public_screen, current_ref)
    assert target is not None
    baseline_target = target.model_copy(
        update={"children": (PublicNode(kind="text", text="First row"),)}
    )
    baseline_screen = public_screen_with_target(
        runtime.screen_state.public_screen,
        baseline_target,
    )
    install_screen_state(
        runtime,
        public_screen=baseline_screen,
        compiled_screen=runtime.screen_state.compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )
    refreshed_target = baseline_target.model_copy(
        update={"children": (PublicNode(kind="text", text="Second row"),)}
    )
    refreshed_screen = public_screen_with_target(baseline_screen, refreshed_target)
    command = ScrollCommand(
        ref="source-ref",
        source_screen_id="screen-source",
        direction="down",
    )
    repairer = FakeRepairer(request=build_action_request_for_binding(handle, command))
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-scroll", status=ActionStatus.DONE)

    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=refreshed_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=99)),
        repairer=repairer,
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-scroll-005",
            kind=CommandKind.SCROLL,
            status=CommandStatus.RUNNING,
            started_at="2026-05-07T00:00:00Z",
        ),
        command,
        runtime_store_lease(runtime),
    )

    assert repairer.calls == ["scroll"]
    assert len(device_calls) == 1
    assert isinstance(device_calls[0], ScrollActionRequest)
    assert device_calls[0].target == HandleTarget(handle)


def test_action_executor_repaired_scroll_failure_reports_source_and_target_refs(
    tmp_path,
) -> None:
    runtime, current_ref, _screen_id, handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    command = ScrollCommand(
        ref="source-ref",
        source_screen_id="screen-source",
        direction="down",
    )
    repairer = FakeRepairer(request=build_action_request_for_binding(handle, command))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-scroll", status=ActionStatus.DONE)

    with pytest.raises(ActionExecutionFailure) as failure:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                RecordingClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=runtime.screen_state.public_screen,
                artifacts=runtime.screen_state.artifacts,
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=99)),
            repairer=repairer,
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-scroll-006",
                kind=CommandKind.SCROLL,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            command,
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details == {
        "reason": "scroll_target_content_unchanged",
        "ref": "source-ref",
        "direction": "down",
        "targetRef": current_ref,
    }


def test_action_executor_scroll_stale_target_retry_uses_retry_baseline(
    tmp_path,
) -> None:
    runtime, ref, screen_id, handle = make_scroll_runtime(tmp_path)
    assert runtime.screen_state is not None
    target = public_node_by_ref(runtime.screen_state.public_screen, ref)
    assert target is not None
    initial_target = target.model_copy(
        update={"children": (PublicNode(kind="text", text="Before retry"),)}
    )
    initial_screen = public_screen_with_target(
        runtime.screen_state.public_screen,
        initial_target,
    )
    retry_target = initial_target.model_copy(
        update={"children": (PublicNode(kind="text", text="Repair baseline"),)}
    )
    retry_screen = public_screen_with_target(initial_screen, retry_target)
    install_screen_state(
        runtime,
        public_screen=initial_screen,
        compiled_screen=runtime.screen_state.compiled_screen,
        artifacts=runtime.screen_state.artifacts,
    )
    device_calls = []

    class RetryRepairer(StrictRepairer):
        def repair_action_command(
            self,
            session,
            record,
            command,
            *,
            lifecycle_lease,
        ):
            del record, lifecycle_lease
            install_screen_state(
                session,
                public_screen=retry_screen,
                compiled_screen=session.screen_state.compiled_screen,
                artifacts=session.screen_state.artifacts,
            )
            return build_action_request_for_binding(handle, command)

    class StaleThenDoneClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            if len(device_calls) == 1:
                raise DaemonError(
                    code="DEVICE_RPC_FAILED",
                    message="stale target",
                    retryable=True,
                    details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                    http_status=200,
                )
            return ActionPerformResult(action_id="act-scroll", status=ActionStatus.DONE)

    with pytest.raises(ActionExecutionFailure) as failure:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                StaleThenDoneClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=retry_screen,
                artifacts=runtime.screen_state.artifacts,
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=100)),
            repairer=RetryRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-scroll-007",
                kind=CommandKind.SCROLL,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            ScrollCommand(ref=ref, source_screen_id=screen_id, direction="down"),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details["reason"] == (
        "scroll_target_content_unchanged"
    )
    assert len(device_calls) == 2


@pytest.mark.parametrize(
    "direction,previous_directions,current_directions",
    [
        ("down", ("down",), ("up", "backward")),
        ("up", ("up", "backward"), ("down",)),
    ],
)
def test_scroll_postcondition_confirms_direction_boundary_change(
    direction: str,
    previous_directions: tuple[str, ...],
    current_directions: tuple[str, ...],
) -> None:
    child = PublicNode(kind="text", text="Only visible row")
    previous_target = PublicNode(
        kind="container",
        role="scroll-container",
        label="Results",
        ref="n1",
        actions=("scroll",),
        scroll_directions=previous_directions,
        children=(child,),
    )
    current_target = previous_target.model_copy(
        update={"scroll_directions": current_directions}
    )
    previous_screen = make_contract_screen(
        targets=(previous_target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(current_target,),
        screen_id="screen-after",
    )

    postconditions_module.validate_postcondition(
        ScrollCommand(ref="n1", source_screen_id="screen-before", direction=direction),
        previous_snapshot=make_snapshot(snapshot_id=1),
        snapshot=make_snapshot(snapshot_id=2),
        previous_screen=previous_screen,
        public_screen=current_screen,
        session=object(),
        focus_context=None,
        action_result=ActionPerformResult(
            action_id="act-scroll",
            status=ActionStatus.DONE,
        ),
        ref_context=ref_postcondition_context(previous_screen, "n1"),
    )


def test_scroll_postcondition_supports_source_ref_fallback_context() -> None:
    previous_target = PublicNode(
        kind="container",
        role="scroll-container",
        label="Results",
        ref="n1",
        actions=("scroll",),
        scroll_directions=("down",),
        children=(PublicNode(kind="text", text="First row"),),
    )
    current_target = previous_target.model_copy(
        update={"children": (PublicNode(kind="text", text="Second row"),)}
    )
    previous_screen = make_contract_screen(
        targets=(previous_target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(current_target,),
        screen_id="screen-after",
    )

    postconditions_module.validate_postcondition(
        ScrollCommand(ref="n1", source_screen_id="screen-before", direction="down"),
        previous_snapshot=make_snapshot(snapshot_id=1),
        snapshot=make_snapshot(snapshot_id=2),
        previous_screen=previous_screen,
        public_screen=current_screen,
        session=object(),
        focus_context=None,
        action_result=ActionPerformResult(
            action_id="act-scroll",
            status=ActionStatus.DONE,
        ),
    )


def test_scroll_postcondition_rejects_missing_resolved_target_after_refresh() -> None:
    target = PublicNode(
        kind="container",
        role="scroll-container",
        label="Results",
        ref="n1",
        actions=("scroll",),
        scroll_directions=("down",),
        children=(PublicNode(kind="text", text="First row"),),
    )
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(targets=(), screen_id="screen-after")

    with pytest.raises(DaemonError) as error:
        postconditions_module.validate_postcondition(
            ScrollCommand(ref="n1", source_screen_id="screen-before", direction="down"),
            previous_snapshot=make_snapshot(snapshot_id=1),
            snapshot=make_snapshot(snapshot_id=2),
            previous_screen=previous_screen,
            public_screen=current_screen,
            session=object(),
            focus_context=None,
            action_result=ActionPerformResult(
                action_id="act-scroll",
                status=ActionStatus.DONE,
            ),
            ref_context=ref_postcondition_context(previous_screen, "n1"),
        )

    assert error.value.code == "ACTION_NOT_CONFIRMED"


@pytest.mark.parametrize(
    "current_screen_updates,target_updates",
    [
        ({"package_name": "com.example.other", "activity_name": "OtherActivity"}, {}),
        ({"keyboard_visible": True}, {}),
        ({"dialog": (make_public_node(ref="n9", label="Unrelated"),)}, {}),
        ({"screen_id": "screen-raw-only"}, {}),
        ({}, {"bounds": (1, 2, 300, 400), "actions": ("scroll", "tap")}),
        ({}, {"role": "container"}),
        ({}, {"label": "Results changed"}),
    ],
)
def test_scroll_postcondition_ignores_non_content_changes(
    current_screen_updates: dict[str, object],
    target_updates: dict[str, object],
) -> None:
    target = PublicNode(
        kind="container",
        role="scroll-container",
        label="Results",
        ref="n1",
        actions=("scroll",),
        bounds=(0, 0, 200, 400),
        scroll_directions=("down",),
        children=(PublicNode(kind="text", text="First row"),),
    )
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_target = target.model_copy(update=target_updates)
    current_screen = make_contract_screen(
        targets=(current_target,),
        screen_id=str(current_screen_updates.get("screen_id", "screen-after")),
        package_name=str(
            current_screen_updates.get("package_name", "com.android.settings")
        ),
        activity_name=str(
            current_screen_updates.get("activity_name", "SettingsActivity")
        ),
        keyboard_visible=bool(current_screen_updates.get("keyboard_visible", False)),
        dialog=current_screen_updates.get("dialog", ()),
    )

    with pytest.raises(DaemonError) as error:
        postconditions_module.validate_postcondition(
            ScrollCommand(ref="n1", source_screen_id="screen-before", direction="down"),
            previous_snapshot=make_snapshot(snapshot_id=1),
            snapshot=make_snapshot(snapshot_id=2),
            previous_screen=previous_screen,
            public_screen=current_screen,
            session=object(),
            focus_context=None,
            action_result=ActionPerformResult(
                action_id="act-scroll",
                status=ActionStatus.DONE,
            ),
            ref_context=ref_postcondition_context(previous_screen, "n1"),
        )

    assert error.value.code == "ACTION_NOT_CONFIRMED"


@pytest.mark.parametrize(
    "child_updates",
    [
        {"kind": "node"},
        {"role": "button"},
        {"label": "First row metadata changed"},
    ],
)
def test_scroll_postcondition_ignores_descendant_metadata_only_changes(
    child_updates: dict[str, object],
) -> None:
    child = PublicNode(
        kind="node",
        role="list-item",
        label="First row",
        value="row-1",
    )
    target = PublicNode(
        kind="container",
        role="scroll-container",
        label="Results",
        ref="n1",
        actions=("scroll",),
        scroll_directions=("down",),
        children=(child,),
    )
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(
            target.model_copy(
                update={"children": (child.model_copy(update=child_updates),)}
            ),
        ),
        screen_id="screen-after",
    )

    with pytest.raises(DaemonError) as error:
        postconditions_module.validate_postcondition(
            ScrollCommand(ref="n1", source_screen_id="screen-before", direction="down"),
            previous_snapshot=make_snapshot(snapshot_id=1),
            snapshot=make_snapshot(snapshot_id=2),
            previous_screen=previous_screen,
            public_screen=current_screen,
            session=object(),
            focus_context=None,
            action_result=ActionPerformResult(
                action_id="act-scroll",
                status=ActionStatus.DONE,
            ),
            ref_context=ref_postcondition_context(previous_screen, "n1"),
        )

    assert error.value.code == "ACTION_NOT_CONFIRMED"


def _long_tap_target(**updates: object) -> PublicNode:
    target = PublicNode(
        kind="container",
        role="list-item",
        label="Row",
        ref="n1",
        actions=("longTap",),
        bounds=(0, 0, 200, 80),
        children=(PublicNode(kind="text", text="Row text"),),
    )
    return target.model_copy(update=updates) if updates else target


def _validate_long_tap_postcondition(
    previous_screen,
    current_screen,
    *,
    ref: str = "n1",
) -> None:
    postconditions_module.validate_postcondition(
        LongTapCommand(ref=ref, source_screen_id="screen-before"),
        previous_snapshot=make_snapshot(snapshot_id=1),
        snapshot=make_snapshot(snapshot_id=2),
        previous_screen=previous_screen,
        public_screen=current_screen,
        session=object(),
        focus_context=None,
        action_result=ActionPerformResult(
            action_id="act-long-tap",
            status=ActionStatus.DONE,
        ),
        ref_context=ref_postcondition_context(previous_screen, ref),
    )


def test_long_tap_postcondition_fails_closed_without_visible_feedback() -> None:
    target = _long_tap_target()
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-after",
    )

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(previous_screen, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"
    assert error.value.message == "long-tap was not confirmed on the refreshed screen"
    assert error.value.details == {
        "reason": "long_tap_feedback_not_observed",
        "ref": "n1",
    }


@pytest.mark.parametrize(
    "current_screen_builder",
    [
        lambda screen, target: public_screen_with_group(
            screen,
            "context",
            (make_public_node(ref="n9", label="Copy", actions=("tap",)),),
        ),
        lambda screen, target: public_screen_with_group(
            screen,
            "dialog",
            (make_public_node(ref="n9", role="dialog", label="Actions"),),
        ),
        lambda screen, target: screen.model_copy(
            update={"transient": (TransientItem(kind="toast", text="Copied"),)}
        ),
        lambda screen, target: public_screen_with_target(
            screen,
            target.model_copy(update={"state": ("selected",)}),
        ),
        lambda screen, target: public_screen_with_target(
            screen,
            target.model_copy(update={"actions": ("tap", "longTap")}),
        ),
        lambda screen, target: public_screen_with_target(
            screen,
            target.model_copy(
                update={
                    "children": (
                        PublicNode(
                            ref="n2",
                            role="button",
                            label="Copy",
                            actions=("tap",),
                        ),
                    )
                }
            ),
        ),
    ],
)
def test_long_tap_postcondition_accepts_conservative_visible_feedback(
    current_screen_builder,
) -> None:
    target = _long_tap_target()
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    baseline_current_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-after",
    )
    current_screen = current_screen_builder(baseline_current_screen, target)

    _validate_long_tap_postcondition(previous_screen, current_screen)


@pytest.mark.parametrize("group_name", ["context", "dialog"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
def test_long_tap_postcondition_rejects_context_dialog_label_only_change(
    group_name,
    nested,
) -> None:
    target = _long_tap_target()
    baseline_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    if nested:
        child = make_public_node(ref="n10", label="Copy", actions=("tap",))
        previous_node = PublicNode(
            ref="n9",
            role="dialog" if group_name == "dialog" else "button",
            label="Actions",
            children=(child,),
        )
        current_node = previous_node.model_copy(
            update={"children": (child.model_copy(update={"label": "Share"}),)}
        )
    else:
        previous_node = make_public_node(
            ref="n9",
            role="dialog" if group_name == "dialog" else "button",
            label="Copy",
            actions=(),
        )
        current_node = previous_node.model_copy(update={"label": "Share"})
    previous_screen = public_screen_with_group(
        baseline_screen,
        group_name,
        (previous_node,),
    )
    current_screen = public_screen_with_group(
        baseline_screen.model_copy(update={"screen_id": "screen-after"}),
        group_name,
        (current_node,),
    )

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(previous_screen, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"
    assert error.value.details["reason"] == "long_tap_feedback_not_observed"


@pytest.mark.parametrize("group_name", ["context", "dialog"])
def test_long_tap_postcondition_accepts_existing_context_dialog_feedback_change(
    group_name,
) -> None:
    target = _long_tap_target()
    baseline_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    previous_node = make_public_node(
        ref="n9",
        role="dialog" if group_name == "dialog" else "button",
        label="Copy",
        actions=(),
    )
    current_node = previous_node.model_copy(update={"actions": ("tap",)})
    previous_screen = public_screen_with_group(
        baseline_screen,
        group_name,
        (previous_node,),
    )
    current_screen = public_screen_with_group(
        baseline_screen.model_copy(update={"screen_id": "screen-after"}),
        group_name,
        (current_node,),
    )

    _validate_long_tap_postcondition(previous_screen, current_screen)


def test_long_tap_postcondition_supports_source_ref_fallback_context() -> None:
    target = _long_tap_target()
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(target.model_copy(update={"state": ("selected",)}),),
        screen_id="screen-after",
    )

    postconditions_module.validate_postcondition(
        LongTapCommand(ref="n1", source_screen_id="screen-before"),
        previous_snapshot=make_snapshot(snapshot_id=1),
        snapshot=make_snapshot(snapshot_id=2),
        previous_screen=previous_screen,
        public_screen=current_screen,
        session=object(),
        focus_context=None,
        action_result=ActionPerformResult(
            action_id="act-long-tap",
            status=ActionStatus.DONE,
        ),
    )


@pytest.mark.parametrize(
    "current_screen_builder",
    [
        lambda screen, target: screen.model_copy(update={"screen_id": "screen-raw"}),
        lambda screen, target: screen.model_copy(
            update={
                "app": screen.app.model_copy(
                    update={
                        "package_name": "com.example.other",
                        "activity_name": "OtherActivity",
                    }
                )
            }
        ),
        lambda screen, target: screen.model_copy(
            update={
                "surface": screen.surface.model_copy(update={"keyboard_visible": True})
            }
        ),
        lambda screen, target: public_screen_with_group(
            screen,
            "system",
            (make_public_node(ref="n8", label="System changed", actions=("tap",)),),
        ),
        lambda screen, target: public_screen_with_target(
            screen,
            target.model_copy(update={"bounds": (1, 2, 300, 400)}),
        ),
        lambda screen, target: public_screen_with_target(
            screen,
            target.model_copy(update={"label": "Renamed row"}),
        ),
        lambda screen, target: public_screen_with_group(
            public_screen_with_target(screen, target),
            "targets",
            (),
        ),
        lambda screen, target: public_screen_with_group(
            screen,
            "targets",
            (
                target,
                make_public_node(ref="n3", label="Other", state=("selected",)),
            ),
        ),
    ],
)
def test_long_tap_postcondition_rejects_non_feedback_changes(
    current_screen_builder,
) -> None:
    target = _long_tap_target()
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    baseline_current_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-after",
    )
    current_screen = current_screen_builder(baseline_current_screen, target)

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(previous_screen, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"
    assert error.value.details["reason"] == "long_tap_feedback_not_observed"


def test_long_tap_postcondition_rejects_missing_baseline() -> None:
    current_screen = make_contract_screen(
        targets=(_long_tap_target(),),
        screen_id="screen-after",
    )

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(None, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"


def test_long_tap_postcondition_rejects_missing_baseline_target() -> None:
    previous_screen = make_contract_screen(
        targets=(make_public_node(ref="n4", label="Other"),),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(_long_tap_target(),),
        screen_id="screen-after",
    )

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(previous_screen, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"


def test_long_tap_postcondition_rejects_child_label_only_change() -> None:
    child = PublicNode(
        ref="n2",
        role="button",
        label="Copy",
        actions=("tap",),
    )
    target = _long_tap_target(children=(child,))
    previous_screen = make_contract_screen(
        targets=(target,),
        screen_id="screen-before",
    )
    current_screen = make_contract_screen(
        targets=(
            target.model_copy(
                update={"children": (child.model_copy(update={"label": "Share"}),)}
            ),
        ),
        screen_id="screen-after",
    )

    with pytest.raises(DaemonError) as error:
        _validate_long_tap_postcondition(previous_screen, current_screen)

    assert error.value.code == "ACTION_NOT_CONFIRMED"


def test_action_executor_long_tap_dispatch_fails_closed_when_feedback_unobserved(
    tmp_path,
) -> None:
    runtime, ref, screen_id, _handle = make_long_tap_runtime(tmp_path)
    assert runtime.screen_state is not None
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(
                action_id="act-long-tap",
                status=ActionStatus.DONE,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=99)),
        repairer=StrictRepairer(),
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            CommandRecord(
                command_id="cmd-long-tap-001",
                kind=CommandKind.LONG_TAP,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            LongTapCommand(ref=ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details["reason"] == (
        "long_tap_feedback_not_observed"
    )
    assert failure.value.dispatch_attempted is True
    assert len(device_calls) == 1
    assert isinstance(device_calls[0], LongTapActionRequest)


def test_action_executor_long_tap_context_feedback_succeeds_without_action_target(
    tmp_path,
) -> None:
    runtime, ref, screen_id, _handle = make_long_tap_runtime(tmp_path)
    assert runtime.screen_state is not None
    refreshed_screen = public_screen_with_group(
        runtime.screen_state.public_screen,
        "context",
        (make_public_node(ref="n9", label="Copy", actions=("tap",)),),
    )
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(
                action_id="act-long-tap",
                status=ActionStatus.DONE,
            )

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=refreshed_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=99)),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-long-tap-002",
            kind=CommandKind.LONG_TAP,
            status=CommandStatus.RUNNING,
            started_at="2026-05-07T00:00:00Z",
        ),
        LongTapCommand(ref=ref, source_screen_id=screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], LongTapActionRequest)
    assert result.action_target is None


def test_action_executor_repaired_long_tap_confirms_resolved_target_feedback(
    tmp_path,
) -> None:
    runtime, current_ref, _screen_id, handle = make_long_tap_runtime(tmp_path)
    assert runtime.screen_state is not None
    target = public_node_by_ref(runtime.screen_state.public_screen, current_ref)
    assert target is not None
    refreshed_screen = public_screen_with_target(
        runtime.screen_state.public_screen,
        target.model_copy(update={"state": ("selected",)}),
    )
    command = LongTapCommand(ref="source-ref", source_screen_id="screen-source")
    repairer = FakeRepairer(request=build_action_request_for_binding(handle, command))
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(
                action_id="act-long-tap",
                status=ActionStatus.DONE,
            )

    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=refreshed_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=99)),
        repairer=repairer,
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-long-tap-003",
            kind=CommandKind.LONG_TAP,
            status=CommandStatus.RUNNING,
            started_at="2026-05-07T00:00:00Z",
        ),
        command,
        runtime_store_lease(runtime),
    )

    assert current_ref != command.ref
    assert repairer.calls == ["longTap"]
    assert len(device_calls) == 1
    assert isinstance(device_calls[0], LongTapActionRequest)
    assert device_calls[0].target == HandleTarget(handle)


def test_action_executor_repaired_long_tap_failure_reports_source_and_target_refs(
    tmp_path,
) -> None:
    runtime, current_ref, _screen_id, handle = make_long_tap_runtime(tmp_path)
    assert runtime.screen_state is not None
    command = LongTapCommand(ref="source-ref", source_screen_id="screen-source")
    repairer = FakeRepairer(request=build_action_request_for_binding(handle, command))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(
                action_id="act-long-tap",
                status=ActionStatus.DONE,
            )

    with pytest.raises(ActionExecutionFailure) as failure:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                RecordingClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=runtime.screen_state.public_screen,
                artifacts=runtime.screen_state.artifacts,
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=99)),
            repairer=repairer,
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-long-tap-004",
                kind=CommandKind.LONG_TAP,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            command,
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details == {
        "reason": "long_tap_feedback_not_observed",
        "ref": "source-ref",
        "targetRef": current_ref,
    }


def test_action_executor_long_tap_stale_target_retry_uses_retry_baseline(
    tmp_path,
) -> None:
    runtime, ref, screen_id, handle = make_long_tap_runtime(tmp_path)
    assert runtime.screen_state is not None
    target = public_node_by_ref(runtime.screen_state.public_screen, ref)
    assert target is not None
    retry_target = target.model_copy(update={"state": ("selected",)})
    retry_screen = public_screen_with_target(
        runtime.screen_state.public_screen,
        retry_target,
    )
    device_calls = []

    class RetryRepairer(StrictRepairer):
        def repair_action_command(
            self,
            session,
            record,
            command,
            *,
            lifecycle_lease,
        ):
            del record, lifecycle_lease
            install_screen_state(
                session,
                public_screen=retry_screen,
                compiled_screen=session.screen_state.compiled_screen,
                artifacts=session.screen_state.artifacts,
            )
            return build_action_request_for_binding(handle, command)

    class StaleThenDoneClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            if len(device_calls) == 1:
                raise DaemonError(
                    code="DEVICE_RPC_FAILED",
                    message="stale target",
                    retryable=True,
                    details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                    http_status=200,
                )
            return ActionPerformResult(
                action_id="act-long-tap",
                status=ActionStatus.DONE,
            )

    with pytest.raises(ActionExecutionFailure) as failure:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                StaleThenDoneClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=retry_screen,
                artifacts=runtime.screen_state.artifacts,
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=100)),
            repairer=RetryRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-long-tap-005",
                kind=CommandKind.LONG_TAP,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            LongTapCommand(ref=ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "ACTION_NOT_CONFIRMED"
    assert failure.value.normalized_error.details["reason"] == (
        "long_tap_feedback_not_observed"
    )
    assert len(device_calls) == 2


def test_action_executor_submit_current_focused_input_with_submit_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, ref, screen_id = make_current_submit_runtime(
        tmp_path,
        raw_actions=("focus", "setText", "submit", "click"),
    )
    install_keyboard_blocking_surface(runtime)
    assert runtime.screen_state is not None
    target = runtime.screen_state.public_screen.groups[0].nodes[0]
    assert target.role == "input"
    assert "focused" in target.state
    assert "submit" in target.actions
    device_calls = []
    refreshed_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            focused=False,
            actions=("focus", "setText", "submit", "click"),
        ),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: None,
        raising=False,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00007",
        kind=CommandKind.SUBMIT,
        status=CommandStatus.RUNNING,
        started_at="2026-05-03T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        SubmitCommand(ref=ref, source_screen_id=screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], NodeActionRequest)
    assert device_calls[0].action == "submit"


def test_action_executor_submit_without_route_rejects_before_dispatch(
    tmp_path,
) -> None:
    runtime, ref, screen_id = make_current_submit_runtime(
        tmp_path,
        raw_actions=("focus", "setText", "action_321", "click"),
    )
    assert runtime.screen_state is not None
    target = runtime.screen_state.public_screen.groups[0].nodes[0]
    assert target.role == "input"
    assert "focused" in target.state
    assert "submit" not in target.actions
    device_calls = []

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            device_calls.append("dispatch")
            raise AssertionError("submit should not dispatch without exposed submit")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00008",
        kind=CommandKind.SUBMIT,
        status=CommandStatus.RUNNING,
        started_at="2026-05-03T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            record,
            SubmitCommand(ref=ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_missing"
    assert error.value.details["action"] == "submit"
    assert device_calls == []


def test_action_executor_submit_refs_take_precedence_over_direct_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    input_node = public_node_by_ref(runtime.screen_state.public_screen, input_ref)
    assert input_node is not None
    install_public_screen_with_target(
        runtime,
        input_node.model_copy(
            update={
                "actions": (*input_node.actions, "submit"),
                "submit_refs": (button_ref,),
            }
        ),
    )
    device_calls = []
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: None,
        raising=False,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=43)),
        repairer=StrictRepairer(),
    )

    executor.execute(
        runtime,
        CommandRecord(
            command_id="cmd-00009",
            kind=CommandKind.SUBMIT,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        SubmitCommand(ref=input_ref, source_screen_id=screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)


def test_action_executor_submit_refs_enable_attributed_tap_routing(
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    button_binding = runtime.ref_registry.get(button_ref)
    assert button_binding is not None
    device_calls = []
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False, actions=("focus", "setText")),
        _make_raw_node(
            rid="w1:submit",
            class_name="android.widget.Button",
            text="Search",
            editable=False,
            focusable=False,
            actions=("click",),
            bounds=(420, 0, 560, 80),
        ),
        snapshot_id=43,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00010",
            kind=CommandKind.SUBMIT,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        SubmitCommand(ref=input_ref, source_screen_id=screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)
    assert device_calls[0].target == HandleTarget(button_binding.handle)
    assert result.action_target is not None
    assert result.action_target.source_ref == input_ref
    assert result.action_target.subject_ref == input_ref
    assert result.action_target.dispatched_ref == button_ref
    assert result.action_target.next_ref is None
    assert result.action_target.evidence == (
        "liveRef",
        "attributedRoute",
        "submitConfirmation",
        "publicChange",
    )


def test_action_executor_attributed_submit_requires_unique_submit_ref(tmp_path) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    input_node = public_node_by_ref(runtime.screen_state.public_screen, input_ref)
    assert input_node is not None
    install_public_screen_with_target(
        runtime,
        input_node.model_copy(update={"submit_refs": (button_ref, "n999")}),
    )

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: object(),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00011",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_ambiguous"


def test_action_executor_attributed_submit_requires_tap_capable_target(
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    button_node = public_node_by_ref(runtime.screen_state.public_screen, button_ref)
    assert button_node is not None
    install_public_screen_with_target(
        runtime,
        button_node.model_copy(update={"actions": ()}),
    )

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: object(),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00012",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_target_not_tap_capable"


def test_action_executor_submit_ref_precedence_requires_tap_not_direct_capability(
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["submit"],
    )
    input_node = public_node_by_ref(runtime.screen_state.public_screen, input_ref)
    assert input_node is not None
    install_public_screen_with_target(
        runtime,
        input_node.model_copy(
            update={
                "actions": (*input_node.actions, "submit"),
                "submit_refs": (button_ref,),
            }
        ),
    )

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: object(),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00013",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "DEVICE_AGENT_CAPABILITY_MISMATCH"
    assert error.value.details["missingActionKinds"] == ["tap"]


def test_action_executor_attributed_submit_non_stale_failure_does_not_fallback_direct(
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    input_node = public_node_by_ref(runtime.screen_state.public_screen, input_ref)
    assert input_node is not None
    install_public_screen_with_target(
        runtime,
        input_node.model_copy(
            update={
                "actions": (*input_node.actions, "submit"),
                "submit_refs": (button_ref,),
            }
        ),
    )
    device_calls = []

    class NonStaleFailClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            raise DaemonError(
                code="DEVICE_RPC_FAILED",
                message="target not actionable",
                retryable=False,
                details={"deviceCode": DeviceRpcErrorCode.TARGET_NOT_ACTIONABLE.value},
                http_status=200,
            )

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                NonStaleFailClient()
            ),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00014",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)


def test_action_executor_attributed_submit_transport_failure_does_not_fallback_direct(
    tmp_path,
) -> None:
    runtime, input_ref, button_ref, screen_id = make_attributed_submit_runtime(tmp_path)
    assert runtime.screen_state is not None
    input_node = public_node_by_ref(runtime.screen_state.public_screen, input_ref)
    assert input_node is not None
    install_public_screen_with_target(
        runtime,
        input_node.model_copy(
            update={
                "actions": (*input_node.actions, "submit"),
                "submit_refs": (button_ref,),
            }
        ),
    )
    device_calls = []

    class TransportFailClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            raise DaemonError(
                code="DEVICE_RPC_TRANSPORT_RESET",
                message="transport reset",
                retryable=True,
                details={"reason": "transport_reset"},
                http_status=200,
            )

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                TransportFailClient()
            ),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00014b",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "DEVICE_RPC_TRANSPORT_RESET"
    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)


def test_action_executor_attributed_submit_tap_only_device_can_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, input_ref, _button_ref, screen_id = make_attributed_submit_runtime(
        tmp_path
    )
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["tap"],
    )
    device_calls = []
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: None,
        raising=False,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(make_snapshot(snapshot_id=43)),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00014",
            kind=CommandKind.SUBMIT,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        SubmitCommand(ref=input_ref, source_screen_id=screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)


def test_action_executor_attributed_submit_requires_device_tap_capability(
    tmp_path,
) -> None:
    runtime, input_ref, _button_ref, screen_id = make_attributed_submit_runtime(
        tmp_path
    )
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["node"],
    )
    device_calls = []

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            device_calls.append("dispatch")
            raise AssertionError("attributed submit should fail before dispatch")

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                FailClient()
            ),
            screen_refresh=object(),
            settler=object(),
            repairer=StrictRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00015",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "DEVICE_AGENT_CAPABILITY_MISMATCH"
    assert error.value.details["missingActionKinds"] == ["tap"]
    assert device_calls == []


def test_action_executor_initial_stale_submit_repairs_then_uses_current_submit_refs(
    tmp_path,
) -> None:
    runtime, current_input_ref, current_button_ref, _screen_id = (
        make_attributed_submit_runtime(tmp_path)
    )
    assert runtime.screen_state is not None
    source_ref = "n9"
    source_screen_id = _PREVIOUS_FOCUS_SCREEN_ID
    current_input_binding = runtime.ref_registry.get(current_input_ref)
    assert current_input_binding is not None
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False, actions=("focus", "setText")),
        _make_raw_node(
            rid="w1:submit",
            class_name="android.widget.Button",
            text="Search",
            editable=False,
            focusable=False,
            actions=("click",),
            bounds=(420, 0, 560, 80),
        ),
        snapshot_id=43,
    )
    device_calls = []

    class RepairToCurrentInput(StrictRepairer):
        def repair_action_binding(
            self,
            session,
            record,
            command,
            *,
            lifecycle_lease,
        ):
            del session, record, command, lifecycle_lease
            return current_input_binding.handle

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=RepairToCurrentInput(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00016",
            kind=CommandKind.SUBMIT,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        SubmitCommand(ref=source_ref, source_screen_id=source_screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TapActionRequest)
    assert result.action_target is not None
    assert result.action_target.source_ref == source_ref
    assert result.action_target.subject_ref == current_input_ref
    assert result.action_target.dispatched_ref == current_button_ref
    assert result.action_target.next_ref is None
    assert result.action_target.evidence == (
        "refRepair",
        "attributedRoute",
        "submitConfirmation",
        "publicChange",
    )


def test_action_executor_direct_submit_stale_retry_does_not_downgrade_to_attributed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, input_ref, screen_id = make_current_submit_runtime(
        tmp_path,
        raw_actions=("focus", "setText", "submit", "click"),
    )
    retry_snapshot = make_attributed_submit_snapshot(
        snapshot_id=43,
        input_actions=("focus", "setText"),
        button_actions=("click",),
        focused=True,
    )
    retry_compiled = SemanticCompiler().compile(_FOCUS_SEQUENCE + 1, retry_snapshot)
    retry_finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=retry_compiled,
        snapshot_id=retry_snapshot.snapshot_id,
        previous_registry=None,
    )
    retry_screen = retry_finalized.compiled_screen.to_public_screen()
    retry_input_ref = retry_screen.surface.focus.input_ref
    assert retry_input_ref is not None
    retry_input_node = public_node_by_ref(retry_screen, retry_input_ref)
    assert retry_input_node is not None
    assert retry_input_node.submit_refs
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: None,
        raising=False,
    )
    device_calls = []

    class RerouteRepairer(StrictRepairer):
        def repair_action_binding(
            self,
            session,
            record,
            command,
            *,
            lifecycle_lease,
        ):
            del record, command, lifecycle_lease
            session.ref_registry = retry_finalized.registry
            install_screen_state(
                session,
                snapshot=retry_snapshot,
                public_screen=retry_screen,
                compiled_screen=retry_finalized.compiled_screen,
                artifacts=build_screen_artifacts(
                    session,
                    screen_id=retry_screen.screen_id,
                ),
            )
            binding = session.ref_registry.get(retry_input_ref)
            assert binding is not None
            return binding.handle

    class StaleThenDoneClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            if len(device_calls) == 1:
                raise DaemonError(
                    code="DEVICE_RPC_FAILED",
                    message="stale target",
                    retryable=True,
                    details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                    http_status=200,
                )
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                StaleThenDoneClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=retry_screen,
                artifacts=build_screen_artifacts(
                    runtime,
                    screen_id=retry_screen.screen_id,
                ),
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=44)),
            repairer=RerouteRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00015",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_changed_after_repair"
    assert isinstance(device_calls[0], NodeActionRequest)
    assert len(device_calls) == 1


def test_action_executor_attributed_submit_stale_retry_does_not_downgrade_to_direct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, input_ref, _button_ref, screen_id = make_attributed_submit_runtime(
        tmp_path,
    )
    retry_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            focused=True,
            actions=("focus", "setText", "submit", "click"),
        ),
        snapshot_id=43,
    )
    retry_compiled = SemanticCompiler().compile(_FOCUS_SEQUENCE + 1, retry_snapshot)
    retry_finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=retry_compiled,
        snapshot_id=retry_snapshot.snapshot_id,
        previous_registry=None,
    )
    retry_screen = retry_finalized.compiled_screen.to_public_screen()
    retry_input_ref = retry_screen.surface.focus.input_ref
    assert retry_input_ref is not None
    retry_input_node = public_node_by_ref(retry_screen, retry_input_ref)
    assert retry_input_node is not None
    assert retry_input_node.submit_refs == ()
    assert "submit" in retry_input_node.actions
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: None,
        raising=False,
    )
    device_calls = []

    class RerouteRepairer(StrictRepairer):
        def repair_action_binding(
            self,
            session,
            record,
            command,
            *,
            lifecycle_lease,
        ):
            del record, command, lifecycle_lease
            session.ref_registry = retry_finalized.registry
            install_screen_state(
                session,
                snapshot=retry_snapshot,
                public_screen=retry_screen,
                compiled_screen=retry_finalized.compiled_screen,
                artifacts=build_screen_artifacts(
                    session,
                    screen_id=retry_screen.screen_id,
                ),
            )
            binding = session.ref_registry.get(retry_input_ref)
            assert binding is not None
            return binding.handle

    class StaleThenDoneClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            if len(device_calls) == 1:
                raise DaemonError(
                    code="DEVICE_RPC_FAILED",
                    message="stale target",
                    retryable=True,
                    details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                    http_status=200,
                )
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    with pytest.raises(DaemonError) as error:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: (
                StaleThenDoneClient()
            ),
            screen_refresh=StaticScreenRefresh(
                public_screen=retry_screen,
                artifacts=build_screen_artifacts(
                    runtime,
                    screen_id=retry_screen.screen_id,
                ),
            ),
            settler=FakeSettler(make_snapshot(snapshot_id=44)),
            repairer=RerouteRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00015b",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref=input_ref, source_screen_id=screen_id),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_changed_after_repair"
    assert isinstance(device_calls[0], TapActionRequest)
    assert len(device_calls) == 1


def test_build_action_request_for_binding_uses_canonical_replace_only_type_shape() -> (
    None
):
    request = build_action_request_for_binding(
        _REQUEST_HANDLE,
        TypeCommand(
            ref=_FOCUS_REF,
            source_screen_id=_FOCUS_SCREEN_ID,
            text="wifi",
        ),
    )

    assert isinstance(request.payload, TypeActionRequest)
    assert request.payload.target == HandleTarget(_REQUEST_HANDLE)
    assert request.payload.text == "wifi"
    assert request.payload.submit is False
    assert request.payload.timeout_ms == action_timeout_ms(CommandKind.TYPE)
    assert not hasattr(request.payload, "append")
    assert request.request_handle == _REQUEST_HANDLE


def test_action_executor_stale_target_error_repairs_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    device_calls: list[str] = []
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="focus"),
    )

    class StaleTargetClient:
        def action_perform(self, payload, *, request_id: str):
            del payload
            device_calls.append(request_id)
            raise DaemonError(
                code="DEVICE_RPC_FAILED",
                message="stale target",
                retryable=True,
                details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            StaleTargetClient()
        ),
        screen_refresh=object(),
        settler=object(),
        repairer=FakeRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.FOCUS,
        status=CommandStatus.RUNNING,
        started_at="2026-04-13T00:00:00Z",
    )
    command = FocusCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID)

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert device_calls == ["androidctld-action", "androidctld-action"]


def test_action_executor_stale_target_retry_rebuilds_focus_confirmation_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    finalize_runtime_refs(runtime)
    source_screen_id = runtime.current_screen_id
    assert source_screen_id is not None
    retry_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False),
        snapshot_id=43,
    )
    retry_compiled = SemanticCompiler().compile(_FOCUS_SEQUENCE + 1, retry_snapshot)
    retry_finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=retry_compiled,
        snapshot_id=retry_snapshot.snapshot_id,
        previous_registry=None,
    )
    retry_screen = retry_finalized.compiled_screen.to_public_screen()
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=True),
        snapshot_id=43,
    )
    focus_calls = []
    original_focus_confirmation = postconditions_module.validate_focus_confirmation

    def record_focus_confirmation(**kwargs):
        focus_calls.append(kwargs["context"])
        return original_focus_confirmation(**kwargs)

    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="focus"),
    )
    monkeypatch.setattr(
        "androidctld.actions.postconditions.validate_focus_confirmation",
        record_focus_confirmation,
    )

    class RepairingRetry(StrictRepairer):
        def repair_action_command(
            self,
            session,
            record,
            command: RefBoundActionCommand,
            *,
            lifecycle_lease,
        ):
            del record, lifecycle_lease
            session.ref_registry = retry_finalized.registry
            install_screen_state(
                session,
                snapshot=retry_snapshot,
                public_screen=retry_screen,
                compiled_screen=retry_finalized.compiled_screen,
                artifacts=build_screen_artifacts(
                    session,
                    screen_id=retry_screen.screen_id,
                ),
            )
            return build_action_request_for_binding(_REFRESHED_HANDLE, command)

    class StaleThenDoneClient:
        def __init__(self) -> None:
            self.calls = 0

        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            self.calls += 1
            if self.calls == 1:
                raise DaemonError(
                    code="DEVICE_RPC_FAILED",
                    message="stale target",
                    retryable=True,
                    details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                    http_status=200,
                )
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            StaleThenDoneClient()
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=RepairingRetry(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00016",
            kind=CommandKind.FOCUS,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        FocusCommand(ref=_FOCUS_REF, source_screen_id=source_screen_id),
        runtime_store_lease(runtime),
    )

    assert focus_calls[0].request_handle == _REFRESHED_HANDLE
    assert result.action_target is not None
    assert result.action_target.evidence[0] == "refRepair"


def test_action_executor_global_stale_source_builds_request_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    build_calls: list[str] = []
    device_calls: list[str] = []
    refreshed_snapshot = make_snapshot(make_raw_node(rid="w1:0.5", focused=False))
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )

    def spy_build_action_request(session, command):
        build_calls.append(command.kind.value)
        return build_action_request(session, command)

    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        spy_build_action_request,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload
            device_calls.append(request_id)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_FOCUS_SCREEN_ID,
                sequence=_FOCUS_SEQUENCE,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00005",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-04-27T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        GlobalCommand(action="back", source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    assert build_calls == ["global"]
    assert device_calls == ["androidctld-action"]


@pytest.mark.parametrize(
    ("kind", "command"),
    [
        (
            CommandKind.OPEN,
            OpenCommand(target=OpenUrlTarget(url="https://example.com")),
        ),
        (
            CommandKind.GLOBAL,
            GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
        ),
    ],
)
def test_action_executor_non_ref_stale_target_does_not_repair_or_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: CommandKind,
    command: OpenCommand | GlobalCommand,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    device_calls: list[str] = []
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )

    class StaleTargetClient:
        def action_perform(self, payload, *, request_id: str):
            del payload
            device_calls.append(request_id)
            raise DaemonError(
                code="DEVICE_RPC_FAILED",
                message="stale target",
                retryable=True,
                details={"deviceCode": DeviceRpcErrorCode.STALE_TARGET.value},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            StaleTargetClient()
        ),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00006",
        kind=kind,
        status=CommandStatus.RUNNING,
        started_at="2026-04-27T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details["deviceCode"] == DeviceRpcErrorCode.STALE_TARGET.value
    assert device_calls == ["androidctld-action"]


def test_action_executor_returns_typed_settle_availability_failure_after_dispatch(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class FailingSettler:
        def settle(self, session, client, kind, baseline_signature, **kwargs):
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code="DEVICE_RPC_FAILED",
                message="device observation lost after dispatch",
                retryable=True,
                details={"deviceCode": "TRANSPORT_CLOSED"},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=object(),
        settler=FailingSettler(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00002",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-04-18T00:00:00Z",
    )
    command = GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID)

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert failure.value.original_error.code == "DEVICE_RPC_FAILED"
    assert failure.value.normalized_error.code == "DEVICE_RPC_FAILED"
    assert failure.value.original_error.details == {"deviceCode": "TRANSPORT_CLOSED"}
    assert failure.value.dispatch_attempted is True
    assert failure.value.truth_lost_after_dispatch is True


def test_action_executor_discards_transport_after_post_dispatch_transport_reset(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    close_calls: list[str] = []
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class FailingSettler:
        def settle(self, session, client, kind, baseline_signature, **kwargs):
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
                message="device rpc transport reset after dispatch",
                retryable=True,
                details={"reason": "connection_reset"},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=object(),
        settler=FailingSettler(),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-transport-reset-after-dispatch",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert (
        failure.value.original_error.code is DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET
    )
    assert (
        failure.value.normalized_error.code
        is DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET
    )
    assert failure.value.dispatch_attempted is True
    assert failure.value.truth_lost_after_dispatch is True
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is None
    assert close_calls == ["closed"]
    assert runtime.device_capabilities is None


def test_action_executor_discards_transport_after_post_dispatch_device_disconnected(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    close_calls: list[str] = []
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class FailingSettler:
        def settle(self, session, client, kind, baseline_signature, **kwargs):
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_DISCONNECTED,
                message="device disconnected after dispatch",
                retryable=True,
                details={"reason": "device_disconnected"},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=object(),
        settler=FailingSettler(),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-device-disconnected-after-dispatch",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert failure.value.original_error.code is DaemonErrorCode.DEVICE_DISCONNECTED
    assert failure.value.normalized_error.code is DaemonErrorCode.DEVICE_DISCONNECTED
    assert failure.value.dispatch_attempted is True
    assert failure.value.truth_lost_after_dispatch is True
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is None
    assert close_calls == ["closed"]
    assert runtime.device_capabilities is None


def test_action_executor_post_dispatch_unauthorized_invalidates_credentials(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    close_calls: list[str] = []
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))
    previous_revision = runtime.lifecycle_revision

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class FailingSettler:
        def settle(self, session, client, kind, baseline_signature, **kwargs):
            del session, client, kind, baseline_signature, kwargs
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
                message="device agent rejected request credentials",
                retryable=False,
                details={"status": 401},
                http_status=200,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=object(),
        settler=FailingSettler(),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00012",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert (
        failure.value.original_error.code is DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED
    )
    assert (
        failure.value.normalized_error.code is DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED
    )
    assert failure.value.dispatch_attempted is True
    assert failure.value.truth_lost_after_dispatch is False
    assert close_calls == ["closed"]
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.lifecycle_revision == previous_revision
    assert runtime.connection is None
    assert runtime.device_token is None
    assert runtime.device_capabilities is None
    assert runtime.transport is None
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert "currentScreenId" not in persisted


def test_global_action_drops_current_authority_after_accepted_before_settle(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))
    previous_snapshot = runtime.latest_snapshot
    previous_compiled = current_compiled_screen(runtime)
    assert previous_snapshot is not None
    expected_baseline = settle_screen_signature(previous_compiled, previous_snapshot)

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            assert runtime.current_screen_id == _FOCUS_SCREEN_ID
            assert runtime.screen_state is not None
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    class AssertingSettler:
        def settle(self, session, client, kind, baseline_signature, **kwargs):
            del client, kind, kwargs
            assert session is runtime
            assert get_authoritative_current_basis(runtime) is None
            assert runtime.current_screen_id is None
            assert runtime.screen_state is None
            assert runtime.status.value == "connected"
            assert baseline_signature == expected_baseline
            return SettledSnapshot(
                snapshot=make_snapshot(
                    make_raw_node(rid="w1:0.6", focused=False, text="Display"),
                    snapshot_id=43,
                ),
                timed_out=False,
            )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_REFRESHED_SCREEN_ID,
                sequence=_FOCUS_SEQUENCE + 1,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=AssertingSettler(),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00007",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )


@pytest.mark.parametrize("action", ["recents", "notifications"])
def test_global_system_action_unchanged_old_app_screen_fails_without_committing_current(
    tmp_path,
    action: str,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    close_calls: list[str] = []
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: close_calls.append("closed"),
    )
    original_transport = runtime.transport
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(
            make_snapshot(
                make_raw_node(rid="w1:0.5", focused=False),
                snapshot_id=99,
            )
        ),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00008",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action=action, source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "SCREEN_NOT_READY"
    assert failure.value.normalized_error.details["reason"] == (
        "post_action_system_evidence_missing"
    )
    assert failure.value.truth_lost_after_dispatch is True
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    assert runtime.status.value == "connected"
    assert runtime.transport is original_transport
    assert close_calls == []
    assert runtime.device_capabilities == DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["global"],
    )


@pytest.mark.parametrize("action", ["recents", "notifications"])
def test_global_system_action_persistent_systemui_surface_does_not_reauthorize_app(
    tmp_path,
    action: str,
) -> None:
    app_node = make_raw_node(rid="w1:0.5", focused=False)
    system_node = _make_raw_node(
        rid="system:root",
        window_id="system-window",
        class_name="android.widget.ImageButton",
        text="Back",
        package_name="com.android.systemui",
        bounds=(0, 2320, 180, 2400),
        editable=False,
        focusable=False,
        actions=("click",),
    )
    windows = (
        RawWindow(
            window_id="w1",
            type="application",
            layer=1,
            package_name="com.android.settings",
            bounds=(0, 0, 1080, 2400),
            root_rid=app_node.rid,
        ),
        RawWindow(
            window_id="system-window",
            type="system",
            layer=20,
            package_name="com.android.systemui",
            bounds=(0, 2320, 1080, 2400),
            root_rid=system_node.rid,
        ),
    )
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_FOCUS_SEQUENCE,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    previous_snapshot = make_snapshot(app_node, system_node, windows=windows)
    previous_compiled = SemanticCompiler().compile(_FOCUS_SEQUENCE, previous_snapshot)
    install_screen_state(
        runtime,
        snapshot=previous_snapshot,
        public_screen=make_contract_screen(
            screen_id=_FOCUS_SCREEN_ID,
            sequence=_FOCUS_SEQUENCE,
            targets=_DEFAULT_FOCUS_TARGETS,
            input_ref=_FOCUS_REF,
            keyboard_visible=False,
        ),
        compiled_screen=previous_compiled,
        artifacts=build_screen_artifacts(
            runtime,
            screen_id=_FOCUS_SCREEN_ID,
        ),
    )
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(
            make_snapshot(
                make_raw_node(rid="w1:0.5", focused=False),
                system_node,
                snapshot_id=99,
                windows=windows,
            )
        ),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00011",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action=action, source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "SCREEN_NOT_READY"
    assert failure.value.normalized_error.details["reason"] == (
        "post_action_system_evidence_missing"
    )
    assert failure.value.truth_lost_after_dispatch is True
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    assert runtime.status.value == "connected"


@pytest.mark.parametrize("action", ["recents", "notifications"])
def test_global_system_action_system_only_noise_does_not_reauthorize_app(
    tmp_path,
    action: str,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(
            make_snapshot(
                make_raw_node(rid="w1:0.5", focused=False),
                _make_raw_node(
                    rid="system:noise",
                    window_id="system-window",
                    class_name="android.widget.TextView",
                    text="Transient status",
                    package_name="com.android.systemui",
                    bounds=(0, 0, 1080, 80),
                    editable=False,
                    focusable=False,
                    actions=(),
                ),
                snapshot_id=99,
                windows=(
                    RawWindow(
                        window_id="w1",
                        type="application",
                        layer=1,
                        package_name="com.android.settings",
                        bounds=(0, 0, 1080, 2400),
                        root_rid="w1:0.5",
                    ),
                    RawWindow(
                        window_id="system-window",
                        type="system",
                        layer=20,
                        package_name="com.android.systemui",
                        bounds=(0, 0, 1080, 80),
                        root_rid="system:noise",
                    ),
                ),
            )
        ),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00012",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        executor.execute(
            runtime,
            record,
            GlobalCommand(action=action, source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "SCREEN_NOT_READY"
    assert failure.value.normalized_error.details["reason"] == (
        "post_action_system_evidence_missing"
    )
    assert failure.value.truth_lost_after_dispatch is True
    assert get_authoritative_current_basis(runtime) is None
    assert runtime.current_screen_id is None
    assert runtime.status.value == "connected"


@pytest.mark.parametrize("action", ["recents", "notifications"])
def test_global_system_action_app_surface_change_reestablishes_current(
    tmp_path,
    action: str,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(
            make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    focused=False,
                    text="Display settings",
                ),
                snapshot_id=99,
            )
        ),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00009",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        GlobalCommand(action=action, source_screen_id=_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    basis = get_authoritative_current_basis(runtime)
    assert basis is not None
    assert basis.snapshot_id == 99
    assert basis.package_name == "com.android.settings"
    assert basis.compiled_screen.package_name == "com.android.settings"
    assert basis.compiled_screen.targets[0].label == "Display settings"
    assert runtime.status.value == "ready"


@pytest.mark.parametrize("action", ["recents", "notifications"])
def test_global_system_action_systemui_evidence_reestablishes_current(
    tmp_path,
    action: str,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    system_snapshot = make_snapshot(
        snapshot_id=99,
        package_name="com.android.systemui",
        activity_name="SystemUiActivity",
        nodes=(
            _make_raw_node(
                rid="system:root",
                window_id="system-window",
                class_name="android.widget.FrameLayout",
                text="System UI",
                package_name="com.android.systemui",
                editable=False,
                focusable=False,
                actions=(),
            ),
        ),
    )
    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(system_snapshot),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00010",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        GlobalCommand(action=action, source_screen_id=_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    basis = get_authoritative_current_basis(runtime)
    assert basis is not None
    assert basis.package_name == "com.android.systemui"
    assert runtime.status.value == "ready"


def test_source_less_recents_without_previous_current_can_commit_fresh_snapshot(
    tmp_path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.CONNECTED)
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(runtime_kernel=kernel),
        settler=FakeSettler(
            make_snapshot(
                snapshot_id=99,
                nodes=(
                    _make_raw_node(
                        rid="w1:0.8",
                        text="Home",
                        editable=False,
                        focusable=False,
                        actions=(),
                    ),
                ),
            )
        ),
        repairer=StrictRepairer(),
        runtime_kernel=kernel,
    )
    record = CommandRecord(
        command_id="cmd-00011",
        kind=CommandKind.GLOBAL,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    executor.execute(
        runtime,
        record,
        GlobalCommand(action="recents", source_screen_id=None),
        runtime_store_lease(runtime),
    )

    assert get_authoritative_current_basis(runtime) is not None
    assert runtime.status.value == "ready"


def test_ref_action_with_old_source_does_not_dispatch_without_fresh_current(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    attach_runtime_transport(runtime)
    kernel = RuntimeKernel(runtime_store_for_workspace(tmp_path))
    assert kernel.drop_current_screen_authority(
        runtime,
        runtime_store_lease(runtime),
    )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: pytest.fail(
            "ref action must not enter Android RPC without fresh current"
        ),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00009",
        kind=CommandKind.TAP,
        status=CommandStatus.RUNNING,
        started_at="2026-05-07T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            record,
            TapCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "SCREEN_NOT_READY"


def test_ref_action_no_transport_screen_not_ready_does_not_rebootstrap(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    runtime.transport = None
    runtime.status = RuntimeStatus.CONNECTED
    runtime.latest_snapshot = None
    runtime.screen_state = None
    runtime.current_screen_id = None
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=[],
    )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: pytest.fail(
            "ref action without current screen must not rebootstrap"
        ),
        screen_refresh=object(),
        settler=object(),
        repairer=StrictRepairer(),
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            CommandRecord(
                command_id="cmd-ref-no-current",
                kind=CommandKind.TAP,
                status=CommandStatus.RUNNING,
                started_at="2026-05-07T00:00:00Z",
            ),
            TapCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "SCREEN_NOT_READY"


def test_action_executor_allows_single_dispatch_when_source_screen_id_needs_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    write_repair_artifact(
        runtime,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        sequence=41,
    )
    device_calls: list[str] = []
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="tap"),
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del payload
            device_calls.append(request_id)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_FOCUS_SCREEN_ID,
                sequence=_FOCUS_SEQUENCE,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(make_snapshot(make_raw_node(rid="w1:0.9", focused=True))),
        repairer=FakeRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.FOCUS,
        status=CommandStatus.RUNNING,
        started_at="2026-04-13T00:00:00Z",
    )
    command = FocusCommand(
        ref=_FOCUS_REF,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
    )

    executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert device_calls == ["androidctld-action"]


def test_action_executor_returns_minimal_semantic_assembly_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="tap"),
    )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_REFRESHED_SCREEN_ID,
                sequence=43,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00002",
        kind=CommandKind.TAP,
        status=CommandStatus.RUNNING,
        started_at="2026-04-20T00:00:00Z",
    )

    result = executor.execute(
        runtime,
        record,
        TapCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    assert result.app_payload is not None
    assert result.app_payload.package_name == "com.android.settings"
    assert result.warnings == ()
    assert hasattr(result, "runtime") is False
    assert hasattr(result, "screen") is False
    assert hasattr(result, "summary") is False


@pytest.mark.parametrize(
    ("kind", "command"),
    [
        (
            CommandKind.OPEN,
            OpenCommand(target=OpenUrlTarget(url="https://example.com")),
        ),
        (
            CommandKind.GLOBAL,
            GlobalCommand(action="back", source_screen_id=_FOCUS_SCREEN_ID),
        ),
        (
            CommandKind.TAP,
            TapCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
        ),
    ],
)
def test_action_executor_exposes_settle_timeout_warning_for_success_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: CommandKind,
    command: OpenCommand | GlobalCommand | TapCommand,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="tap"),
    )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_REFRESHED_SCREEN_ID,
                sequence=43,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(refreshed_snapshot, timed_out=True),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00003",
        kind=kind,
        status=CommandStatus.RUNNING,
        started_at="2026-04-20T00:00:00Z",
    )

    result = executor.execute(
        runtime,
        record,
        command,
        runtime_store_lease(runtime),
    )

    assert result.warnings == (
        "post-dispatch observation timed out before stability was confirmed",
    )


def test_action_executor_repairs_stale_source_screen_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    runtime.current_screen_id = _FOCUS_SCREEN_ID
    repairer = FakeRepairer()
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_FOCUS_SCREEN_ID,
                sequence=_FOCUS_SEQUENCE,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_FOCUS_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(make_snapshot(make_raw_node(rid="w1:0.5", focused=True))),
        repairer=repairer,
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.TAP,
        status=CommandStatus.RUNNING,
        started_at="2026-04-14T00:00:00Z",
    )
    command = TapCommand(ref=_FOCUS_REF, source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID)

    executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert repairer.calls == ["tap"]


def test_action_executor_repaired_target_miss_uses_ref_repair_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    missing_handle = NodeHandle(snapshot_id=43, rid="w1:missing")
    repairer = FakeRepairer(
        request=BuiltDeviceActionRequest(
            payload=NodeActionRequest(
                target=HandleTarget(missing_handle),
                action="tap",
                timeout_ms=1000,
            ),
            request_handle=missing_handle,
        )
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            raise AssertionError("missing repaired target should not dispatch")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=repairer,
    )
    record = CommandRecord(
        command_id="cmd-00004",
        kind=CommandKind.TAP,
        status=CommandStatus.RUNNING,
        started_at="2026-04-27T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(
            runtime,
            record,
            TapCommand(ref=_FOCUS_REF, source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert error.value.code == "REF_STALE"
    assert error.value.retryable is True
    assert error.value.details["sourceScreenId"] == _PREVIOUS_FOCUS_SCREEN_ID
    assert error.value.details["sourceArtifactStatus"] == "repair_failed"
    assert "screen" in error.value.details
    assert "artifacts" in error.value.details
    assert repairer.calls == ["tap"]


def test_action_executor_repaired_type_still_requires_current_focused_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    write_repair_artifact(
        runtime,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        sequence=41,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            raise AssertionError("repaired type should not dispatch without focus")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=FakeRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.TYPE,
        status=CommandStatus.RUNNING,
        started_at="2026-04-13T00:00:00Z",
    )
    command = TypeCommand(
        ref=_FOCUS_REF,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        text="wifi",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "action_not_exposed"


@pytest.mark.parametrize(
    "command",
    [
        TypeCommand(
            ref=_FOCUS_REF,
            source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
            text="wifi",
        ),
        SubmitCommand(
            ref=_FOCUS_REF,
            source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        ),
    ],
)
def test_action_executor_repaired_input_action_reports_keyboard_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    command: TypeCommand | SubmitCommand,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    write_repair_artifact(
        runtime,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        sequence=41,
    )
    blocked_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False, actions=("focus", "setText")),
        snapshot_id=43,
    )
    blocked_target = make_semantic_node(
        raw_rid="w1:0.5",
        ref=_FOCUS_REF,
        role="input",
        label="Search settings",
        group="targets",
    )
    blocked_target.state = []
    blocked_target.actions = []
    keyboard_key = make_semantic_node(
        raw_rid="ime:key",
        ref="n2",
        role="button",
        label="Search",
        group="keyboard",
    )
    keyboard_key.actions = ["tap"]
    blocked_screen = make_compiled_screen(
        _REFRESHED_SCREEN_ID,
        sequence=43,
        source_snapshot_id=43,
        fingerprint="blocked-keyboard-surface",
        targets=[blocked_target],
    )
    blocked_screen.keyboard_visible = True
    blocked_screen.blocking_group = "keyboard"
    blocked_screen.keyboard = [keyboard_key]
    install_screen_state(
        runtime,
        snapshot=blocked_snapshot,
        public_screen=make_contract_screen(
            screen_id=_REFRESHED_SCREEN_ID,
            sequence=43,
            targets=(
                make_public_node(
                    ref=_FOCUS_REF,
                    role="input",
                    label="Search settings",
                    state=(),
                    actions=(),
                ),
            ),
            input_ref=None,
            blocking_group="keyboard",
            keyboard_visible=True,
        ),
        compiled_screen=blocked_screen,
        artifacts=build_screen_artifacts(
            runtime,
            screen_id=_REFRESHED_SCREEN_ID,
        ),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            raise AssertionError("blocked repaired input should not dispatch")

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FailClient(),
        screen_refresh=object(),
        settler=object(),
        repairer=FakeRepairer(
            request=build_action_request_for_binding(_REFRESHED_HANDLE, command)
        ),
    )
    record = CommandRecord(
        command_id="cmd-00003",
        kind=command.kind,
        status=CommandStatus.RUNNING,
        started_at="2026-04-18T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        executor.execute(runtime, record, command, runtime_store_lease(runtime))

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_keyboard"


def test_action_executor_repaired_focused_input_type_allowed_under_keyboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    write_repair_artifact(
        runtime,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        sequence=41,
    )
    blocked_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=True, actions=("focus", "setText")),
        snapshot_id=43,
    )
    focused_target = make_semantic_node(
        raw_rid="w1:0.5",
        ref=_FOCUS_REF,
        role="input",
        label="Search settings",
        group="targets",
    )
    focused_target.state = ["focused"]
    focused_target.actions = ["type"]
    keyboard_key = make_semantic_node(
        raw_rid="ime:key",
        ref="n2",
        role="button",
        label="Search",
        group="keyboard",
    )
    keyboard_key.actions = ["tap"]
    blocked_screen = make_compiled_screen(
        _REFRESHED_SCREEN_ID,
        sequence=43,
        source_snapshot_id=43,
        fingerprint="blocked-keyboard-surface",
        targets=[focused_target],
    )
    blocked_screen.keyboard_visible = True
    blocked_screen.blocking_group = "keyboard"
    blocked_screen.keyboard = [keyboard_key]
    install_screen_state(
        runtime,
        snapshot=blocked_snapshot,
        public_screen=make_contract_screen(
            screen_id=_REFRESHED_SCREEN_ID,
            sequence=43,
            targets=(
                make_public_node(
                    ref=_FOCUS_REF,
                    role="input",
                    label="Search settings",
                    state=("focused",),
                    actions=("type",),
                ),
            ),
            input_ref=_FOCUS_REF,
            blocking_group="keyboard",
            keyboard_visible=True,
        ),
        compiled_screen=blocked_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=_REFRESHED_SCREEN_ID),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_type_confirmation",
        lambda **kwargs: TypeConfirmationCandidate(
            strategy="requestTarget",
            node=blocked_snapshot.nodes[0],
            target_handle=_REFRESHED_HANDLE,
        ),
    )
    device_calls = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    command = TypeCommand(
        ref=_FOCUS_REF,
        source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
        text="wifi",
    )
    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=runtime.screen_state.public_screen,
            artifacts=runtime.screen_state.artifacts,
        ),
        settler=FakeSettler(blocked_snapshot),
        repairer=FakeRepairer(
            request=build_action_request_for_binding(_REFRESHED_HANDLE, command)
        ),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00003b",
            kind=command.kind,
            status=CommandStatus.RUNNING,
            started_at="2026-04-18T00:00:00Z",
        ),
        command,
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], TypeActionRequest)


def test_action_executor_focus_runs_same_target_confirmation_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=True),
        snapshot_id=43,
    )
    resolved_target = ResolvedHandleTarget(handle=_REFRESHED_HANDLE)
    focus_calls = []
    original_focus_confirmation = postconditions_module.validate_focus_confirmation

    def record_focus_confirmation(**kwargs):
        focus_calls.append(kwargs["context"])
        return original_focus_confirmation(**kwargs)

    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="focus"),
    )
    monkeypatch.setattr(
        "androidctld.actions.postconditions.validate_focus_confirmation",
        record_focus_confirmation,
    )
    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(
                action_id="action-1",
                status=ActionStatus.DONE,
                resolved_target=resolved_target,
            )
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.FOCUS,
        status=CommandStatus.RUNNING,
        started_at="2026-04-07T00:00:00Z",
    )
    source_screen_id = runtime.current_screen_id
    assert source_screen_id is not None
    focus_command = FocusCommand(ref=_FOCUS_REF, source_screen_id=source_screen_id)

    executor.execute(runtime, record, focus_command, runtime_store_lease(runtime))

    assert focus_calls[0].request_handle == _REQUEST_HANDLE
    assert focus_calls[0].resolved_target == resolved_target


def test_action_executor_focus_accepts_current_successor_ref_after_real_refresh(
    tmp_path,
) -> None:
    runtime = build_runtime(
        tmp_path,
        screen_sequence=0,
    )
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.LAN,
        host="127.0.0.1",
        port=17171,
    )
    runtime.device_token = "device-token"
    previous_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            focused=False,
        ),
        snapshot_id=42,
    )
    previous_compiled = SemanticCompiler().compile(_FOCUS_SEQUENCE, previous_snapshot)
    previous_finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=previous_compiled,
        snapshot_id=previous_snapshot.snapshot_id,
        previous_registry=None,
    )
    previous_public_screen = previous_finalized.compiled_screen.to_public_screen()
    runtime.ref_registry = previous_finalized.registry
    install_screen_state(
        runtime,
        snapshot=previous_snapshot,
        public_screen=previous_public_screen,
        compiled_screen=previous_finalized.compiled_screen,
        artifacts=build_screen_artifacts(
            runtime,
            screen_id=previous_public_screen.screen_id,
        ),
    )
    assert runtime.ref_registry.get(_FOCUS_REF) is not None
    assert previous_public_screen.surface.focus.input_ref is None

    refreshed_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.4",
            text="Search settings",
            focused=False,
        ),
        make_raw_node(
            rid="w1:0.5",
            text="Search successor",
            focused=True,
        ),
        snapshot_id=43,
    )
    device_calls: list[object] = []

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(
                action_id="action-1",
                status=ActionStatus.DONE,
                resolved_target=ResolvedHandleTarget(handle=_REQUEST_HANDLE),
            )

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00009",
            kind=CommandKind.FOCUS,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        FocusCommand(ref=_FOCUS_REF, source_screen_id=previous_public_screen.screen_id),
        runtime_store_lease(runtime),
    )

    assert len(device_calls) == 1
    assert isinstance(device_calls[0], NodeActionRequest)
    assert device_calls[0].target == HandleTarget(_REQUEST_HANDLE)
    assert runtime.screen_state is not None
    public_screen = runtime.screen_state.public_screen
    assert public_screen is not None
    assert public_screen.surface.focus.input_ref is not None
    assert public_screen.surface.focus.input_ref != _FOCUS_REF
    compiled_screen = runtime.screen_state.compiled_screen
    assert compiled_screen is not None
    assert compiled_screen.focused_input_ref() == public_screen.surface.focus.input_ref
    focused_node = compiled_screen.focused_input_node()
    assert focused_node is not None
    assert focused_node.raw_rid == _REQUEST_HANDLE.rid
    source_binding = runtime.ref_registry.get(_FOCUS_REF)
    assert source_binding is not None
    assert source_binding.handle.rid == "w1:0.4"
    assert result.app_payload is not None
    assert result.action_target is not None
    assert result.action_target.source_ref == _FOCUS_REF
    assert result.action_target.subject_ref == _FOCUS_REF
    assert result.action_target.dispatched_ref == _FOCUS_REF
    assert result.action_target.identity_status == "successor"
    assert result.action_target.next_ref == public_screen.surface.focus.input_ref
    assert "resolvedTarget" in result.action_target.evidence
    assert "focusConfirmation" in result.action_target.evidence


def _assert_unrepaired_refresh_outcome(
    tmp_path,
    *,
    command_id: str,
    expected_command_kind: CommandKind,
    refreshed_snapshot: RawSnapshot,
    expected_identity_status: str,
    expected_next_ref: str | None,
    expected_evidence: tuple[str, ...],
) -> None:
    command: RefBoundActionCommand
    if expected_command_kind == CommandKind.FOCUS:
        runtime = make_focus_runtime(tmp_path, previous_focused=False)
        finalize_runtime_refs(runtime)
        source_screen_id = runtime.current_screen_id
        assert source_screen_id is not None
        command = FocusCommand(ref=_FOCUS_REF, source_screen_id=source_screen_id)
    elif expected_command_kind == CommandKind.TYPE:
        runtime = make_focus_runtime(tmp_path, previous_focused=True)
        finalize_runtime_refs(runtime)
        install_keyboard_blocking_surface(runtime)
        source_screen_id = runtime.current_screen_id
        assert source_screen_id is not None
        command = TypeCommand(
            ref=_FOCUS_REF,
            source_screen_id=source_screen_id,
            text="wifi",
        )
    elif expected_command_kind == CommandKind.SUBMIT:
        runtime, ref, screen_id = make_current_submit_runtime(
            tmp_path,
            raw_actions=("focus", "setText", "submit", "click"),
        )
        command = SubmitCommand(ref=ref, source_screen_id=screen_id)
    else:
        raise AssertionError(f"unexpected command kind: {expected_command_kind}")

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="action-1", status=ActionStatus.DONE)
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id=command_id,
            kind=expected_command_kind,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        command,
        runtime_store_lease(runtime),
    )

    assert result.action_target is not None
    assert result.action_target.identity_status == expected_identity_status
    assert result.action_target.next_ref == expected_next_ref
    assert result.action_target.evidence == expected_evidence


@pytest.mark.parametrize(
    (
        "command_id",
        "expected_command_kind",
        "refreshed_snapshot",
        "expected_identity_status",
        "expected_next_ref",
        "expected_evidence",
    ),
    (
        pytest.param(
            "cmd-00010",
            CommandKind.FOCUS,
            make_snapshot(
                make_raw_node(rid="w1:0.5", focused=True),
                snapshot_id=43,
            ),
            "sameRef",
            _FOCUS_REF,
            ("liveRef", "requestTarget", "focusConfirmation"),
            id="focus-same-ref",
        ),
        pytest.param(
            "cmd-00011",
            CommandKind.TYPE,
            make_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                snapshot_id=43,
            ),
            "sameRef",
            _FOCUS_REF,
            ("liveRef", "requestTarget", "typeConfirmation"),
            id="type-same-ref",
        ),
        pytest.param(
            "cmd-00012",
            CommandKind.SUBMIT,
            make_snapshot(snapshot_id=43),
            "gone",
            None,
            ("liveRef", "submitConfirmation", "targetGone"),
            id="submit-gone",
        ),
        pytest.param(
            "cmd-00013",
            CommandKind.SUBMIT,
            make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search results",
                    focused=True,
                    actions=("focus", "setText", "submit", "click"),
                ),
                snapshot_id=43,
            ),
            "unconfirmed",
            None,
            ("liveRef", "submitConfirmation", "publicChange"),
            id="submit-public-change",
        ),
    ),
)
def test_action_executor_emits_unrepaired_action_target_after_real_refresh(
    tmp_path,
    command_id: str,
    expected_command_kind: CommandKind,
    refreshed_snapshot: RawSnapshot,
    expected_identity_status: str,
    expected_next_ref: str | None,
    expected_evidence: tuple[str, ...],
) -> None:
    _assert_unrepaired_refresh_outcome(
        tmp_path,
        command_id=command_id,
        expected_command_kind=expected_command_kind,
        refreshed_snapshot=refreshed_snapshot,
        expected_identity_status=expected_identity_status,
        expected_next_ref=expected_next_ref,
        expected_evidence=expected_evidence,
    )


def test_action_executor_repaired_focus_emits_action_target_from_current_screen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    finalize_runtime_refs(runtime)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=True),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="action-1", status=ActionStatus.DONE)
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=FakeRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00013",
            kind=CommandKind.FOCUS,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        FocusCommand(ref="n9", source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    assert result.action_target is not None
    assert result.action_target.source_ref == "n9"
    assert result.action_target.subject_ref == _FOCUS_REF
    assert result.action_target.dispatched_ref == _FOCUS_REF
    assert result.action_target.identity_status == "sameRef"
    assert result.action_target.next_ref == _FOCUS_REF
    assert result.action_target.evidence == (
        "refRepair",
        "requestTarget",
        "focusConfirmation",
    )


def test_action_executor_repaired_type_emits_action_target_from_current_screen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=True)
    finalize_runtime_refs(runtime)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", text="wifi", focused=True),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="action-1", status=ActionStatus.DONE)
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=FakeRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00014",
            kind=CommandKind.TYPE,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        TypeCommand(
            ref="n9",
            source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID,
            text="wifi",
        ),
        runtime_store_lease(runtime),
    )

    assert result.action_target is not None
    assert result.action_target.source_ref == "n9"
    assert result.action_target.subject_ref == _FOCUS_REF
    assert result.action_target.dispatched_ref == _FOCUS_REF
    assert result.action_target.identity_status == "sameRef"
    assert result.action_target.next_ref == _FOCUS_REF
    assert result.action_target.evidence == (
        "refRepair",
        "requestTarget",
        "typeConfirmation",
    )


def test_action_executor_direct_submit_unconfirmed_fails_closed_without_action_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, _ref, _screen_id = make_current_submit_runtime(
        tmp_path,
        raw_actions=("focus", "setText", "submit", "click"),
    )
    refreshed_snapshot = make_snapshot(
        snapshot_id=43,
        package_name="com.example.other",
        activity_name="OtherActivity",
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )

    with pytest.raises(ActionExecutionFailure) as failure:
        ActionExecutor(
            device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
                ActionPerformResult(action_id="action-1", status=ActionStatus.DONE)
            ),
            screen_refresh=ScreenRefreshService(
                RuntimeKernel(runtime_store_for_workspace(tmp_path)),
            ),
            settler=FakeSettler(refreshed_snapshot),
            repairer=FakeRepairer(),
        ).execute(
            runtime,
            CommandRecord(
                command_id="cmd-00015",
                kind=CommandKind.SUBMIT,
                status=CommandStatus.RUNNING,
                started_at="2026-05-03T00:00:00Z",
            ),
            SubmitCommand(ref="n9", source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID),
            runtime_store_lease(runtime),
        )

    assert failure.value.normalized_error.code == "SUBMIT_NOT_CONFIRMED"
    assert failure.value.normalized_error.details["reason"] == (
        "direct_submit_not_confirmed"
    )
    assert failure.value.dispatch_attempted is True
    assert failure.value.truth_lost_after_dispatch is False


def test_action_executor_attributed_submit_unconfirmed_keeps_existing_action_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, _ref, _button_ref, _screen_id = make_attributed_submit_runtime(tmp_path)
    refreshed_snapshot = make_snapshot(
        snapshot_id=43,
        package_name="com.example.other",
        activity_name="OtherActivity",
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_action_semantics",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_resolved_ref_action",
        lambda session, command, request_handle: None,
    )

    result = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(action_id="action-1", status=ActionStatus.DONE)
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=FakeRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00015",
            kind=CommandKind.SUBMIT,
            status=CommandStatus.RUNNING,
            started_at="2026-05-03T00:00:00Z",
        ),
        SubmitCommand(ref="n9", source_screen_id=_PREVIOUS_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    assert result.action_target is not None
    assert result.action_target.source_ref == "n9"
    assert result.action_target.subject_ref != result.action_target.dispatched_ref
    assert result.action_target.identity_status == "unconfirmed"
    assert result.action_target.next_ref is None
    assert result.action_target.evidence == (
        "refRepair",
        "attributedRoute",
        "submitConfirmation",
        "ambiguousSuccessor",
    )
    assert "nextRef" not in result.action_target.model_dump(
        by_alias=True,
        mode="json",
    )


def test_action_executor_same_semantics_refresh_keeps_new_generation_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=False)
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False),
        snapshot_id=43,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: make_device_request(action="focus"),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )

    ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: FakeClient(
            ActionPerformResult(
                action_id="action-1",
                status=ActionStatus.DONE,
                resolved_target=ResolvedNoneTarget(),
                observed=None,
            )
        ),
        screen_refresh=ScreenRefreshService(
            RuntimeKernel(runtime_store_for_workspace(tmp_path)),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    ).execute(
        runtime,
        CommandRecord(
            command_id="cmd-00001",
            kind=CommandKind.FOCUS,
            status=CommandStatus.RUNNING,
            started_at="2026-04-10T00:00:00Z",
        ),
        FocusCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
        runtime_store_lease(runtime),
    )

    assert runtime.current_screen_id != _FOCUS_SCREEN_ID
    assert runtime.latest_snapshot is not None
    assert runtime.latest_snapshot.snapshot_id == 43


def test_action_executor_focus_already_focused_noop_success_before_dispatch(
    tmp_path,
) -> None:
    runtime = make_focus_runtime(tmp_path, previous_focused=True)
    finalize_runtime_refs(runtime)
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=[],
    )

    class FailClient:
        def action_perform(self, payload, *, request_id: str):
            del payload, request_id
            raise AssertionError("already-focused focus must not dispatch")

    class FailSettler:
        def settle(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("already-focused focus must not settle")

    class FailRefresh:
        def refresh(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("already-focused focus must not refresh")

    factory_calls = []

    def fail_factory(session, *, lifecycle_lease=None):
        del session, lifecycle_lease
        factory_calls.append("factory")
        return FailClient()

    executor = ActionExecutor(
        device_client_factory=fail_factory,
        screen_refresh=FailRefresh(),
        settler=FailSettler(),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00001",
        kind=CommandKind.FOCUS,
        status=CommandStatus.RUNNING,
        started_at="2026-04-07T00:00:00Z",
    )
    source_screen_id = runtime.current_screen_id
    assert source_screen_id is not None
    focus_command = FocusCommand(ref=_FOCUS_REF, source_screen_id=source_screen_id)

    result = executor.execute(
        runtime,
        record,
        focus_command,
        runtime_store_lease(runtime),
    )

    assert result.execution_outcome == "notAttempted"
    assert result.action_target is None
    assert factory_calls == []


def test_action_executor_routes_standalone_submit_through_shared_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime, ref, source_screen_id = make_current_submit_runtime(
        tmp_path,
        raw_actions=("focus", "setText", "submit", "click"),
    )
    refreshed_snapshot = make_snapshot(
        make_raw_node(rid="w1:0.5", focused=False),
        snapshot_id=43,
    )
    device_calls = []
    submit_calls = []
    request = BuiltDeviceActionRequest(
        payload=NodeActionRequest(
            target=HandleTarget(_REQUEST_HANDLE),
            action="submit",
            timeout_ms=1000,
        ),
        request_handle=_REQUEST_HANDLE,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.ensure_command_supported",
        lambda session, command: None,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.build_action_request",
        lambda session, command: request,
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_postcondition",
        lambda *args, **kwargs: PostconditionOutcome(),
    )
    monkeypatch.setattr(
        "androidctld.actions.executor.validate_submit_confirmation",
        lambda **kwargs: submit_calls.append(kwargs["command_target_handle"]),
        raising=False,
    )

    class RecordingClient:
        def action_perform(self, payload, *, request_id: str):
            del request_id
            device_calls.append(payload)
            return ActionPerformResult(action_id="act-1", status=ActionStatus.DONE)

    executor = ActionExecutor(
        device_client_factory=lambda session, *, lifecycle_lease=None: (
            RecordingClient()
        ),
        screen_refresh=StaticScreenRefresh(
            public_screen=make_contract_screen(
                screen_id=_FOCUS_SCREEN_ID,
                sequence=_FOCUS_SEQUENCE,
                targets=_DEFAULT_FOCUS_TARGETS,
                input_ref=_FOCUS_REF,
                keyboard_visible=False,
            ),
            artifacts=build_screen_artifacts(
                runtime,
                screen_id=_REFRESHED_SCREEN_ID,
            ),
        ),
        settler=FakeSettler(refreshed_snapshot),
        repairer=StrictRepairer(),
    )
    record = CommandRecord(
        command_id="cmd-00004",
        kind=CommandKind.SUBMIT,
        status=CommandStatus.RUNNING,
        started_at="2026-04-07T00:00:00Z",
    )
    submit_command = SubmitCommand(
        ref=ref,
        source_screen_id=source_screen_id,
    )

    executor.execute(runtime, record, submit_command, runtime_store_lease(runtime))

    assert len(device_calls) == 1
    assert submit_calls == [_REQUEST_HANDLE]
