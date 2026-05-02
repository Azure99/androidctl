"""Focus confirmation helpers for post-action validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from androidctld.actions.type_confirmation import (
    TypeConfirmationContext,
    fingerprint_rematch_confirmation_node,
    resolved_target_handle,
    reused_ref_confirmation_node,
    snapshot_node_for_handle,
)
from androidctld.commands.command_models import FocusCommand
from androidctld.device.types import ResolvedTarget
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import NodeHandle, RefBinding
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.snapshots.models import RawNode, RawSnapshot


@dataclass(frozen=True)
class FocusConfirmationCandidate:
    strategy: str
    node: RawNode


@dataclass(frozen=True)
class FocusConfirmationOutcome:
    strategy: str
    node: RawNode
    target_handle: NodeHandle


@dataclass(frozen=True)
class FocusConfirmationContext:
    request_handle: NodeHandle | None
    binding: RefBinding | None
    resolved_target: ResolvedTarget | None


def build_focus_confirmation_context(
    session: WorkspaceRuntime,
    command: FocusCommand,
    request_handle: NodeHandle | None,
) -> FocusConfirmationContext:
    binding = session.ref_registry.get(command.ref)
    return FocusConfirmationContext(
        request_handle=request_handle,
        binding=None if binding is None else deepcopy(binding),
        resolved_target=None,
    )


def validate_focus_confirmation(
    *,
    session: WorkspaceRuntime,
    previous_snapshot: RawSnapshot | None,
    snapshot: RawSnapshot,
    context: FocusConfirmationContext,
) -> FocusConfirmationOutcome:
    previous_candidates = focus_confirmation_candidates(
        session=session,
        snapshot=previous_snapshot,
        context=context,
    )
    candidates = focus_confirmation_candidates(
        session=session,
        snapshot=snapshot,
        context=context,
    )
    candidate = first_valid_focus_candidate(candidates)
    if candidate is None and not candidates:
        raise DaemonError(
            code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
            message="focus did not expose a resolvable target handle",
            retryable=True,
            details={},
            http_status=200,
        )
    previous_candidate = previous_candidate_for_strategy(
        previous_candidates,
        strategy=None if candidate is None else candidate.strategy,
    )
    if previous_candidate is not None and previous_candidate.node.focused:
        raise DaemonError(
            code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
            message="focus target was already focused before refresh",
            retryable=True,
            details={"reason": "already_focused"},
            http_status=200,
        )
    if candidate is None:
        raise DaemonError(
            code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
            message="focus did not land on the requested input target",
            retryable=True,
            details={},
            http_status=200,
        )
    return FocusConfirmationOutcome(
        strategy=candidate.strategy,
        node=candidate.node,
        target_handle=NodeHandle(
            snapshot_id=snapshot.snapshot_id,
            rid=candidate.node.rid,
        ),
    )


def focus_confirmation_candidates(
    *,
    session: WorkspaceRuntime,
    snapshot: RawSnapshot | None,
    context: FocusConfirmationContext,
) -> list[FocusConfirmationCandidate]:
    if snapshot is None:
        return []
    type_context = TypeConfirmationContext(
        ref=None if context.binding is None else context.binding.ref,
        request_handle=context.request_handle,
        binding=context.binding,
    )
    candidates: list[FocusConfirmationCandidate] = []
    seen_rids: set[str] = set()

    def add_candidate(strategy: str, node: RawNode | None) -> None:
        if node is None or node.rid in seen_rids:
            return
        seen_rids.add(node.rid)
        candidates.append(FocusConfirmationCandidate(strategy=strategy, node=node))

    add_candidate(
        "resolvedTarget",
        snapshot_node_for_handle(
            snapshot,
            (
                None
                if context.resolved_target is None
                else resolved_target_handle(context.resolved_target)
            ),
        ),
    )
    add_candidate(
        "requestTarget", snapshot_node_for_handle(snapshot, context.request_handle)
    )
    add_candidate(
        "reusedRef", reused_ref_confirmation_node(session, snapshot, type_context)
    )
    add_candidate(
        "fingerprintRematch",
        fingerprint_rematch_confirmation_node(session, snapshot, type_context),
    )
    return candidates


def first_valid_focus_candidate(
    candidates: list[FocusConfirmationCandidate],
) -> FocusConfirmationCandidate | None:
    for candidate in candidates:
        if candidate.node.focused and candidate.node.editable:
            return candidate
    return None


def previous_candidate_for_strategy(
    candidates: list[FocusConfirmationCandidate],
    *,
    strategy: str | None,
) -> FocusConfirmationCandidate | None:
    if strategy is None:
        return None
    for candidate in candidates:
        if candidate.strategy == strategy:
            return candidate
    return None
