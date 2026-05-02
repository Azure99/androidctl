"""Semantic compiler label and state-description rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from androidctld.semantics.policy import LABEL_MAX_LENGTH
from androidctld.snapshots.models import RawNode
from androidctld.text_equivalence import (
    canonical_text_key,
    normalized_text_surface,
    semantic_state_description_remainder,
)

_WHITESPACE_RE = re.compile(r"\s+")
_STATE_DESCRIPTION_SPLIT_RE = re.compile(r"[,;/]+")
GENERIC_SEMANTIC_ROLES = {"container", "text", "image", "list-item"}
LABEL_QUALITY_RESOURCE_ID = 1
LABEL_QUALITY_STATE_DESCRIPTION = 2
LABEL_QUALITY_NEARBY = 3
LABEL_QUALITY_HINT = 4
LABEL_QUALITY_CONTENT_DESC = 5
LABEL_QUALITY_TEXT = 6
_STATE_DESCRIPTION_STATE_MAP = {
    "on": "checked",
    "off": "unchecked",
    "checked": "checked",
    "unchecked": "unchecked",
    "not checked": "unchecked",
    "selected": "selected",
    "disabled": "disabled",
    "focused": "focused",
    "expanded": "expanded",
    "collapsed": "collapsed",
    "password": "password",
}


@dataclass(frozen=True)
class LabelInfo:
    label: str
    quality: int


def parent_node_for(
    raw_node: RawNode, raw_nodes_by_rid: dict[str, RawNode]
) -> RawNode | None:
    if raw_node.parent_rid is None:
        return None
    return raw_nodes_by_rid.get(raw_node.parent_rid)


def infer_role(raw_node: RawNode | None) -> str:
    if raw_node is None:
        return "container"
    class_name = raw_node.class_name
    if "Dialog" in class_name:
        return "dialog"
    if "Tab" in class_name:
        return "tab"
    if "Keyboard" in class_name or "Key" in class_name:
        return "keyboard-key"
    if raw_node.editable or "EditText" in class_name:
        return "input"
    if "Switch" in class_name:
        return "switch"
    if "CheckBox" in class_name:
        return "checkbox"
    if "RadioButton" in class_name:
        return "radio"
    if "Button" in class_name:
        return "button"
    if "Image" in class_name:
        return "image"
    if raw_node.clickable:
        return "list-item"
    if raw_node.text or raw_node.content_desc:
        return "text"
    return "container"


def synthesize_label_info(
    raw_node: RawNode | None, raw_nodes_by_rid: dict[str, RawNode]
) -> LabelInfo:
    if raw_node is None:
        return LabelInfo(label="", quality=0)
    for quality, value in (
        (LABEL_QUALITY_TEXT, raw_node.text),
        (LABEL_QUALITY_CONTENT_DESC, raw_node.content_desc),
        (LABEL_QUALITY_HINT, raw_node.hint_text),
    ):
        label = normalize_text(value)
        if label:
            return LabelInfo(label=label[:LABEL_MAX_LENGTH], quality=quality)
    near_label = near_label_for_node(raw_node, raw_nodes_by_rid)
    if near_label:
        return LabelInfo(
            label=near_label[:LABEL_MAX_LENGTH],
            quality=LABEL_QUALITY_NEARBY,
        )
    state_description_label = label_from_state_description(raw_node.state_description)
    if state_description_label:
        return LabelInfo(
            label=state_description_label[:LABEL_MAX_LENGTH],
            quality=LABEL_QUALITY_STATE_DESCRIPTION,
        )
    resource_id = normalize_text(raw_node.resource_id)
    if resource_id:
        return LabelInfo(
            label=resource_id.split("/")[-1][:LABEL_MAX_LENGTH],
            quality=LABEL_QUALITY_RESOURCE_ID,
        )
    return LabelInfo(label="", quality=0)


def fallback_label(raw_node: RawNode | None) -> str:
    if raw_node is None:
        return "item"
    return raw_node.class_name.split(".")[-1] or "item"


def extract_state(raw_node: RawNode) -> list[str]:
    state = []
    if raw_node.checked:
        state.append("checked")
    elif raw_node.checkable:
        state.append("unchecked")
    if raw_node.selected:
        state.append("selected")
    if not raw_node.enabled:
        state.append("disabled")
    if raw_node.focused:
        state.append("focused")
    if raw_node.password:
        state.append("password")
    for token in states_from_state_description(raw_node.state_description):
        if token not in state:
            state.append(token)
    return state


def semantic_role(
    raw_node: RawNode,
    anchor_node: RawNode,
    anchor_actions: list[str],
) -> str:
    source_role = infer_role(raw_node)
    anchor_role = infer_role(anchor_node)
    if not anchor_actions or source_role not in GENERIC_SEMANTIC_ROLES:
        role = source_role
    elif anchor_role not in GENERIC_SEMANTIC_ROLES:
        role = anchor_role
    elif "type" in anchor_actions:
        role = "input"
    elif "scroll" in anchor_actions:
        role = "container"
    else:
        role = anchor_role
    if role == "input" and not anchor_node.editable:
        if anchor_role != "input":
            return anchor_role
        if "scroll" in anchor_actions:
            return "container"
        if "tap" in anchor_actions or "longTap" in anchor_actions:
            return "list-item"
        return "text"
    return role


def near_label_for_node(raw_node: RawNode, raw_nodes_by_rid: dict[str, RawNode]) -> str:
    parent_node = parent_node_for(raw_node, raw_nodes_by_rid)
    if parent_node is None:
        return ""
    for child_rid in parent_node.child_rids:
        if child_rid == raw_node.rid:
            continue
        sibling = raw_nodes_by_rid.get(child_rid)
        if sibling is None:
            continue
        label = normalize_text(sibling.text or sibling.content_desc)
        if label:
            return label
    return normalize_text(parent_node.text or parent_node.content_desc)


def sibling_labels_for_node(
    raw_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    label_infos: dict[str, LabelInfo],
) -> list[str]:
    parent_node = parent_node_for(raw_node, raw_nodes_by_rid)
    if parent_node is None:
        return []
    labels = []
    for child_rid in parent_node.child_rids:
        if child_rid == raw_node.rid:
            continue
        sibling = raw_nodes_by_rid.get(child_rid)
        if sibling is None:
            continue
        label = label_infos[sibling.rid].label or fallback_label(sibling)
        if label:
            labels.append(label)
    return labels


def relative_bounds_for_node(
    raw_node: RawNode, parent_node: RawNode | None
) -> tuple[int, int, int, int]:
    if parent_node is None:
        return raw_node.bounds
    return (
        raw_node.bounds[0] - parent_node.bounds[0],
        raw_node.bounds[1] - parent_node.bounds[1],
        raw_node.bounds[2] - parent_node.bounds[0],
        raw_node.bounds[3] - parent_node.bounds[1],
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(value)).strip()


def states_from_state_description(value: Any) -> list[str]:
    states: list[str] = []
    for part in state_description_parts(value):
        token = _STATE_DESCRIPTION_STATE_MAP.get(canonical_text_key(part))
        if token and token not in states:
            states.append(token)
    return states


def label_from_state_description(value: Any) -> str:
    return semantic_state_description_remainder(value)


def state_description_parts(value: Any) -> list[str]:
    normalized = normalized_text_surface(value)
    if not normalized:
        return []
    parts = [
        part.strip()
        for part in _STATE_DESCRIPTION_SPLIT_RE.split(normalized)
        if part.strip()
    ]
    if parts:
        return parts
    return [normalized]
