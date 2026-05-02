from __future__ import annotations

from pathlib import Path

import pytest

from androidctld.actions.focus_confirmation import (
    FocusConfirmationContext,
    FocusConfirmationOutcome,
    validate_focus_confirmation,
)
from androidctld.actions.postconditions import validate_postcondition
from androidctld.commands.command_models import FocusCommand
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    ResolvedHandleTarget,
    ResolvedNoneTarget,
)
from androidctld.errors import DaemonError
from androidctld.refs.models import (
    NodeHandle,
    RefBinding,
    RefFingerprint,
    SemanticProfile,
)
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.semantics.public_models import PublicNode, public_group_nodes

from .support.runtime import build_runtime, install_screen_state
from .support.semantic_screen import (
    make_compiled_screen,
    make_public_node,
    make_raw_node,
    make_semantic_node,
)
from .support.semantic_screen import (
    make_contract_screen as make_public_screen,
)
from .support.semantic_screen import (
    make_contract_snapshot as make_snapshot,
)

_FOCUS_RUNTIME_ROOT = Path("/tmp/focus-runtime")
_FOCUS_SCREEN_ID = "screen-00001"
_FOCUS_REF = "n1"
_REQUEST_HANDLE = NodeHandle(snapshot_id=42, rid="w1:0.5")
_REUSED_HANDLE = NodeHandle(snapshot_id=42, rid="w1:0.9")


def make_focus_target(
    *,
    label: str,
    state: tuple[str, ...],
    actions: tuple[str, ...],
) -> PublicNode:
    return make_public_node(
        ref=_FOCUS_REF,
        role="input",
        label=label,
        state=state,
        actions=actions,
    )


def make_binding() -> RefBinding:
    return RefBinding(
        ref=_FOCUS_REF,
        handle=_REQUEST_HANDLE,
        fingerprint=RefFingerprint(
            role="input",
            normalized_label="search settings",
            resource_id="",
            class_name="android.widget.edittext",
            parent_role="",
            parent_label="",
            sibling_labels=(),
            relative_bounds=(0, 0, 100, 40),
        ),
        semantic_profile=SemanticProfile(
            state=(),
            actions=("tap", "type", "focus"),
        ),
    )


def test_compiler_exposes_focus_only_for_public_input_targets() -> None:
    snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=False,
            actions=("focus", "setText", "click"),
        ),
        make_raw_node(
            rid="w1:0.6",
            text="Toolbar",
            class_name="android.widget.TextView",
            editable=False,
            focused=False,
            actions=("focus", "click"),
        ),
    )

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()

    target = next(
        node
        for node in public_group_nodes(screen, "targets")
        if node.label == "Search settings"
    )
    assert "focus" in target.actions
    assert all(
        "focus" not in node.actions
        for node in public_group_nodes(screen, "targets")
        if node.label != "Search settings"
    )


def test_compiler_exposes_focus_for_input_target_with_non_type_primary_action() -> None:
    snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=False,
            actions=("focus", "click"),
        )
    )

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()
    target = next(
        node
        for node in public_group_nodes(screen, "targets")
        if node.label == "Search settings"
    )
    assert "focus" in target.actions


def test_compiler_omits_focus_for_already_focused_input_target() -> None:
    snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText", "click"),
        )
    )

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()
    target = next(
        node
        for node in public_group_nodes(screen, "targets")
        if node.label == "Search settings"
    )
    assert "focus" not in target.actions


def test_compiler_uses_anchor_focus_state_for_promoted_input_targets() -> None:
    field = make_raw_node(
        rid="w1:0.5",
        text="",
        editable=True,
        focused=True,
        actions=("focus", "setText", "click"),
    )
    label = make_raw_node(
        rid="w1:0.4",
        text="Search settings",
        class_name="android.widget.TextView",
        editable=False,
        focused=False,
        actions=(),
    )
    object.__setattr__(field, "child_rids", ("w1:0.4",))
    object.__setattr__(label, "parent_rid", "w1:0.5")
    snapshot = make_snapshot(field, label)

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()
    target = next(
        node
        for node in public_group_nodes(screen, "targets")
        if node.label == "Search settings"
    )
    assert "focused" in target.state
    assert "focus" not in target.actions


