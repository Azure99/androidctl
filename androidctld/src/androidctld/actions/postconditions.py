"""Post-action validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from androidctld.actions.focus_confirmation import (
    FocusConfirmationContext,
    FocusConfirmationOutcome,
    validate_focus_confirmation,
)
from androidctld.app_targets import AppTargetMatch, require_app_target_match
from androidctld.commands.command_models import (
    ActionCommand,
    FocusCommand,
    LongTapCommand,
    OpenCommand,
    ScrollCommand,
)
from androidctld.commands.open_targets import OpenAppTarget, OpenUrlTarget
from androidctld.commands.results import screen_changed
from androidctld.device.types import ActionPerformResult
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import current_compiled_screen
from androidctld.semantics.public_models import (
    PublicNode,
    PublicScreen,
    iter_public_nodes,
)
from androidctld.snapshots.models import RawSnapshot


@dataclass(frozen=True)
class PostconditionOutcome:
    app_match: AppTargetMatch | None = None
    focus_confirmation: FocusConfirmationOutcome | None = None


@dataclass(frozen=True)
class RefActionPostconditionContext:
    target_ref: str | None
    baseline_screen: PublicScreen | None
    baseline_target: PublicNode | None


def validate_postcondition(
    command: ActionCommand,
    previous_snapshot: RawSnapshot | None,
    snapshot: RawSnapshot,
    previous_screen: PublicScreen | None,
    public_screen: PublicScreen,
    *,
    session: WorkspaceRuntime,
    focus_context: FocusConfirmationContext | None,
    action_result: ActionPerformResult,
    ref_context: RefActionPostconditionContext | None = None,
) -> PostconditionOutcome:
    if isinstance(command, FocusCommand):
        assert focus_context is not None
        focus_confirmation = validate_focus_confirmation(
            session=session,
            previous_snapshot=previous_snapshot,
            snapshot=snapshot,
            context=FocusConfirmationContext(
                request_handle=focus_context.request_handle,
                binding=focus_context.binding,
                resolved_target=action_result.resolved_target,
            ),
        )
        if not _focus_postcondition_matches(
            session=session,
            public_screen=public_screen,
            confirmation=focus_confirmation,
        ):
            raise DaemonError(
                code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
                message="focus did not land on the requested input target",
                retryable=True,
                details={
                    "reason": "focus_mismatch",
                    "ref": command.ref,
                    "focusedInputRef": public_screen.surface.focus.input_ref,
                },
                http_status=200,
            )
        return PostconditionOutcome(focus_confirmation=focus_confirmation)
    if isinstance(command, ScrollCommand):
        _validate_scroll_confirmation(
            command,
            context=_ref_context_or_source_ref(
                command.ref,
                previous_screen=previous_screen,
                context=ref_context,
            ),
            public_screen=public_screen,
        )
        return PostconditionOutcome()
    if isinstance(command, LongTapCommand):
        _validate_long_tap_confirmation(
            command,
            context=_ref_context_or_source_ref(
                command.ref,
                previous_screen=previous_screen,
                context=ref_context,
            ),
            public_screen=public_screen,
        )
        return PostconditionOutcome()
    if not isinstance(command, OpenCommand):
        return PostconditionOutcome()
    if isinstance(command.target, OpenAppTarget):
        match = require_app_target_match(
            command.target.package_name,
            snapshot.package_name,
        )
        return PostconditionOutcome(app_match=match)
    assert isinstance(command.target, OpenUrlTarget)
    _validate_open_url_navigation(
        command.target,
        previous_snapshot=previous_snapshot,
        snapshot=snapshot,
        previous_screen=previous_screen,
        public_screen=public_screen,
    )
    return PostconditionOutcome()


def _focus_postcondition_matches(
    *,
    session: WorkspaceRuntime,
    public_screen: PublicScreen,
    confirmation: FocusConfirmationOutcome,
) -> bool:
    focused_ref = public_screen.surface.focus.input_ref
    if focused_ref is None or not _public_ref_exists(public_screen, focused_ref):
        return False
    compiled_screen = current_compiled_screen(session)
    if compiled_screen is None:
        return False
    if compiled_screen.source_snapshot_id != confirmation.target_handle.snapshot_id:
        return False
    focused_node = compiled_screen.focused_input_node()
    focused_input_ref = compiled_screen.focused_input_ref()
    return (
        focused_node is not None
        and focused_input_ref == focused_ref
        and focused_node.raw_rid == confirmation.target_handle.rid
    )


def _public_ref_exists(public_screen: PublicScreen, ref: str) -> bool:
    for group in public_screen.groups:
        for node in iter_public_nodes(group.nodes):
            if node.ref == ref:
                return True
    return False


def _ref_context_or_source_ref(
    ref: str,
    *,
    previous_screen: PublicScreen | None,
    context: RefActionPostconditionContext | None,
) -> RefActionPostconditionContext:
    if context is not None:
        return context
    return RefActionPostconditionContext(
        target_ref=ref,
        baseline_screen=previous_screen,
        baseline_target=(
            None
            if previous_screen is None
            else _public_node_by_ref(previous_screen, ref)
        ),
    )


def _validate_scroll_confirmation(
    command: ScrollCommand,
    *,
    context: RefActionPostconditionContext,
    public_screen: PublicScreen,
) -> None:
    previous_target = context.baseline_target
    current_target = _current_ref_context_target(public_screen, context)
    if (
        previous_target is not None
        and current_target is not None
        and previous_target.role == "scroll-container"
        and current_target.role == "scroll-container"
        and "scroll" in previous_target.actions
        and "scroll" in current_target.actions
        and _scroll_content_signature(previous_target)
        != _scroll_content_signature(current_target)
    ):
        return
    if (
        previous_target is not None
        and current_target is not None
        and previous_target.role == "scroll-container"
        and current_target.role == "scroll-container"
        and "scroll" in previous_target.actions
        and "scroll" in current_target.actions
        and _scroll_direction_change_confirms(
            command.direction,
            previous_target.scroll_directions,
            current_target.scroll_directions,
        )
    ):
        return
    raise DaemonError(
        code=DaemonErrorCode.ACTION_NOT_CONFIRMED,
        message="scroll was not confirmed on the refreshed screen",
        retryable=True,
        details=_ref_action_error_details(
            command,
            context,
            reason="scroll_target_content_unchanged",
            direction=command.direction,
        ),
        http_status=200,
    )


def _public_node_by_ref(public_screen: PublicScreen, ref: str) -> PublicNode | None:
    for group in public_screen.groups:
        for node in iter_public_nodes(group.nodes):
            if node.ref == ref:
                return node
    return None


def _validate_long_tap_confirmation(
    command: LongTapCommand,
    *,
    context: RefActionPostconditionContext,
    public_screen: PublicScreen,
) -> None:
    if context.baseline_screen is not None:
        previous_screen = context.baseline_screen
        previous_target = context.baseline_target
        if previous_target is not None:
            if _long_tap_context_or_dialog_changed(
                previous_screen,
                public_screen,
            ) or _long_tap_transient_changed(previous_screen, public_screen):
                return
            current_target = _current_ref_context_target(public_screen, context)
            if current_target is not None and _same_target_long_tap_feedback_changed(
                previous_target,
                current_target,
            ):
                return
    raise DaemonError(
        code=DaemonErrorCode.ACTION_NOT_CONFIRMED,
        message="long-tap was not confirmed on the refreshed screen",
        retryable=True,
        details=_ref_action_error_details(
            command,
            context,
            reason="long_tap_feedback_not_observed",
        ),
        http_status=200,
    )


def _current_ref_context_target(
    public_screen: PublicScreen,
    context: RefActionPostconditionContext,
) -> PublicNode | None:
    if context.target_ref is None:
        return None
    return _public_node_by_ref(public_screen, context.target_ref)


def _ref_action_error_details(
    command: ScrollCommand | LongTapCommand,
    context: RefActionPostconditionContext,
    **details: object,
) -> dict[str, object]:
    payload = {
        **details,
        "ref": command.ref,
    }
    if context.target_ref is not None and context.target_ref != command.ref:
        payload["targetRef"] = context.target_ref
    return payload


def _long_tap_context_or_dialog_changed(
    previous_screen: PublicScreen,
    public_screen: PublicScreen,
) -> bool:
    return any(
        _long_tap_context_dialog_group_signature(previous_screen, group_name)
        != _long_tap_context_dialog_group_signature(public_screen, group_name)
        for group_name in ("context", "dialog")
    )


def _long_tap_transient_changed(
    previous_screen: PublicScreen,
    public_screen: PublicScreen,
) -> bool:
    return _transient_signature(previous_screen) != _transient_signature(public_screen)


def _long_tap_context_dialog_group_signature(
    screen: PublicScreen,
    group_name: str,
) -> tuple[object, ...]:
    for group in screen.groups:
        if group.name == group_name:
            return tuple(
                _long_tap_context_dialog_feedback_signature(node)
                for node in group.nodes
            )
    return ()


def _long_tap_context_dialog_feedback_signature(
    node: PublicNode,
) -> tuple[object, ...]:
    if node.kind == "text":
        return (
            "text",
            node.text,
            node.value,
        )
    return (
        node.kind,
        node.role,
        node.text,
        node.value,
        tuple(sorted(node.state)),
        tuple(sorted(node.actions)),
        tuple(
            _long_tap_context_dialog_feedback_signature(child)
            for child in node.children
        ),
    )


def _transient_signature(screen: PublicScreen) -> tuple[object, ...]:
    return tuple((item.kind, item.text) for item in screen.transient)


def _same_target_long_tap_feedback_changed(
    previous_target: PublicNode,
    current_target: PublicNode,
) -> bool:
    if tuple(sorted(previous_target.state)) != tuple(sorted(current_target.state)):
        return True
    if tuple(sorted(previous_target.actions)) != tuple(sorted(current_target.actions)):
        return True
    return _children_feedback_signature(
        previous_target.children
    ) != _children_feedback_signature(current_target.children)


def _children_feedback_signature(nodes: tuple[PublicNode, ...]) -> tuple[object, ...]:
    return tuple(_child_feedback_signature(node) for node in nodes)


def _child_feedback_signature(node: PublicNode) -> tuple[object, ...]:
    return (
        node.text,
        node.value,
        tuple(sorted(node.state)),
        tuple(sorted(node.actions)),
        _children_feedback_signature(node.children),
    )


def _scroll_content_signature(node: PublicNode) -> tuple[object, ...]:
    return tuple(_scroll_child_content_signature(child) for child in node.children)


def _scroll_direction_change_confirms(
    direction: str,
    previous_directions: tuple[str, ...],
    current_directions: tuple[str, ...],
) -> bool:
    previous = set(previous_directions)
    current = set(current_directions)
    if previous == current:
        return False
    return bool(_opposite_scroll_directions(direction).intersection(current))


def _opposite_scroll_directions(direction: str) -> set[str]:
    if direction == "down":
        return {"up", "backward"}
    if direction in {"up", "backward"}:
        return {"down"}
    if direction == "left":
        return {"right"}
    if direction == "right":
        return {"left"}
    return set()


def _scroll_child_content_signature(node: PublicNode) -> tuple[object, ...]:
    return (
        node.text,
        node.value,
        tuple(_scroll_child_content_signature(child) for child in node.children),
    )


def _requires_visible_open_url_navigation(target: OpenUrlTarget) -> bool:
    return urlsplit(target.url).scheme.lower() in {"http", "https"}


def _validate_open_url_navigation(
    target: OpenUrlTarget,
    *,
    previous_snapshot: RawSnapshot | None,
    snapshot: RawSnapshot,
    previous_screen: PublicScreen | None,
    public_screen: PublicScreen,
) -> None:
    if not _requires_visible_open_url_navigation(target):
        return
    if previous_snapshot is None:
        if screen_changed(previous_screen, public_screen):
            return
        raise DaemonError(
            code=DaemonErrorCode.OPEN_FAILED,
            message="open url did not produce a visible navigation change",
            retryable=True,
            details={
                "packageName": snapshot.package_name,
                "activityName": snapshot.activity_name,
            },
            http_status=200,
        )
    previous_package = previous_snapshot.package_name
    previous_activity = previous_snapshot.activity_name
    current_package = snapshot.package_name
    current_activity = snapshot.activity_name
    if current_package and current_package != previous_package:
        return
    if current_activity and current_activity != previous_activity:
        return
    if screen_changed(previous_screen, public_screen):
        return
    if not current_package:
        raise DaemonError(
            code=DaemonErrorCode.OPEN_FAILED,
            message="open url did not produce a visible foreground change",
            retryable=True,
            details={"packageName": current_package},
            http_status=200,
        )
    raise DaemonError(
        code=DaemonErrorCode.OPEN_FAILED,
        message="open url did not produce a visible navigation change",
        retryable=True,
        details={
            "packageName": current_package,
            "activityName": current_activity,
        },
        http_status=200,
    )
