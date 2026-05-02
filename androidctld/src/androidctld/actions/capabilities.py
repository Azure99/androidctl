"""Command capability validation helpers."""

from __future__ import annotations

from typing import NamedTuple

from androidctld.actions.focused_input_admissibility import (
    blocked_by_group_fields,
    focus_mismatch_fields,
    keyboard_blocker_allows_public_type,
    keyboard_blocker_allows_semantic_type,
    public_focused_input_ref,
    public_node_is_focused_input,
    semantic_focused_input_ref,
    semantic_node_is_focused_input,
)
from androidctld.commands.command_models import (
    ActionCommand,
    FocusCommand,
    GlobalCommand,
    LongTapCommand,
    OpenCommand,
    RefBoundActionCommand,
    ScreenshotCommand,
    ScrollCommand,
    SubmitCommand,
    TapCommand,
    TypeCommand,
    is_ref_bound_action_command,
)
from androidctld.commands.open_targets import (
    OpenAppTarget,
    OpenUrlTarget,
    validate_open_target,
)
from androidctld.device.action_models import (
    GlobalActionRequest,
    LaunchAppActionRequest,
    LongTapActionRequest,
    NodeActionRequest,
    OpenUrlActionRequest,
    ScrollActionRequest,
    SwipeActionRequest,
    TapActionRequest,
    TypeActionRequest,
    required_action_kind_for_request,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import CommandKind
from androidctld.refs.models import NodeHandle
from androidctld.refs.repair import failed_repair_decision, ref_repair_error
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_artifacts,
    current_compiled_screen,
    current_public_screen,
)
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.semantics.public_models import PublicNode, PublicScreen


class BoundRefNode(NamedTuple):
    node: PublicNode
    group_name: str


class BoundSemanticNode(NamedTuple):
    node: SemanticNode
    blocking_group: str | None


def ensure_command_supported(
    session: WorkspaceRuntime, command: ActionCommand | ScreenshotCommand
) -> None:
    capabilities = session.device_capabilities
    if capabilities is None:
        return
    if isinstance(command, ScreenshotCommand):
        if capabilities.supports_screenshot:
            return
        raise unsupported_command_capability(
            command=command.kind,
            missing_capabilities=["supportsScreenshot"],
        )
    if isinstance(command, SubmitCommand):
        return
    required_action_kind = required_action_kind_for(command)
    if capabilities.supports_action(required_action_kind):
        return
    raise unsupported_command_capability(
        command=command.kind,
        missing_action_kinds=_public_missing_action_kinds(
            command=command.kind,
            missing_action_kinds=[required_action_kind],
        ),
    )


def ensure_action_request_supported(
    session: WorkspaceRuntime,
    *,
    command: CommandKind,
    request: (
        TapActionRequest
        | LongTapActionRequest
        | TypeActionRequest
        | NodeActionRequest
        | ScrollActionRequest
        | SwipeActionRequest
        | GlobalActionRequest
        | LaunchAppActionRequest
        | OpenUrlActionRequest
    ),
) -> None:
    capabilities = session.device_capabilities
    if capabilities is None:
        return
    required_action_kind = required_action_kind_for(request)
    if capabilities.supports_action(required_action_kind):
        return
    raise unsupported_command_capability(
        command=command,
        missing_action_kinds=_public_missing_action_kinds(
            command=command,
            missing_action_kinds=[required_action_kind],
        ),
    )


def validate_ref_action(
    session: WorkspaceRuntime,
    command: RefBoundActionCommand,
) -> BoundRefNode | None:
    if isinstance(command, SubmitCommand):
        return None
    screen = current_public_screen(session)
    if screen is None:
        raise DaemonError(
            code=DaemonErrorCode.SCREEN_NOT_READY,
            message="screen is not ready yet",
            retryable=False,
            details={"workspaceRoot": session.workspace_root.as_posix()},
            http_status=200,
        )
    if command.source_screen_id != screen.screen_id:
        return None
    bound = _find_bound_ref_node(screen, command.ref)
    if bound is None:
        raise DaemonError(
            code=DaemonErrorCode.REF_RESOLUTION_FAILED,
            message="ref does not exist on the current screen",
            retryable=False,
            details={"ref": command.ref},
            http_status=200,
        )
    _ensure_not_blocked(screen, bound, action=command.kind.value)
    _ensure_action_exposed(bound.node, command.kind.value)
    if isinstance(command, ScrollCommand):
        _ensure_scroll_direction_exposed(bound.node, command.direction)
    if isinstance(command, (FocusCommand, TypeCommand)):
        _ensure_input_capable(bound.node, action=command.kind.value)
    if isinstance(command, TypeCommand):
        _ensure_matching_focused_input(screen, node=bound.node)
    return bound