def test_compiler_does_not_export_input_when_editable_identity_is_cross_node() -> None:
    actionable_parent = make_raw_node(
        rid="w1:0.1",
        text=None,
        class_name="android.widget.LinearLayout",
        editable=False,
        focused=False,
        actions=("click",),
    )
    label = make_raw_node(
        rid="w1:0.2",
        text="Search label",
        class_name="android.widget.TextView",
        editable=False,
        focused=False,
        actions=(),
    )
    editable_child = make_raw_node(
        rid="w1:0.3",
        text="Search settings",
        editable=True,
        focused=False,
        actions=(),
    )
    object.__setattr__(actionable_parent, "child_rids", ("w1:0.2", "w1:0.3"))
    object.__setattr__(label, "parent_rid", "w1:0.1")
    object.__setattr__(editable_child, "parent_rid", "w1:0.1")
    snapshot = make_snapshot(actionable_parent, label, editable_child)

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()

    assert all(
        not (node.role == "input" and node.label == "Search settings")
        for node in public_group_nodes(screen, "targets")
    )


def test_compiler_does_not_expose_focus_for_cross_node_input_promotion() -> None:
    actionable_parent = make_raw_node(
        rid="w1:0.1",
        text=None,
        class_name="android.widget.LinearLayout",
        editable=False,
        focused=False,
        actions=("click", "focus"),
    )
    label = make_raw_node(
        rid="w1:0.2",
        text="Search label",
        class_name="android.widget.TextView",
        editable=False,
        focused=False,
        actions=(),
    )
    editable_child = make_raw_node(
        rid="w1:0.3",
        text="Search settings",
        editable=True,
        focused=False,
        actions=(),
    )
    object.__setattr__(actionable_parent, "child_rids", ("w1:0.2", "w1:0.3"))
    object.__setattr__(label, "parent_rid", "w1:0.1")
    object.__setattr__(editable_child, "parent_rid", "w1:0.1")
    snapshot = make_snapshot(actionable_parent, label, editable_child)

    screen = SemanticCompiler().compile(1, snapshot).to_public_screen()

    assert all(
        "focus" not in node.actions
        for node in public_group_nodes(screen, "targets")
        if node.label == "Search settings"
    )


def test_focus_public_target_preserves_actions_in_serialized_contract() -> None:
    target = make_focus_target(
        label="Search",
        state=(),
        actions=("tap", "type", "focus"),
    )

    assert target.model_dump(by_alias=True, mode="json")["actions"] == [
        "tap",
        "type",
        "focus",
    ]


def test_focus_confirmation_accepts_same_target_when_it_becomes_focused() -> None:
    outcome = validate_focus_confirmation(
        session=build_runtime(
            _FOCUS_RUNTIME_ROOT,
            screen_sequence=1,
            current_screen_id=_FOCUS_SCREEN_ID,
        ),
        previous_snapshot=make_snapshot(
            make_raw_node(
                rid="w1:0.5",
                text="Search settings",
                editable=True,
                focused=False,
                actions=("focus", "setText"),
            )
        ),
        snapshot=make_snapshot(
            make_raw_node(
                rid="w1:0.5",
                text="Search settings",
                editable=True,
                focused=True,
                actions=("focus", "setText"),
            )
        ),
        context=FocusConfirmationContext(
            request_handle=_REQUEST_HANDLE,
            binding=None,
            resolved_target=ResolvedNoneTarget(),
        ),
    )

    assert isinstance(outcome, FocusConfirmationOutcome)
    assert outcome.strategy == "requestTarget"
    assert outcome.node.rid == "w1:0.5"
    assert outcome.target_handle == NodeHandle(snapshot_id=42, rid="w1:0.5")


def test_focus_confirmation_rejects_target_that_was_already_focused() -> None:
    with pytest.raises(DaemonError) as error:
        validate_focus_confirmation(
            session=build_runtime(
                _FOCUS_RUNTIME_ROOT,
                screen_sequence=1,
                current_screen_id=_FOCUS_SCREEN_ID,
            ),
            previous_snapshot=make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search settings",
                    editable=True,
                    focused=True,
                    actions=("focus", "setText"),
                )
            ),
            snapshot=make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search settings",
                    editable=True,
                    focused=True,
                    actions=("focus", "setText"),
                )
            ),
            context=FocusConfirmationContext(
                request_handle=_REQUEST_HANDLE,
                binding=None,
                resolved_target=ResolvedNoneTarget(),
            ),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "already_focused"


def test_focus_confirmation_accepts_fingerprint_rematch_when_refresh_changes_rid(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        tmp_path,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    previous_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=False,
            actions=("focus", "setText"),
        )
    )
    rematched = make_raw_node(
        rid="w2:0.7",
        text="Search settings",
        editable=True,
        focused=True,
        actions=("focus", "setText"),
    )
    refreshed_snapshot = make_snapshot(rematched)
    compiled_screen = SemanticCompiler().compile(2, refreshed_snapshot)
    install_screen_state(
        runtime,
        snapshot=refreshed_snapshot,
        public_screen=compiled_screen.to_public_screen(),
        compiled_screen=compiled_screen,
    )

    outcome = validate_focus_confirmation(
        session=runtime,
        previous_snapshot=previous_snapshot,
        snapshot=refreshed_snapshot,
        context=FocusConfirmationContext(
            request_handle=_REQUEST_HANDLE,
            binding=make_binding(),
            resolved_target=ResolvedNoneTarget(),
        ),
    )

    assert outcome.strategy == "fingerprintRematch"
    assert outcome.node.rid == "w2:0.7"
    assert outcome.target_handle == NodeHandle(
        snapshot_id=refreshed_snapshot.snapshot_id,
        rid="w2:0.7",
    )


