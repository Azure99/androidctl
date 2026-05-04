from __future__ import annotations

from pathlib import Path

import pytest

from androidctld.actions.submit_confirmation import (
    SubmitConfirmationContext,
    submit_public_change_is_attributable,
    validate_submit_confirmation,
)
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    ResolvedHandleTarget,
)
from androidctld.errors import DaemonError
from androidctld.refs.models import NodeHandle
from androidctld.semantics.public_models import PublicNode

from .support.runtime import build_runtime
from .support.semantic_screen import (
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
    make_raw_node,
)

_RUNTIME_ROOT = Path("/tmp/androidctl")
_INPUT_REF = "n1"
_REQUEST_HANDLE = NodeHandle(snapshot_id=42, rid="w1:0.5")
_CONFIRMED_HANDLE = NodeHandle(snapshot_id=42, rid="w2:0.7")


def make_submit_target(
    *,
    label: str = "Search",
    state: tuple[str, ...] = (),
    actions: tuple[str, ...] = ("type", "submit"),
) -> PublicNode:
    return make_public_node(
        ref=_INPUT_REF,
        role="input",
        label=label,
        state=state,
        actions=actions,
    )


def make_submit_screen(
    *,
    targets: tuple[PublicNode, ...] | None = None,
    keyboard_visible: bool = False,
):
    return make_contract_screen(
        targets=(make_submit_target(),) if targets is None else targets,
        keyboard_visible=keyboard_visible,
    )


def make_context(
    *,
    request_handle: NodeHandle = _REQUEST_HANDLE,
) -> SubmitConfirmationContext:
    return SubmitConfirmationContext(
        ref=_INPUT_REF,
        request_handle=request_handle,
        binding=None,
    )


def test_submit_confirmation_raises_submit_not_confirmed_without_visible_effect() -> (
    None
):
    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="direct",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            previous_screen=make_submit_screen(),
            public_screen=make_submit_screen(),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"


def test_submit_confirmation_requires_device_side_submit_acceptance() -> None:
    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="direct",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.TIMEOUT,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=False),
                windowless=True,
            ),
            previous_screen=make_submit_screen(),
            public_screen=make_submit_screen(),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "device_submit_not_accepted"


def test_submit_confirmation_accepts_foreground_package_change() -> None:
    outcome = validate_submit_confirmation(
        route_kind="attributed",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            package_name="com.android.settings",
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            package_name="com.google.android.settings.intelligence",
            windowless=True,
        ),
        previous_screen=make_submit_screen(),
        public_screen=make_submit_screen(),
        command_target_handle=_REQUEST_HANDLE,
    )

    assert outcome.status == "unconfirmed"


def test_submit_confirmation_accepts_public_screen_change_on_command_target_line() -> (
    None
):
    outcome = validate_submit_confirmation(
        route_kind="direct",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        previous_screen=make_submit_screen(
            targets=(make_submit_target(state=("focused",)),),
        ),
        public_screen=make_submit_screen(
            targets=(make_submit_target(label="Search results"),),
        ),
        command_target_handle=_REQUEST_HANDLE,
    )

    assert outcome.status == "publicChange"


def test_submit_confirmation_rejects_public_change_without_previous_screen() -> None:
    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="direct",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            previous_screen=None,
            public_screen=make_submit_screen(
                targets=(make_submit_target(label="Search results"),),
            ),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "target_still_focused_editable"


def test_submit_public_change_ignores_app_and_keyboard_only_changes() -> None:
    previous_screen = make_submit_screen()

    assert not submit_public_change_is_attributable(
        previous_screen,
        make_contract_screen(
            targets=(make_submit_target(),),
            package_name="com.example.other",
        ),
    )
    assert not submit_public_change_is_attributable(
        previous_screen,
        make_submit_screen(keyboard_visible=True),
    )


def test_submit_public_change_ignores_focus_actions_and_submit_refs_only() -> None:
    submit_button = make_public_node(
        ref="n2",
        role="button",
        label="Search",
        state=(),
        actions=("tap",),
    )
    previous_input = make_submit_target(
        state=("focused",),
        actions=("tap", "type", "submit"),
    ).model_copy(update={"submit_refs": ("n2",)})
    current_input = make_submit_target(actions=("focus",))
    previous_screen = make_submit_screen(targets=(previous_input, submit_button))
    current_screen = make_submit_screen(
        targets=(
            current_input,
            submit_button.model_copy(update={"state": ("focused",)}),
        )
    )

    assert not submit_public_change_is_attributable(previous_screen, current_screen)


def test_submit_confirmation_prefers_confirmed_target_handle_over_request_context() -> (
    None
):
    validate_submit_confirmation(
        session=build_runtime(_RUNTIME_ROOT),
        route_kind="direct",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
            resolved_target=ResolvedHandleTarget(handle=_REQUEST_HANDLE),
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            make_raw_node(rid="w2:0.7", text="wifi", focused=False),
            windowless=True,
        ),
        previous_screen=make_submit_screen(),
        public_screen=make_submit_screen(),
        context=make_context(),
        command_target_handle=_CONFIRMED_HANDLE,
    )


