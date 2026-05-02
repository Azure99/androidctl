from __future__ import annotations

from pathlib import Path

import pytest

from androidctld.actions.capabilities import (
    ensure_command_supported,
    validate_ref_action,
)
from androidctld.actions.type_confirmation import (
    TypeConfirmationContext,
    validate_type_confirmation,
)
from androidctld.commands.command_models import (
    FocusCommand,
    SubmitCommand,
    TapCommand,
    TypeCommand,
)
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    DeviceCapabilities,
    ResolvedNoneTarget,
)
from androidctld.errors import DaemonError
from androidctld.refs.models import NodeHandle
from androidctld.semantics.public_models import PublicNode

from .support.runtime import build_runtime, install_screen_state
from .support.semantic_screen import (
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
    make_raw_node,
)

_RUNTIME_ROOT = Path("/tmp/androidctl")
_SCREEN_ID = "screen-00001"
_REQUEST_HANDLE = NodeHandle(snapshot_id=42, rid="w1:0.5")
_TYPE_REF = "n1"


def make_screen_target(
    *,
    ref: str,
    role: str,
    label: str,
    state: tuple[str, ...] = (),
    actions: tuple[str, ...],
) -> PublicNode:
    return make_public_node(
        ref=ref,
        role=role,
        label=label,
        state=state,
        actions=actions,
    )


def make_type_context(*, ref: str = _TYPE_REF) -> TypeConfirmationContext:
    return TypeConfirmationContext(
        ref=ref,
        request_handle=_REQUEST_HANDLE,
        binding=None,
    )


def make_type_command(
    *,
    ref: str = _TYPE_REF,
    source_screen_id: str = _SCREEN_ID,
    text: str = "wifi",
) -> TypeCommand:
    return TypeCommand(
        ref=ref,
        source_screen_id=source_screen_id,
        text=text,
    )


def make_submit_command(
    *,
    ref: str = _TYPE_REF,
    source_screen_id: str = _SCREEN_ID,
) -> SubmitCommand:
    return SubmitCommand(
        ref=ref,
        source_screen_id=source_screen_id,
    )


def test_capability_mismatch_surfaces_public_action_kind_for_focus() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=[],
    )

    with pytest.raises(DaemonError) as error:
        ensure_command_supported(
            runtime,
            FocusCommand(ref=_TYPE_REF, source_screen_id=_SCREEN_ID),
        )

    assert error.value.code == "DEVICE_AGENT_CAPABILITY_MISMATCH"
    assert error.value.details["missingActionKinds"] == ["focus"]


def test_submit_capability_check_is_deferred_until_route_selection() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=[],
    )

    ensure_command_supported(
        runtime,
        make_submit_command(ref=_TYPE_REF, source_screen_id=_SCREEN_ID),
    )


def test_validate_ref_action_requires_matching_focused_input_for_type() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    install_screen_state(
        runtime,
        snapshot=make_contract_snapshot(
            make_raw_node(
                rid="w1:0.5",
                window_id="w1",
                text="Search",
                class_name="android.widget.EditText",
                resource_id=None,
                bounds=(0, 0, 100, 40),
                editable=True,
                focusable=True,
                actions=("focus", "setText"),
            ),
            windowless=True,
        ),
        public_screen=make_contract_screen(
            targets=(
                make_screen_target(
                    ref="n3",
                    role="input",
                    label="Search",
                    actions=("type",),
                ),
            ),
            input_ref=None,
        ),
    )

    with pytest.raises(DaemonError) as error:
        validate_ref_action(
            runtime,
            make_type_command(ref="n3", text="hello"),
        )

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "focus_mismatch"


