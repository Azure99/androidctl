"""Submit-only route admission for direct and attributed dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from androidctl_contracts.command_results import ActionTargetEvidence
from androidctld.actions.focused_input_admissibility import (
    keyboard_blocker_allows_submit_subject,
    public_focused_input_ref,
    public_node_is_focused_input,
)
from androidctld.commands.command_models import SubmitCommand
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import NodeHandle
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_compiled_screen,
    current_public_screen,
)
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.semantics.public_models import (
    PublicNode,
    PublicScreen,
    iter_public_nodes,
)

SubmitRouteKind = Literal["direct", "attributed"]
SubmitRouteFailureCode = Literal["TARGET_NOT_ACTIONABLE", "TARGET_BLOCKED"]


@dataclass(frozen=True)
class SubmitRouteOutcome:
    route: SubmitRouteKind
    source_ref: str
    source_screen_id: str
    source_evidence: ActionTargetEvidence
    route_screen_id: str
    subject_ref: str
    subject_handle: NodeHandle
    dispatched_ref: str
    dispatched_handle: NodeHandle


@dataclass(frozen=True)
class SubmitRouteFailure:
    code: SubmitRouteFailureCode
    reason: str
    ref: str | None
    action: str | None = None
    blocking_group: str | None = None
    focused_input_ref: str | None = None

    def to_error(self) -> DaemonError:
        details: dict[str, object] = {"reason": self.reason, "ref": self.ref}
        if self.action is not None:
            details["action"] = self.action
        if self.blocking_group is not None:
            details["blockingGroup"] = self.blocking_group
        if self.focused_input_ref is not None:
            details["focusedInputRef"] = self.focused_input_ref
        if self.code == "TARGET_BLOCKED":
            return DaemonError(
                code=DaemonErrorCode.TARGET_BLOCKED,
                message="target is blocked on the current screen",
                retryable=False,
                details=details,
                http_status=200,
            )
        return DaemonError(
            code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
            message="submit is not available for the requested target",
            retryable=False,
            details=details,
            http_status=200,
        )


def resolve_submit_route(
    session: WorkspaceRuntime,
    command: SubmitCommand,
    *,
    subject_handle: NodeHandle | None,
    source_evidence: ActionTargetEvidence,
) -> SubmitRouteOutcome:
    screen = current_public_screen(session)
    compiled_screen = current_compiled_screen(session)
    if (
        screen is None
        or compiled_screen is None
        or session.latest_snapshot is None
        or subject_handle is None
    ):
        raise _failure(
            "submit_route_unresolved_subject",
            ref=command.ref,
        ).to_error()
    if screen.screen_id != compiled_screen.screen_id:
        raise _failure(
            "submit_route_basis_mismatch",
            ref=command.ref,
        ).to_error()
    if subject_handle.snapshot_id != session.latest_snapshot.snapshot_id:
        raise _failure(
            "submit_route_stale_subject_handle",
            ref=command.ref,
        ).to_error()

    subject_node = _semantic_node_for_handle(compiled_screen, subject_handle)
    subject_ref = None if subject_node is None else subject_node.ref
    if subject_node is None or subject_ref is None:
        raise _failure(
            "submit_route_unresolved_subject",
            ref=command.ref,
        ).to_error()
    subject_public = _unique_public_node(screen, subject_ref)
    if subject_public is None:
        raise _failure(
            "submit_route_unresolved_subject",
            ref=subject_ref,
        ).to_error()
    _ensure_subject_admissible(screen, compiled_screen, subject_node, subject_public)

    submit_refs = subject_public.submit_refs
    if len(submit_refs) > 1:
        raise _failure(
            "submit_route_ambiguous",
            ref=subject_ref,
            action="submit",
        ).to_error()
    if len(submit_refs) == 1:
        dispatched_ref = submit_refs[0]
        dispatched_public = _unique_public_node(screen, dispatched_ref)
        dispatched_node = _unique_semantic_node_for_ref(compiled_screen, dispatched_ref)
        if dispatched_public is None or dispatched_node is None:
            raise _failure(
                "submit_route_unresolved_target",
                ref=dispatched_ref,
                action="tap",
            ).to_error()
        _ensure_unblocked(screen, dispatched_node, ref=dispatched_ref)
        if (
            "tap" not in dispatched_public.actions
            or "tap" not in dispatched_node.actions
        ):
            raise _failure(
                "submit_route_target_not_tap_capable",
                ref=dispatched_ref,
                action="tap",
            ).to_error()

        return SubmitRouteOutcome(
            route="attributed",
            source_ref=command.ref,
            source_screen_id=command.source_screen_id,
            source_evidence=source_evidence,
            route_screen_id=screen.screen_id,
            subject_ref=subject_ref,
            subject_handle=_current_handle(session, subject_node),
            dispatched_ref=dispatched_ref,
            dispatched_handle=_current_handle(session, dispatched_node),
        )

    if "submit" not in subject_public.actions:
        raise _failure(
            "submit_route_missing",
            ref=subject_ref,
            action="submit",
        ).to_error()

    return SubmitRouteOutcome(
        route="direct",
        source_ref=command.ref,
        source_screen_id=command.source_screen_id,
        source_evidence=source_evidence,
        route_screen_id=screen.screen_id,
        subject_ref=subject_ref,
        subject_handle=_current_handle(session, subject_node),
        dispatched_ref=subject_ref,
        dispatched_handle=_current_handle(session, subject_node),
    )


def _ensure_subject_admissible(
    screen: PublicScreen,
    compiled_screen: CompiledScreen,
    subject_node: SemanticNode,
    subject_public: PublicNode,
) -> None:
    _ensure_subject_unblocked(screen, compiled_screen, subject_node, subject_public)
    if subject_public.role != "input" or subject_node.role != "input":
        raise _failure(
            "not_input_capable",
            ref=subject_public.ref,
            action="submit",
        ).to_error()
    if not public_node_is_focused_input(screen, subject_public):
        raise _failure(
            "focus_mismatch",
            ref=subject_public.ref,
            focused_input_ref=public_focused_input_ref(screen),
        ).to_error()


def _ensure_subject_unblocked(
    screen: PublicScreen,
    compiled_screen: CompiledScreen,
    subject_node: SemanticNode,
    subject_public: PublicNode,
) -> None:
    blocking_group = screen.surface.blocking_group
    if blocking_group is None or subject_node.group == blocking_group:
        return
    if keyboard_blocker_allows_submit_subject(
        blocking_group=blocking_group,
        public_screen=screen,
        compiled_screen=compiled_screen,
        public_node=subject_public,
        semantic_node=subject_node,
    ):
        return
    raise _failure(
        f"blocked_by_{blocking_group}",
        code="TARGET_BLOCKED",
        ref=subject_public.ref,
        blocking_group=blocking_group,
    ).to_error()


def _ensure_unblocked(
    screen: PublicScreen,
    node: SemanticNode,
    *,
    ref: str | None,
) -> None:
    blocking_group = screen.surface.blocking_group
    if blocking_group is None or node.group == blocking_group:
        return
    raise _failure(
        f"blocked_by_{blocking_group}",
        code="TARGET_BLOCKED",
        ref=ref,
        blocking_group=blocking_group,
    ).to_error()


def _failure(
    reason: str,
    *,
    ref: str | None,
    code: SubmitRouteFailureCode = "TARGET_NOT_ACTIONABLE",
    action: str | None = None,
    blocking_group: str | None = None,
    focused_input_ref: str | None = None,
) -> SubmitRouteFailure:
    return SubmitRouteFailure(
        code=code,
        reason=reason,
        ref=ref,
        action=action,
        blocking_group=blocking_group,
        focused_input_ref=focused_input_ref,
    )


def _semantic_node_for_handle(
    screen: CompiledScreen,
    handle: NodeHandle,
) -> SemanticNode | None:
    for node in _compiled_nodes(screen):
        if node.raw_rid == handle.rid:
            return node
    return None


def _current_handle(session: WorkspaceRuntime, node: SemanticNode) -> NodeHandle:
    assert session.latest_snapshot is not None
    return NodeHandle(snapshot_id=session.latest_snapshot.snapshot_id, rid=node.raw_rid)


def _unique_semantic_node_for_ref(
    screen: CompiledScreen,
    ref: str,
) -> SemanticNode | None:
    nodes = [node for node in _compiled_nodes(screen) if node.ref == ref]
    return nodes[0] if len(nodes) == 1 else None


def _unique_public_node(screen: PublicScreen, ref: str) -> PublicNode | None:
    nodes = [
        node
        for group in screen.groups
        for node in iter_public_nodes(group.nodes)
        if node.ref == ref
    ]
    return nodes[0] if len(nodes) == 1 else None


def _compiled_nodes(screen: CompiledScreen) -> tuple[SemanticNode, ...]:
    return (
        *screen.targets,
        *screen.context,
        *screen.dialog,
        *screen.keyboard,
        *screen.system,
    )
