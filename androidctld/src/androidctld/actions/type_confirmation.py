"""Type confirmation helpers for post-action validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from androidctld.commands.command_models import TypeCommand
from androidctld.device.types import (
    ActionPerformResult,
    ResolvedHandleTarget,
    ResolvedTarget,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import NodeHandle, RefBinding
from androidctld.refs.service import best_candidate_for_binding
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.snapshots.models import RawNode, RawSnapshot
from androidctld.text_equivalence import (
    canonical_text_key,
)


@dataclass(frozen=True)
class TypeConfirmationContext:
    ref: str | None
    request_handle: NodeHandle | None
    binding: RefBinding | None


@dataclass(frozen=True)
class TypeConfirmationCandidate:
    strategy: str
    node: RawNode | None
    target_handle: NodeHandle | None


def validate_type_confirmation(
    session: WorkspaceRuntime,
    command: TypeCommand,
    snapshot: RawSnapshot,
    context: TypeConfirmationContext,
    action_result: ActionPerformResult,
) -> TypeConfirmationCandidate:
    candidates = type_confirmation_candidates(
        session,
        snapshot,
        context,
        action_result,
    )
    for candidate in candidates:
        if candidate.node is None:
            raise RuntimeError("type confirmation candidate is missing node")
        if matches_typed_value(command, observed_input_value(candidate.node)):
            return candidate
    raise DaemonError(
        code=DaemonErrorCode.TYPE_NOT_CONFIRMED,
        message="typed text was not confirmed on the refreshed screen",
        retryable=True,
        details=type_confirmation_error_details(command, context, candidates),
        http_status=200,
    )


def build_type_confirmation_context(
    session: WorkspaceRuntime,
    command: TypeCommand,
    request_handle: NodeHandle | None,
) -> TypeConfirmationContext:
    binding = session.ref_registry.get(command.ref)
    return TypeConfirmationContext(
        ref=command.ref,
        request_handle=request_handle,
        binding=None if binding is None else deepcopy(binding),
    )


def type_confirmation_candidates(
    session: WorkspaceRuntime,
    snapshot: RawSnapshot,
    context: TypeConfirmationContext,
    action_result: ActionPerformResult,
) -> list[TypeConfirmationCandidate]:
    candidate_nodes: list[TypeConfirmationCandidate] = []
    seen_rids: set[str] = set()

    def add_candidate(
        strategy: str,
        node: RawNode | None,
        target_handle: NodeHandle | None,
    ) -> None:
        if node is None or node.rid in seen_rids:
            return
        seen_rids.add(node.rid)
        candidate_nodes.append(
            TypeConfirmationCandidate(
                strategy=strategy,
                node=node,
                target_handle=target_handle,
            )
        )

    resolved_handle = action_result_target_handle(action_result)
    add_candidate(
        "resolvedTarget",
        snapshot_node_for_handle(snapshot, resolved_handle),
        resolved_handle,
    )
    add_candidate(
        "requestTarget",
        snapshot_node_for_handle(snapshot, context.request_handle),
        context.request_handle,
    )
    reused_node = reused_ref_confirmation_node(session, snapshot, context)
    add_candidate("reusedRef", reused_node, _snapshot_handle(snapshot, reused_node))
    rematch_node = fingerprint_rematch_confirmation_node(session, snapshot, context)
    add_candidate(
        "fingerprintRematch",
        rematch_node,
        _snapshot_handle(snapshot, rematch_node),
    )
    return candidate_nodes


def action_result_target_handle(
    action_result: ActionPerformResult,
) -> NodeHandle | None:
    resolved_target = action_result.resolved_target
    if resolved_target is None:
        return None
    return resolved_target_handle(resolved_target)


def resolved_target_handle(target: ResolvedTarget) -> NodeHandle | None:
    if not isinstance(target, ResolvedHandleTarget):
        return None
    return target.handle


def snapshot_node_for_handle(
    snapshot: RawSnapshot, handle: NodeHandle | None
) -> RawNode | None:
    if handle is None:
        return None
    for node in snapshot.nodes:
        if node.rid == handle.rid:
            return node
    return None


def _snapshot_handle(snapshot: RawSnapshot, node: RawNode | None) -> NodeHandle | None:
    if node is None:
        return None
    return NodeHandle(snapshot_id=snapshot.snapshot_id, rid=node.rid)


def is_type_confirmation_candidate(node: RawNode) -> bool:
    return bool(node.visible_to_user and node.editable)


def observed_input_value(node: RawNode) -> str:
    if node.text is None:
        return ""
    return str(node.text)


def matches_typed_value(command: TypeCommand, actual_value: str) -> bool:
    return canonical_text_key(actual_value) == canonical_text_key(command.text)


def reused_ref_confirmation_node(
    session: WorkspaceRuntime,
    snapshot: RawSnapshot,
    context: TypeConfirmationContext,
) -> RawNode | None:
    if context.ref is None:
        return None
    binding = session.ref_registry.get(context.ref)
    if binding is None or not binding.reused:
        return None
    node = snapshot_node_for_handle(snapshot, binding.handle)
    if node is None or not is_type_confirmation_candidate(node):
        return None
    return node


def fingerprint_rematch_confirmation_node(
    session: WorkspaceRuntime,
    snapshot: RawSnapshot,
    context: TypeConfirmationContext,
) -> RawNode | None:
    if context.binding is None or session.screen_state is None:
        return None
    compiled_screen = session.screen_state.compiled_screen
    if compiled_screen is None:
        return None
    match = best_candidate_for_binding(
        context.binding,
        type_confirmation_semantic_candidates(compiled_screen, snapshot),
    )
    if match is None:
        return None
    candidate, _ = match
    return snapshot_node_for_rid(snapshot, candidate.raw_rid)


def type_confirmation_semantic_candidates(
    compiled_screen: CompiledScreen,
    snapshot: RawSnapshot,
) -> list[SemanticNode]:
    nodes_by_rid = {node.rid: node for node in snapshot.nodes}
    candidates = []
    for semantic_node in compiled_screen_nodes(compiled_screen):
        raw_node = nodes_by_rid.get(semantic_node.raw_rid)
        if raw_node is None or not is_type_confirmation_candidate(raw_node):
            continue
        candidates.append(semantic_node)
    return candidates


def snapshot_node_for_rid(snapshot: RawSnapshot, rid: str) -> RawNode | None:
    for node in snapshot.nodes:
        if node.rid == rid:
            return node
    return None


def type_confirmation_error_details(
    command: TypeCommand,
    context: TypeConfirmationContext,
    candidates: list[TypeConfirmationCandidate],
) -> dict[str, Any]:
    return {
        "ref": context.ref,
        "text": command.text,
        "replace": True,
        "candidateCount": len(candidates),
        "confirmationStrategy": (
            "resolvedTarget>requestTarget>reusedRef>" + "fingerprintRematch"
        ),
        "candidateRids": [
            None if candidate.node is None else candidate.node.rid
            for candidate in candidates
        ],
    }


def compiled_screen_nodes(compiled_screen: CompiledScreen) -> tuple[SemanticNode, ...]:
    return (
        *compiled_screen.targets,
        *compiled_screen.context,
        *compiled_screen.dialog,
        *compiled_screen.keyboard,
        *compiled_screen.system,
    )
