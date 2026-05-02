"""Semantic compiler surface-shape helpers."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from androidctld.semantics.labels import normalize_text
from androidctld.semantics.policy import (
    ENABLED_SCORE_BONUS,
    FOCUSED_SCORE_BONUS,
    ROLE_BASE_SCORES,
    TARGETABLE_SCORE_BONUS,
)
from androidctld.semantics.public_models import (
    BlockingGroupName,
    PublicNode,
    PublicSemanticMeta,
)
from androidctld.semantics.registries import GROUP_ORDER
from androidctld.semantics.targets import normalize_action_name
from androidctld.snapshots.models import RawNode, RawSnapshot
from androidctld.text_equivalence import canonical_text_key

if TYPE_CHECKING:
    from androidctld.semantics.compiler import SemanticNode


_SCROLL_DIRECTION_BY_ACTION = {
    "scrollForward": "down",
    "scrollBackward": "backward",
    "scrollUp": "up",
    "scrollDown": "down",
    "scrollLeft": "left",
    "scrollRight": "right",
}


def passive_node_dedup_key(
    node: SemanticNode,
) -> tuple[str, str, str, str, str] | None:
    if node.targetable:
        return None
    normalized_label = normalize_text(node.label).lower()
    if not normalized_label:
        return None
    return (
        node.group,
        node.role,
        normalized_label,
        normalize_text(node.parent_role).lower(),
        normalize_text(node.parent_label).lower(),
    )


def infer_group(
    raw_node: RawNode,
    role: str,
    targetable: bool,
    *,
    ime_window_id: str | None,
) -> str:
    if role == "dialog" or raw_node.pane_title:
        return "dialog"
    normalized_ime_window_id = (
        None if ime_window_id is None else normalize_text(ime_window_id)
    )
    if role == "keyboard-key" or (
        normalized_ime_window_id is not None
        and normalize_text(raw_node.window_id) == normalized_ime_window_id
    ):
        return "keyboard"
    package_name = raw_node.package_name
    if package_name is not None and package_name.startswith("com.android.systemui"):
        return "system"
    if targetable:
        return "targets"
    return "context"


def score_node(raw_node: RawNode, role: str, targetable: bool) -> int:
    score = 0
    if targetable:
        score += TARGETABLE_SCORE_BONUS
    score += ROLE_BASE_SCORES.get(role, 0)
    if raw_node.focused:
        score += FOCUSED_SCORE_BONUS
    if raw_node.enabled:
        score += ENABLED_SCORE_BONUS
    return score


def target_sort_key(node: SemanticNode) -> tuple[int, int, int, str, str]:
    return (
        -node.score,
        node.bounds[1],
        node.bounds[0],
        node.label.lower(),
        node.raw_rid,
    )


def context_sort_key(node: SemanticNode) -> tuple[int, int, str]:
    return (
        node.bounds[1],
        node.bounds[0],
        node.label.lower(),
    )


def ime_owned_surface(
    *,
    snapshot: RawSnapshot,
    grouped_nodes: dict[str, list[SemanticNode]],
) -> bool:
    if not snapshot.ime.visible:
        return False
    return any(
        node.role == "input" and "focused" in node.state
        for node in grouped_nodes["keyboard"]
    )


def resolve_blocking_group(
    *,
    snapshot: RawSnapshot,
    grouped_nodes: dict[str, list[SemanticNode]],
) -> BlockingGroupName | None:
    if any(node.actions for node in grouped_nodes["dialog"]):
        return "dialog"
    if ime_owned_surface(snapshot=snapshot, grouped_nodes=grouped_nodes) and any(
        node.actions for node in grouped_nodes["keyboard"]
    ):
        return "keyboard"
    if any(node.actions for node in grouped_nodes["system"]):
        return "system"
    return None


def apply_blocking_policy(
    grouped_nodes: dict[str, list[SemanticNode]],
    blocking_group: BlockingGroupName | None,
) -> None:
    if blocking_group is None:
        return
    for group_name, nodes in grouped_nodes.items():
        if group_name == "context":
            continue
        if group_name == blocking_group:
            continue
        for node in nodes:
            node.actions = []


def build_action_surface_fingerprint(
    *,
    snapshot: RawSnapshot,
    grouped_nodes: dict[str, list[SemanticNode]],
    blocking_group: BlockingGroupName | None,
) -> str:
    parts = [
        canonical_text_key(snapshot.package_name),
        canonical_text_key(snapshot.activity_name),
        "keyboard-visible" if snapshot.ime.visible else "keyboard-hidden",
        f"blocking:{blocking_group or ''}",
    ]
    for group_name in GROUP_ORDER:
        parts.append(f"group:{group_name}")
        for node in grouped_nodes[group_name]:
            parts.append(semantic_node_fingerprint(group_name, node))
    return "\n".join(parts)


def semantic_node_fingerprint(group_name: str, node: SemanticNode) -> str:
    parts = [_semantic_node_base_fingerprint(group_name, node)]
    parts.extend(f"submitRefs:{token}" for token in sorted(node.submit_relation_tokens))
    return "|".join(parts)


def _semantic_node_base_fingerprint(group_name: str, node: SemanticNode) -> str:
    state = ",".join(sorted(canonical_text_key(token) for token in node.state))
    actions = ",".join(sorted(canonical_text_key(token) for token in node.actions))
    siblings = ",".join(
        canonical_text_key(label)
        for label in node.sibling_labels
        if canonical_text_key(label)
    )
    bounds = ",".join(str(value) for value in node.relative_bounds)
    return "|".join(
        (
            group_name,
            canonical_text_key(node.role),
            canonical_text_key(node.label),
            state,
            actions,
            canonical_text_key(node.resource_id),
            canonical_text_key(node.class_name),
            canonical_text_key(node.parent_role),
            canonical_text_key(node.parent_label),
            siblings,
            bounds,
        )
    )


def semantic_relation_identity(group_name: str, node: SemanticNode) -> str:
    return _semantic_node_base_fingerprint(group_name, node)


def semantic_relation_key(group_name: str, node: SemanticNode) -> tuple[str, str]:
    return (group_name, semantic_relation_identity(group_name, node))


def stable_screen_id(action_surface_fingerprint: str) -> str:
    digest = hashlib.sha256(action_surface_fingerprint.encode("utf-8")).digest()
    numeric = int.from_bytes(digest[:8], "big") % (10**16)
    return f"screen-{numeric:016d}"


def node_to_public_node(
    node: SemanticNode,
    *,
    submit_refs: tuple[str, ...] = (),
) -> PublicNode:
    return PublicNode(
        kind="container" if node.role == "scroll-container" else "node",
        role=node.role,
        label=node.label,
        ref=node.ref or None,
        state=tuple(node.state),
        actions=tuple(node.actions),
        bounds=node.bounds,
        meta=PublicSemanticMeta(
            resource_id=node.meta.resource_id,
            class_name=node.meta.class_name,
        ),
        scroll_directions=tuple(node.scroll_directions),
        submit_refs=submit_refs,
        children=tuple(
            PublicNode(kind="text", text=child_text)
            for child_text in node.text_children
        ),
    )


def scroll_directions_for_raw_node(raw_node: RawNode) -> tuple[str, ...]:
    directions: list[str] = []
    for action in raw_node.actions:
        normalized = normalize_action_name(action)
        direction = _SCROLL_DIRECTION_BY_ACTION.get(normalized)
        if direction is None or direction in directions:
            continue
        directions.append(direction)
    return tuple(directions)