def test_focus_confirmation_prefers_reused_ref_when_request_handle_stays_unfocused(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        tmp_path,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    runtime.ref_registry.bindings[_FOCUS_REF] = make_binding()
    previous_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=False,
            actions=("focus", "setText"),
        )
    )
    reused = make_raw_node(
        rid="w1:0.9",
        text="Search settings",
        editable=True,
        focused=True,
        actions=("focus", "setText"),
    )
    stale = make_raw_node(
        rid="w1:0.5",
        text="Search settings",
        editable=True,
        focused=False,
        actions=("focus", "setText"),
    )
    refreshed_snapshot = make_snapshot(stale, reused)
    runtime.ref_registry.bindings[_FOCUS_REF] = RefBinding(
        ref=_FOCUS_REF,
        handle=_REUSED_HANDLE,
        fingerprint=make_binding().fingerprint,
        semantic_profile=make_binding().semantic_profile,
        reused=True,
    )
    compiled_screen = SemanticCompiler().compile(2, refreshed_snapshot)
    install_screen_state(
        runtime,
        snapshot=refreshed_snapshot,
        public_screen=compiled_screen.to_public_screen(),
        compiled_screen=compiled_screen,
    )

    outcome = validate_focus_confirmation(
        session=runtime,
        previous_snapshot=previous_snapshot,
        snapshot=refreshed_snapshot,
        context=FocusConfirmationContext(
            request_handle=_REQUEST_HANDLE,
            binding=make_binding(),
            resolved_target=ResolvedNoneTarget(),
        ),
    )

    assert outcome.strategy == "reusedRef"
    assert outcome.node.rid == "w1:0.9"
    assert outcome.target_handle == NodeHandle(
        snapshot_id=refreshed_snapshot.snapshot_id,
        rid="w1:0.9",
    )


def test_focus_confirmation_requires_same_identity_chain_target_to_become_focused() -> (
    None
):
    with pytest.raises(DaemonError) as error:
        validate_focus_confirmation(
            session=build_runtime(
                _FOCUS_RUNTIME_ROOT,
                screen_sequence=1,
                current_screen_id=_FOCUS_SCREEN_ID,
            ),
            previous_snapshot=make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search settings",
                    editable=True,
                    focused=False,
                    actions=("focus", "setText"),
                )
            ),
            snapshot=make_snapshot(
                make_raw_node(
                    rid="w1:0.8",
                    text="Other field",
                    editable=True,
                    focused=True,
                    actions=("focus", "setText"),
                )
            ),
            context=FocusConfirmationContext(
                request_handle=_REQUEST_HANDLE,
                binding=None,
                resolved_target=ResolvedNoneTarget(),
            ),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"


def _focus_action_result() -> ActionPerformResult:
    return ActionPerformResult(
        action_id="act-1",
        status=ActionStatus.DONE,
        resolved_target=ResolvedHandleTarget(handle=_REQUEST_HANDLE),
    )


def _focused_input_screen(
    *,
    input_ref: str | None,
    target_ref: str | None,
):
    return make_public_screen(
        targets=(
            make_focus_target(
                label="Search settings",
                state=("focused",),
                actions=("type",),
            ).model_copy(update={"ref": target_ref}),
        ),
        input_ref=input_ref,
    )


def _compiled_input_screen(
    *,
    ref: str,
    raw_rid: str,
    snapshot_id: int = _REQUEST_HANDLE.snapshot_id,
):
    node = make_semantic_node(
        raw_rid=raw_rid,
        ref=ref,
        role="input",
        label="Search settings",
    )
    node.state = ["focused"]
    node.actions = ["type"]
    return make_compiled_screen(
        _FOCUS_SCREEN_ID,
        sequence=1,
        source_snapshot_id=snapshot_id,
        fingerprint="focused-input",
        targets=[node],
    )