def validate_action_semantics(
    session: WorkspaceRuntime,
    command: ActionCommand,
) -> None:
    if not is_ref_bound_action_command(command):
        return
    validate_ref_action(session, command)


def validate_resolved_ref_action(
    session: WorkspaceRuntime,
    command: RefBoundActionCommand,
    request_handle: NodeHandle | None,
) -> None:
    if isinstance(command, SubmitCommand):
        return
    screen = current_public_screen(session)
    if screen is None or request_handle is None:
        return
    if command.source_screen_id == screen.screen_id:
        return
    compiled_screen = current_compiled_screen(session)
    if compiled_screen is None:
        raise DaemonError(
            code=DaemonErrorCode.SCREEN_NOT_READY,
            message="screen is not ready yet",
            retryable=False,
            details={"workspaceRoot": session.workspace_root.as_posix()},
            http_status=200,
        )
    bound = _find_repaired_target(
        session,
        compiled_screen,
        request_handle,
        command.ref,
        source_screen_id=command.source_screen_id,
    )
    _ensure_not_blocked_semantic(
        bound,
        action=command.kind.value,
        compiled_screen=compiled_screen,
    )
    _ensure_action_exposed_semantic(bound.node, action=command.kind.value)
    if isinstance(command, ScrollCommand):
        _ensure_scroll_direction_exposed_semantic(bound.node, command.direction)
    if isinstance(command, (FocusCommand, TypeCommand)):
        _ensure_input_capable_semantic(bound.node, action=command.kind.value)
    if isinstance(command, TypeCommand):
        _ensure_matching_focused_input_semantic(
            compiled_screen,
            target=bound.node,
            ref=command.ref,
        )


def required_action_kind_for(
    command: (
        ActionCommand
        | TapActionRequest
        | LongTapActionRequest
        | TypeActionRequest
        | NodeActionRequest
        | ScrollActionRequest
        | SwipeActionRequest
        | GlobalActionRequest
        | LaunchAppActionRequest
        | OpenUrlActionRequest
        | OpenAppTarget
        | OpenUrlTarget
    ),
) -> str:
    if isinstance(command, (OpenAppTarget, OpenUrlTarget)):
        return validate_open_target(command).required_action_kind
    if isinstance(
        command,
        (
            TapActionRequest,
            LongTapActionRequest,
            TypeActionRequest,
            NodeActionRequest,
            ScrollActionRequest,
            SwipeActionRequest,
            GlobalActionRequest,
            LaunchAppActionRequest,
            OpenUrlActionRequest,
        ),
    ):
        return required_action_kind_for_request(command)
    if isinstance(command, OpenCommand):
        return required_action_kind_for(command.target)
    if isinstance(command, TapCommand):
        return "tap"
    if isinstance(command, LongTapCommand):
        return "longTap"
    if isinstance(command, TypeCommand):
        return "type"
    if isinstance(command, (FocusCommand, SubmitCommand)):
        return "node"
    if isinstance(command, ScrollCommand):
        return "scroll"
    if isinstance(command, GlobalCommand):
        return "global"
    raise TypeError(f"unsupported action capability input: {type(command)!r}")


def unsupported_command_capability(
    *,
    command: CommandKind,
    missing_capabilities: list[str] | None = None,
    missing_action_kinds: list[str] | None = None,
) -> DaemonError:
    missing_capabilities = missing_capabilities or []
    missing_action_kinds = missing_action_kinds or []
    return DaemonError(
        code=DaemonErrorCode.DEVICE_AGENT_CAPABILITY_MISMATCH,
        message=f"{command.value} is not supported by the connected device agent",
        retryable=False,
        details={
            "command": command.value,
            "missingCapabilities": missing_capabilities,
            "missingActionKinds": missing_action_kinds,
        },
        http_status=200,
    )


def _public_missing_action_kinds(
    *, command: CommandKind, missing_action_kinds: list[str]
) -> list[str]:
    if command is CommandKind.FOCUS:
        return [
            "focus" if action_kind == "node" else action_kind
            for action_kind in missing_action_kinds
        ]
    if command is CommandKind.SUBMIT:
        return [
            "submit" if action_kind == "node" else action_kind
            for action_kind in missing_action_kinds
        ]
    return missing_action_kinds


def _find_bound_ref_node(screen: PublicScreen, ref: str) -> BoundRefNode | None:
    for group in screen.groups:
        for node in group.nodes:
            if node.ref == ref:
                return BoundRefNode(node=node, group_name=group.name)
    return None


def _ensure_not_blocked(
    screen: PublicScreen,
    bound: BoundRefNode,
    *,
    action: str,
) -> None:
    blocking_group = screen.surface.blocking_group
    if blocking_group is None or bound.group_name == blocking_group:
        return
    if keyboard_blocker_allows_public_type(
        blocking_group=blocking_group,
        action=action,
        screen=screen,
        node=bound.node,
    ):
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_BLOCKED,
        message="target is blocked on the current screen",
        retryable=False,
        details=blocked_by_group_fields(
            blocking_group=blocking_group,
            ref=bound.node.ref,
        ),
        http_status=200,
    )