def test_submit_confirmation_does_not_reanchor_disappeared_handle_to_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "androidctld.actions.submit_confirmation.fingerprint_rematch_confirmation_node",
        lambda session, snapshot, context: make_raw_node(
            rid="w1:0.5",
            text="wifi",
            focused=True,
        ),
    )

    validate_submit_confirmation(
        session=build_runtime(_RUNTIME_ROOT),
        route_kind="direct",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w2:0.7", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        previous_screen=make_submit_screen(),
        public_screen=make_submit_screen(),
        context=make_context(),
        command_target_handle=_CONFIRMED_HANDLE,
    )


def test_submit_confirmation_accepts_target_disappearance_without_diff_proof() -> None:
    outcome = validate_submit_confirmation(
        route_kind="direct",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(windowless=True),
        previous_screen=make_submit_screen(),
        public_screen=make_contract_screen(),
        command_target_handle=_REQUEST_HANDLE,
    )

    assert outcome.status == "targetGone"


def test_submit_confirmation_does_not_treat_rid_churn_as_target_disappearance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rematched = make_raw_node(rid="w2:0.7", text="wifi", focused=True)
    monkeypatch.setattr(
        "androidctld.actions.submit_confirmation.fingerprint_rematch_confirmation_node",
        lambda session, snapshot, context: rematched,
    )

    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            session=build_runtime(_RUNTIME_ROOT),
            route_kind="direct",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(rematched, windowless=True),
            previous_screen=make_submit_screen(),
            public_screen=make_submit_screen(),
            context=make_context(),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "target_still_focused_editable"


def test_submit_confirmation_accepts_focus_loss_on_same_target() -> None:
    outcome = validate_submit_confirmation(
        route_kind="direct",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=False),
            windowless=True,
        ),
        previous_screen=make_submit_screen(),
        public_screen=make_submit_screen(),
        command_target_handle=_REQUEST_HANDLE,
    )

    assert outcome.status == "sameTarget"


def test_submit_confirmation_rejects_attributed_focus_loss_without_public_change() -> (
    None
):
    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="attributed",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=False),
                windowless=True,
            ),
            previous_screen=make_submit_screen(),
            public_screen=make_submit_screen(),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "attributed_submit_blur_only"


def test_submit_confirmation_rejects_attributed_blur_only_public_focus_change() -> None:
    submit_button = make_public_node(
        ref="n2",
        role="button",
        label="Search",
        state=(),
        actions=("tap",),
    )
    previous_input = make_submit_target(
        state=("focused",),
        actions=("tap", "type", "submit"),
    ).model_copy(update={"submit_refs": ("n2",)})
    current_input = make_submit_target(actions=("focus",))

    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="attributed",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search",
                    editable=False,
                    focusable=False,
                    focused=False,
                ),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(
                    rid="w1:0.5",
                    text="Search",
                    editable=False,
                    focusable=False,
                    focused=True,
                ),
                windowless=True,
            ),
            previous_screen=make_submit_screen(
                targets=(previous_input, submit_button),
            ),
            public_screen=make_submit_screen(
                targets=(
                    current_input,
                    submit_button.model_copy(update={"state": ("focused",)}),
                ),
            ),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "attributed_submit_blur_only"


def test_submit_confirmation_public_change_wins_over_attributed_focus_loss() -> None:
    outcome = validate_submit_confirmation(
        route_kind="attributed",
        action_result=ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
        ),
        previous_snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=True),
            windowless=True,
        ),
        snapshot=make_contract_snapshot(
            make_raw_node(rid="w1:0.5", text="wifi", focused=False),
            windowless=True,
        ),
        previous_screen=make_submit_screen(
            targets=(make_submit_target(state=("focused",)),),
        ),
        public_screen=make_submit_screen(
            targets=(make_submit_target(label="Search results"),),
        ),
        command_target_handle=_REQUEST_HANDLE,
    )

    assert outcome.status == "publicChange"


def test_submit_confirmation_rejects_still_focused_editable_target_after_refresh() -> (
    None
):
    with pytest.raises(DaemonError) as error:
        validate_submit_confirmation(
            route_kind="direct",
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
            previous_snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            snapshot=make_contract_snapshot(
                make_raw_node(rid="w1:0.5", text="wifi", focused=True),
                windowless=True,
            ),
            previous_screen=make_submit_screen(),
            public_screen=make_submit_screen(),
            command_target_handle=_REQUEST_HANDLE,
        )

    assert error.value.code == "SUBMIT_NOT_CONFIRMED"
    assert error.value.details["reason"] == "target_still_focused_editable"
