"""Public-safe actionTarget projection helpers."""

from __future__ import annotations

from collections.abc import Iterable

from androidctl_contracts.command_results import (
    ActionTargetEvidence,
    ActionTargetIdentityStatus,
    ActionTargetPayload,
)
from androidctld.refs.models import NodeHandle
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.semantics.public_models import PublicScreen, iter_public_nodes
from androidctld.snapshots.models import RawNode


def public_ref_for_handle(
    *,
    compiled_screen: CompiledScreen | None,
    public_screen: PublicScreen | None,
    handle: NodeHandle | None,
) -> str | None:
    if compiled_screen is None or public_screen is None or handle is None:
        return None
    if compiled_screen.screen_id != public_screen.screen_id:
        return None
    if compiled_screen.source_snapshot_id != handle.snapshot_id:
        return None
    return _unique_public_ref_for_raw_rid(
        compiled_screen=compiled_screen,
        public_screen=public_screen,
        raw_rid=handle.rid,
    )


def public_ref_for_raw_node(
    *,
    compiled_screen: CompiledScreen | None,
    public_screen: PublicScreen | None,
    node: RawNode | None,
) -> str | None:
    if compiled_screen is None or public_screen is None or node is None:
        return None
    if compiled_screen.screen_id != public_screen.screen_id:
        return None
    return _unique_public_ref_for_raw_rid(
        compiled_screen=compiled_screen,
        public_screen=public_screen,
        raw_rid=node.rid,
    )


def build_action_target_payload(
    *,
    source_ref: str,
    source_screen_id: str,
    subject_ref: str | None,
    next_screen_id: str,
    identity_status: ActionTargetIdentityStatus,
    evidence: Iterable[ActionTargetEvidence],
    dispatched_ref: str | None = None,
    next_ref: str | None = None,
) -> ActionTargetPayload | None:
    if subject_ref is None:
        return None
    payload_kwargs = {
        "source_ref": source_ref,
        "source_screen_id": source_screen_id,
        "subject_ref": subject_ref,
        "next_screen_id": next_screen_id,
        "identity_status": identity_status,
        "evidence": tuple(evidence),
    }
    if dispatched_ref is not None:
        payload_kwargs["dispatched_ref"] = dispatched_ref
    if next_ref is not None:
        payload_kwargs["next_ref"] = next_ref
    return ActionTargetPayload(**payload_kwargs)


def build_same_or_successor_action_target(
    *,
    source_ref: str,
    source_screen_id: str,
    subject_ref: str | None,
    dispatched_ref: str | None,
    next_screen_id: str,
    next_ref: str | None,
    evidence: Iterable[ActionTargetEvidence],
) -> ActionTargetPayload | None:
    if subject_ref is None or next_ref is None:
        return None
    identity_status: ActionTargetIdentityStatus = (
        "sameRef" if next_ref == subject_ref else "successor"
    )
    return build_action_target_payload(
        source_ref=source_ref,
        source_screen_id=source_screen_id,
        subject_ref=subject_ref,
        dispatched_ref=dispatched_ref,
        next_screen_id=next_screen_id,
        next_ref=next_ref,
        identity_status=identity_status,
        evidence=evidence,
    )


def _unique_public_ref_for_raw_rid(
    *,
    compiled_screen: CompiledScreen,
    public_screen: PublicScreen,
    raw_rid: str,
) -> str | None:
    refs = {
        node.ref
        for node in _compiled_nodes(compiled_screen)
        if node.raw_rid == raw_rid and node.ref
    }
    if len(refs) != 1:
        return None
    ref = next(iter(refs))
    return ref if _public_ref_is_unique(public_screen, ref) else None


def _compiled_nodes(compiled_screen: CompiledScreen) -> tuple[SemanticNode, ...]:
    return (
        *compiled_screen.targets,
        *compiled_screen.context,
        *compiled_screen.dialog,
        *compiled_screen.keyboard,
        *compiled_screen.system,
    )


def _public_ref_is_unique(public_screen: PublicScreen, ref: str) -> bool:
    count = 0
    for group in public_screen.groups:
        for node in iter_public_nodes(group.nodes):
            if node.ref == ref:
                count += 1
    return count == 1