def _ensure_not_blocked_semantic(
    bound: BoundSemanticNode,
    *,
    action: str,
    compiled_screen: CompiledScreen,
) -> None:
    blocking_group = bound.blocking_group
    if blocking_group is None or bound.node.group == blocking_group:
        return
    if keyboard_blocker_allows_semantic_type(
        blocking_group=blocking_group,
        action=action,
        screen=compiled_screen,
        node=bound.node,
    ):
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_BLOCKED,
        message="target is blocked on the current screen",
        retryable=False,
        details=blocked_by_group_fields(
            blocking_group=blocking_group,
            ref=bound.node.ref or None,
        ),
        http_status=200,
    )


def _ensure_action_exposed(node: PublicNode, action: str) -> None:
    if action in node.actions:
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=f"{action} is not available for the requested target",
        retryable=False,
        details={
            "reason": "action_not_exposed",
            "ref": node.ref,
            "action": action,
        },
        http_status=200,
    )


def _ensure_action_exposed_semantic(node: SemanticNode, *, action: str) -> None:
    if action in node.actions:
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=f"{action} is not available for the requested target",
        retryable=False,
        details={
            "reason": "action_not_exposed",
            "ref": node.ref or None,
            "action": action,
        },
        http_status=200,
    )


def _ensure_scroll_direction_exposed(node: PublicNode, direction: str) -> None:
    if direction in node.scroll_directions:
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=(
            f"scroll direction '{direction}' is not available for the requested target"
        ),
        retryable=False,
        details={
            "reason": "scroll_direction_not_exposed",
            "ref": node.ref,
            "direction": direction,
            "scrollDirections": list(node.scroll_directions),
        },
        http_status=200,
    )


def _ensure_scroll_direction_exposed_semantic(
    node: SemanticNode, direction: str
) -> None:
    if direction in node.scroll_directions:
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=(
            f"scroll direction '{direction}' is not available for the requested target"
        ),
        retryable=False,
        details={
            "reason": "scroll_direction_not_exposed",
            "ref": node.ref or None,
            "direction": direction,
            "scrollDirections": list(node.scroll_directions),
        },
        http_status=200,
    )


def _ensure_input_capable(node: PublicNode, *, action: str) -> None:
    if node.role == "input":
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=f"{action} requires an input-capable target",
        retryable=False,
        details={
            "reason": "not_input_capable",
            "ref": node.ref,
            "action": action,
            "role": node.role,
        },
        http_status=200,
    )


def _ensure_input_capable_semantic(node: SemanticNode, *, action: str) -> None:
    if node.role == "input":
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message=f"{action} requires an input-capable target",
        retryable=False,
        details={
            "reason": "not_input_capable",
            "ref": node.ref or None,
            "action": action,
            "role": node.role,
        },
        http_status=200,
    )


def _ensure_matching_focused_input(screen: PublicScreen, *, node: PublicNode) -> None:
    if public_node_is_focused_input(screen, node):
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message="target is not the current focused input",
        retryable=False,
        details=focus_mismatch_fields(
            ref=node.ref,
            focused_input_ref=public_focused_input_ref(screen),
        ),
        http_status=200,
    )


def _ensure_matching_focused_input_semantic(
    screen: CompiledScreen,
    *,
    target: SemanticNode,
    ref: str,
) -> None:
    if semantic_node_is_focused_input(screen, target):
        return
    raise DaemonError(
        code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
        message="target is not the current focused input",
        retryable=False,
        details=focus_mismatch_fields(
            ref=ref,
            focused_input_ref=semantic_focused_input_ref(screen),
        ),
        http_status=200,
    )


def _find_repaired_target(
    session: WorkspaceRuntime,
    screen: CompiledScreen,
    handle: NodeHandle,
    ref: str,
    *,
    source_screen_id: str,
) -> BoundSemanticNode:
    for node in _compiled_screen_nodes(screen):
        if node.raw_rid == handle.rid:
            return BoundSemanticNode(node=node, blocking_group=screen.blocking_group)
    raise ref_repair_error(
        failed_repair_decision(ref=ref, source_screen_id=source_screen_id),
        public_screen=current_public_screen(session),
        artifacts=current_artifacts(session),
    )


def _compiled_screen_nodes(screen: CompiledScreen) -> tuple[SemanticNode, ...]:
    return (
        tuple(screen.targets)
        + tuple(screen.dialog)
        + tuple(screen.keyboard)
        + tuple(screen.system)
        + tuple(screen.context)
    )
