"""Semantic compiler target and promotion rules."""

from __future__ import annotations

from androidctld.semantics.labels import (
    GENERIC_SEMANTIC_ROLES,
    LabelInfo,
    infer_role,
    normalize_text,
    parent_node_for,
    synthesize_label_info,
)
from androidctld.snapshots.models import RawNode

CLICK_ACTIONS = {"tap", "click"}
LONG_CLICK_ACTIONS = {"longTap", "longClick"}
TYPE_ACTIONS = {"setText"}
SCROLL_ACTIONS = {
    "scrollForward",
    "scrollBackward",
    "scrollUp",
    "scrollDown",
    "scrollLeft",
    "scrollRight",
}
SUBMIT_ACTIONS = {"submit"}


def semantic_actions_for(raw_node: RawNode) -> list[str]:
    raw_actions = {normalize_action_name(action) for action in raw_node.actions}
    semantic_actions: list[str] = []
    if raw_actions.intersection(CLICK_ACTIONS):
        semantic_actions.append("tap")
    if raw_actions.intersection(LONG_CLICK_ACTIONS):
        semantic_actions.append("longTap")
    if raw_node.editable and raw_actions.intersection(TYPE_ACTIONS):
        semantic_actions.append("type")
    if raw_node.scrollable and raw_actions.intersection(SCROLL_ACTIONS):
        semantic_actions.append("scroll")
    return semantic_actions


def public_primary_actions_for(
    *,
    anchor_node: RawNode,
    role: str,
    primary_actions: list[str],
) -> list[str]:
    if role != "input" or not anchor_node.editable:
        return list(primary_actions)
    if anchor_node.focused:
        return list(primary_actions)
    return []


def secondary_public_actions_for(
    *,
    anchor_node: RawNode,
    role: str,
    primary_actions: list[str],
) -> list[str]:
    del primary_actions
    anchor_actions = {normalize_action_name(action) for action in anchor_node.actions}
    if role != "input" or not anchor_node.editable:
        return []
    secondary_actions: list[str] = []
    if anchor_node.focused:
        if anchor_actions.intersection(SUBMIT_ACTIONS):
            secondary_actions.append("submit")
        return secondary_actions
    if "focus" not in anchor_actions:
        return secondary_actions
    secondary_actions.append("focus")
    return secondary_actions


def select_target_sources(
    raw_nodes: tuple[RawNode, ...],
    raw_nodes_by_rid: dict[str, RawNode],
    label_infos: dict[str, LabelInfo],
    actionability: dict[str, list[str]],
) -> dict[str, str]:
    target_sources: dict[str, str] = {}
    for raw_node in raw_nodes:
        anchor_node = actionable_anchor_for(raw_node, raw_nodes_by_rid, actionability)
        if anchor_node is None:
            continue
        existing_source = target_sources.get(anchor_node.rid)
        if existing_source is None or target_source_sort_key(
            raw_nodes_by_rid[existing_source],
            anchor_node,
            raw_nodes_by_rid,
            label_infos,
        ) < target_source_sort_key(
            raw_node,
            anchor_node,
            raw_nodes_by_rid,
            label_infos,
        ):
            target_sources[anchor_node.rid] = raw_node.rid
    return target_sources


def actionable_anchor_for(
    raw_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    actionability: dict[str, list[str]],
) -> RawNode | None:
    if actionability[raw_node.rid]:
        return raw_node
    current = parent_node_for(raw_node, raw_nodes_by_rid)
    while current is not None:
        anchor_actions = actionability[current.rid]
        if anchor_actions:
            if can_promote_to_actionable_ancestor(raw_node, anchor_actions):
                return current
            return None
        current = parent_node_for(current, raw_nodes_by_rid)
    return None


def can_promote_to_actionable_ancestor(
    raw_node: RawNode,
    anchor_actions: list[str],
) -> bool:
    if "scroll" in anchor_actions:
        return False
    label_quality = synthesize_label_info(raw_node, {raw_node.rid: raw_node}).quality
    role = infer_role(raw_node)
    return label_quality > 0 or role not in GENERIC_SEMANTIC_ROLES


def target_source_sort_key(
    raw_node: RawNode,
    anchor_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    label_infos: dict[str, LabelInfo],
) -> tuple[int, int, int, int, int, str]:
    label_info = label_infos[raw_node.rid]
    role = infer_role(raw_node)
    actionable_role_bonus = int(role not in GENERIC_SEMANTIC_ROLES)
    return (
        label_info.quality,
        actionable_role_bonus,
        int(raw_node.rid != anchor_node.rid),
        -ancestor_distance(raw_node, anchor_node.rid, raw_nodes_by_rid),
        len(normalize_text(label_info.label)),
        raw_node.rid,
    )


def ancestor_distance(
    raw_node: RawNode,
    ancestor_rid: str,
    raw_nodes_by_rid: dict[str, RawNode],
) -> int:
    distance = 0
    current: RawNode | None = raw_node
    while current is not None and current.rid != ancestor_rid:
        current = parent_node_for(current, raw_nodes_by_rid)
        distance += 1
    return distance


def normalize_action_name(value: str) -> str:
    normalized = normalize_text(value)
    return normalized[:1].lower() + normalized[1:]
