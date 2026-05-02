"""Shared submit confirmation helpers for standalone submit commands."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from androidctld.actions.type_confirmation import (
    TypeConfirmationContext,
    fingerprint_rematch_confirmation_node,
    reused_ref_confirmation_node,
    snapshot_node_for_handle,
)
from androidctld.commands.command_models import SubmitCommand
from androidctld.device.types import ActionPerformResult, ActionStatus
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import NodeHandle, RefBinding
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.public_models import (
    PublicNode,
    PublicScreen,
    public_group_nodes,
)
from androidctld.snapshots.models import RawNode, RawSnapshot


@dataclass(frozen=True)
class SubmitConfirmationContext:
    ref: str | None
    request_handle: NodeHandle | None
    binding: RefBinding | None


@dataclass(frozen=True)
class SubmitConfirmationOutcome:
    status: Literal["sameTarget", "targetGone", "publicChange", "unconfirmed"]
    node: RawNode | None
    target_handle: NodeHandle


def build_submit_confirmation_context(
    session: WorkspaceRuntime,
    command: SubmitCommand,
    request_handle: NodeHandle | None,
) -> SubmitConfirmationContext:
    binding = session.ref_registry.get(command.ref)
    return SubmitConfirmationContext(
        ref=command.ref,
        request_handle=request_handle,
        binding=None if binding is None else deepcopy(binding),
    )


def submit_confirmation_node(
    *,
    session: WorkspaceRuntime | None,
    snapshot: RawSnapshot,
    context: SubmitConfirmationContext | None,
    command_target_handle: NodeHandle,
) -> RawNode | None:
    direct_node = snapshot_node_for_handle(snapshot, command_target_handle)
    if direct_node is not None:
        return direct_node
    if session is None or context is None:
        return None
    if (
        context.request_handle is not None
        and command_target_handle != context.request_handle
    ):
        return None
    type_context = TypeConfirmationContext(
        ref=context.ref,
        request_handle=context.request_handle,
        binding=context.binding,
    )
    for node in (
        reused_ref_confirmation_node(session, snapshot, type_context),
        fingerprint_rematch_confirmation_node(session, snapshot, type_context),
    ):
        if node is not None:
            return node
    return None


def submit_public_change_is_attributable(
    previous_screen: PublicScreen | None,
    public_screen: PublicScreen,
) -> bool:
    if previous_screen is None:
        return False

    def structural_groups(
        screen: PublicScreen,
    ) -> tuple[tuple[tuple[object, ...], ...], ...]:
        return (
            _structural_node_signatures(public_group_nodes(screen, "targets")),
            _structural_node_signatures(public_group_nodes(screen, "context")),
            _structural_node_signatures(public_group_nodes(screen, "dialog")),
        )

    return structural_groups(previous_screen) != structural_groups(public_screen)


def _structural_node_signatures(
    nodes: tuple[PublicNode, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(_structural_node_signature(node) for node in nodes)


def _structural_node_signature(node: PublicNode) -> tuple[object, ...]:
    return (
        node.kind,
        node.role,
        node.label,
        node.text,
        tuple(_structural_node_signature(child) for child in node.children),
        tuple(node.scroll_directions),
        (
            None
            if node.meta is None
            else (
                node.meta.resource_id,
                node.meta.class_name,
            )
        ),
    )


def validate_submit_confirmation(
    *,
    session: WorkspaceRuntime | None = None,
    route_kind: Literal["direct", "attributed"],
    action_result: ActionPerformResult,
    previous_snapshot: RawSnapshot | None,
    snapshot: RawSnapshot,
    previous_screen: PublicScreen | None,
    public_screen: PublicScreen,
    context: SubmitConfirmationContext | None = None,
    command_target_handle: NodeHandle | None,
) -> SubmitConfirmationOutcome:
    if command_target_handle is None:
        raise DaemonError(
            code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
            message="submit effect could not be confirmed",
            retryable=True,
            details={"reason": "missing_command_target_identity"},
            http_status=200,
        )
    if action_result.status is not ActionStatus.DONE:
        raise DaemonError(
            code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
            message="submit effect could not be confirmed",
            retryable=True,
            details={
                "reason": "device_submit_not_accepted",
                "status": action_result.status.value,
            },
            http_status=200,
        )
    if previous_snapshot is not None and (
        snapshot.package_name != previous_snapshot.package_name
        or snapshot.activity_name != previous_snapshot.activity_name
    ):
        return SubmitConfirmationOutcome(
            status="unconfirmed",
            node=None,
            target_handle=command_target_handle,
        )
    confirmation_node = submit_confirmation_node(
        session=session,
        snapshot=snapshot,
        context=context,
        command_target_handle=command_target_handle,
    )
    if confirmation_node is None:
        return SubmitConfirmationOutcome(
            status="targetGone",
            node=None,
            target_handle=command_target_handle,
        )
    if submit_public_change_is_attributable(
        previous_screen,
        public_screen,
    ):
        return SubmitConfirmationOutcome(
            status="publicChange",
            node=confirmation_node,
            target_handle=command_target_handle,
        )
    if not confirmation_node.focused or not confirmation_node.editable:
        if route_kind == "attributed":
            raise DaemonError(
                code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
                message="submit effect could not be confirmed",
                retryable=True,
                details={"reason": "attributed_submit_blur_only"},
                http_status=200,
            )
        return SubmitConfirmationOutcome(
            status="sameTarget",
            node=confirmation_node,
            target_handle=command_target_handle,
        )
    raise DaemonError(
        code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
        message="submit effect could not be confirmed",
        retryable=True,
        details={"reason": "target_still_focused_editable"},
        http_status=200,
    )