def test_validate_ref_action_rejects_blocked_background_target() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    install_screen_state(
        runtime,
        snapshot=make_contract_snapshot(
            make_raw_node(
                rid="w1:0.5",
                window_id="w1",
                text="Search",
                class_name="android.widget.EditText",
                resource_id=None,
                bounds=(0, 0, 100, 40),
                editable=True,
                focusable=True,
                actions=("focus", "setText"),
            ),
            windowless=True,
        ),
        public_screen=make_contract_screen(
            targets=(
                make_screen_target(
                    ref="n2",
                    role="button",
                    label="Allow",
                    actions=("tap",),
                ),
            ),
            dialog=(
                make_screen_target(
                    ref="n9",
                    role="button",
                    label="Confirm",
                    actions=("tap",),
                ),
            ),
            blocking_group="dialog",
        ),
    )

    with pytest.raises(DaemonError) as error:
        validate_ref_action(
            runtime,
            TapCommand(ref="n2", source_screen_id=_SCREEN_ID),
        )

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_dialog"


def test_validate_ref_action_allows_keyboard_blocked_focused_input_type() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    target = make_screen_target(
        ref=_TYPE_REF,
        role="input",
        label="Search settings",
        state=("focused",),
        actions=("type",),
    )
    install_screen_state(
        runtime,
        snapshot=make_contract_snapshot(
            make_raw_node(
                rid="w1:input",
                window_id="w1",
                text="Search settings",
                class_name="android.widget.EditText",
                bounds=(0, 0, 100, 40),
                editable=True,
                focusable=True,
                focused=True,
                actions=("focus", "setText", "submit", "click"),
            ),
            windowless=True,
        ),
        public_screen=make_contract_screen(
            targets=(target,),
            input_ref=target.ref,
            blocking_group="keyboard",
            keyboard_visible=True,
        ),
    )

    bound = validate_ref_action(
        runtime,
        make_type_command(ref=target.ref, source_screen_id=_SCREEN_ID),
    )

    assert bound is not None
    assert bound.node.ref == target.ref


def test_validate_ref_action_rejects_keyboard_blocked_other_input_type() -> None:
    runtime = build_runtime(_RUNTIME_ROOT)
    focused = make_screen_target(
        ref="n1",
        role="input",
        label="Search settings",
        state=("focused",),
        actions=("type",),
    )
    other = make_screen_target(
        ref="n2",
        role="input",
        label="Other input",
        actions=("type",),
    )
    install_screen_state(
        runtime,
        snapshot=make_contract_snapshot(windowless=True),
        public_screen=make_contract_screen(
            targets=(focused, other),
            input_ref=focused.ref,
            blocking_group="keyboard",
            keyboard_visible=True,
        ),
    )

    with pytest.raises(DaemonError) as error:
        validate_ref_action(
            runtime,
            make_type_command(ref=other.ref, source_screen_id=_SCREEN_ID),
        )

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_keyboard"
    assert error.value.details["ref"] == other.ref


def test_validate_type_confirmation_plain_type_does_not_drift() -> None:
    with pytest.raises(DaemonError) as error:
        validate_type_confirmation(
            session=build_runtime(_RUNTIME_ROOT),
            command=make_type_command(),
            snapshot=make_contract_snapshot(
                make_raw_node(
                    rid="w1:0.9",
                    window_id="w1",
                    text="wifi",
                    class_name="android.widget.EditText",
                    resource_id=None,
                    bounds=(0, 0, 100, 40),
                    editable=True,
                    focusable=True,
                    focused=True,
                    actions=("focus", "setText"),
                ),
                windowless=True,
            ),
            context=make_type_context(),
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
                resolved_target=ResolvedNoneTarget(),
            ),
        )

    assert error.value.code == "TYPE_NOT_CONFIRMED"
    assert error.value.message == "typed text was not confirmed on the refreshed screen"
    assert error.value.details["confirmationStrategy"] == (
        "resolvedTarget>requestTarget>reusedRef>fingerprintRematch"
    )
    stale_strategy = "focused" "Fallback"
    assert stale_strategy not in error.value.details["confirmationStrategy"]