def _validate_focus_postcondition(
    *,
    runtime,
    public_screen,
    current_snapshot,
) -> None:
    validate_postcondition(
        FocusCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
        make_snapshot(
            make_raw_node(
                rid="w1:0.5",
                text="Search settings",
                editable=True,
                focused=False,
                actions=("focus", "setText"),
            )
        ),
        current_snapshot,
        make_public_screen(
            targets=(
                make_focus_target(
                    label="Search settings",
                    state=(),
                    actions=("focus", "type"),
                ),
            ),
            input_ref=None,
        ),
        public_screen,
        session=runtime,
        focus_context=FocusConfirmationContext(
            request_handle=_REQUEST_HANDLE,
            binding=None,
            resolved_target=None,
        ),
        action_result=_focus_action_result(),
    )


def test_focus_postcondition_accepts_same_ref_focus(tmp_path: Path) -> None:
    current_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
        )
    )
    runtime = build_runtime(
        tmp_path,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    public_screen = _focused_input_screen(
        input_ref=_FOCUS_REF,
        target_ref=_FOCUS_REF,
    )
    install_screen_state(
        runtime,
        snapshot=current_snapshot,
        public_screen=public_screen,
        compiled_screen=_compiled_input_screen(ref=_FOCUS_REF, raw_rid="w1:0.5"),
    )

    _validate_focus_postcondition(
        runtime=runtime,
        public_screen=public_screen,
        current_snapshot=current_snapshot,
    )


def test_focus_postcondition_accepts_current_successor_ref_for_confirmed_raw_target(
    tmp_path: Path,
) -> None:
    current_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
        )
    )
    runtime = build_runtime(
        tmp_path,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    public_screen = _focused_input_screen(input_ref="n2", target_ref="n2")
    install_screen_state(
        runtime,
        snapshot=current_snapshot,
        public_screen=public_screen,
        compiled_screen=_compiled_input_screen(ref="n2", raw_rid="w1:0.5"),
    )

    _validate_focus_postcondition(
        runtime=runtime,
        public_screen=public_screen,
        current_snapshot=current_snapshot,
    )


def test_focus_postcondition_requires_semantic_focus_match() -> None:
    runtime = build_runtime(
        _FOCUS_RUNTIME_ROOT,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    current_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
        )
    )
    public_screen = _focused_input_screen(input_ref="n2", target_ref="n2")
    install_screen_state(
        runtime,
        snapshot=current_snapshot,
        public_screen=public_screen,
        compiled_screen=_compiled_input_screen(ref="n2", raw_rid="w1:0.8"),
    )

    with pytest.raises(DaemonError) as error:
        validate_postcondition(
            FocusCommand(ref=_FOCUS_REF, source_screen_id=_FOCUS_SCREEN_ID),
            make_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search settings",
                    editable=True,
                    focused=False,
                    actions=("focus", "setText"),
                )
            ),
            current_snapshot,
            make_public_screen(
                targets=(
                    make_focus_target(
                        label="Search settings",
                        state=(),
                        actions=("focus", "type"),
                    ),
                ),
                input_ref=None,
            ),
            public_screen,
            session=runtime,
            focus_context=FocusConfirmationContext(
                request_handle=_REQUEST_HANDLE,
                binding=None,
                resolved_target=None,
            ),
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
                resolved_target=ResolvedHandleTarget(handle=_REQUEST_HANDLE),
            ),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "focus_mismatch"


@pytest.mark.parametrize(
    ("input_ref", "compiled_ref", "compiled_raw_rid"),
    [
        (None, "n2", "w1:0.5"),
        (_FOCUS_REF, None, None),
        (_FOCUS_REF, _FOCUS_REF, "w1:0.8"),
        ("n2", None, None),
        ("n2", "n3", "w1:0.5"),
    ],
)
def test_focus_postcondition_fails_closed_without_focus_or_mapping(
    tmp_path: Path,
    input_ref: str | None,
    compiled_ref: str | None,
    compiled_raw_rid: str | None,
) -> None:
    runtime = build_runtime(
        tmp_path,
        screen_sequence=1,
        current_screen_id=_FOCUS_SCREEN_ID,
    )
    current_snapshot = make_snapshot(
        make_raw_node(
            rid="w1:0.5",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
        )
    )
    public_screen = _focused_input_screen(input_ref=input_ref, target_ref=input_ref)
    compiled_screen = (
        None
        if compiled_ref is None or compiled_raw_rid is None
        else _compiled_input_screen(ref=compiled_ref, raw_rid=compiled_raw_rid)
    )
    install_screen_state(
        runtime,
        snapshot=current_snapshot,
        public_screen=public_screen,
        compiled_screen=compiled_screen,
    )

    with pytest.raises(DaemonError) as error:
        _validate_focus_postcondition(
            runtime=runtime,
            public_screen=public_screen,
            current_snapshot=current_snapshot,
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "focus_mismatch"
