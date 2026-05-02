"""High-confidence input-to-submit-control attribution."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from androidctld.semantics.labels import (
    LABEL_QUALITY_CONTENT_DESC,
    LABEL_QUALITY_HINT,
    LABEL_QUALITY_TEXT,
)
from androidctld.semantics.models import RelationScopeNode
from androidctld.semantics.public_models import BlockingGroupName, PublicGroupName
from androidctld.semantics.registries import GROUP_ORDER
from androidctld.semantics.surface import (
    semantic_relation_identity,
    semantic_relation_key,
)
from androidctld.text_equivalence import canonical_text_key

if TYPE_CHECKING:
    from androidctld.semantics.compiler import SemanticNode


NodeKey = tuple[str, str]

MAX_SCOPE_DISTANCE = 4
EDGE_TOLERANCE_PX = 4
MIN_STACKED_HORIZONTAL_OVERLAP = 0.35
MIN_INLINE_VERTICAL_OVERLAP = 0.50
MAX_STACKED_GAP_MULTIPLIER = 2.5
MAX_INLINE_GAP_MULTIPLIER = 1.5
MIN_GAP_CAP_PX = 24

_GLOBAL_SCOPE_TOKENS = (
    "toolbar",
    "actionbar",
    "statusbar",
    "navigationbar",
    "systembar",
    "urlbar",
    "omnibox",
    "addressbar",
    "tabstrip",
    "appbar",
)
_FORM_CONTAINER_CLASSES = frozenset({"android.webkit.webview"})

_SUBMIT_LABELS = frozenset(
    {
        "submit",
        "send",
        "search",
        "go",
        "done",
        "next",
        "enter",
        "continue",
        "sign in",
        "signin",
        "log in",
        "login",
    }
)
_HIGH_CONFIDENCE_LABEL_QUALITIES = {
    LABEL_QUALITY_HINT,
    LABEL_QUALITY_CONTENT_DESC,
    LABEL_QUALITY_TEXT,
}


def assign_submit_ref_relations(
    *,
    grouped_nodes: dict[str, list[SemanticNode]],
    blocking_group: BlockingGroupName | None,
) -> None:
    """Attach daemon-internal submit attribution when a scope is unambiguous."""

    clear_submit_ref_relations(grouped_nodes)
    for group_name in _active_relation_groups(blocking_group):
        nodes = grouped_nodes.get(group_name, [])
        focused_inputs = [node for node in nodes if _eligible_focused_input(node)]
        if len(focused_inputs) != 1:
            continue
        source = focused_inputs[0]
        candidates = _submit_control_candidates(
            source=source,
            nodes=nodes,
            group_name=group_name,
        )
        if len(candidates) != 1:
            continue
        target = candidates[0]
        source.submit_target_keys.append(_node_key(group_name, target))
        source.submit_relation_tokens.append(
            submit_relation_token(group_name, source, target)
        )


def clear_submit_ref_relations(grouped_nodes: dict[str, list[SemanticNode]]) -> None:
    for nodes in grouped_nodes.values():
        for node in nodes:
            node.submit_target_keys.clear()
            node.submit_relation_tokens.clear()


def _active_relation_groups(
    blocking_group: BlockingGroupName | None,
) -> tuple[PublicGroupName, ...]:
    if blocking_group is not None:
        return (blocking_group,)
    return cast(
        tuple[PublicGroupName, ...],
        tuple(group_name for group_name in GROUP_ORDER if group_name != "context"),
    )


def _eligible_focused_input(node: SemanticNode) -> bool:
    return (
        node.role == "input"
        and "disabled" not in node.state
        and "focused" in node.state
        and bool(node.actions or "focused" in node.state)
    )


def _submit_control_candidates(
    *,
    source: SemanticNode,
    nodes: list[SemanticNode],
    group_name: PublicGroupName,
) -> list[SemanticNode]:
    hard_candidates: list[tuple[SemanticNode, RelationScopeNode]] = []
    for target in nodes:
        if target is source:
            continue
        if not _eligible_submit_control(target, group_name=group_name):
            continue
        if source.relation_window_id != target.relation_window_id:
            continue
        relation_scope = relation_scope_for(source, target)
        if relation_scope is None:
            continue
        hard_candidates.append((target, relation_scope))
    return [
        target
        for target, relation_scope in hard_candidates
        if is_plausible_page_submit_geometry(source, target, relation_scope)
    ]


def _eligible_submit_control(
    node: SemanticNode,
    *,
    group_name: PublicGroupName,
) -> bool:
    if group_name == "keyboard":
        eligible_role = node.role in {"button", "keyboard-key"}
    else:
        eligible_role = node.role == "button"
    return (
        eligible_role
        and "tap" in node.actions
        and "disabled" not in node.state
        and node.label_quality in _HIGH_CONFIDENCE_LABEL_QUALITIES
        and canonical_text_key(node.label) in _SUBMIT_LABELS
    )


def relation_scope_for(
    source: SemanticNode,
    target: SemanticNode,
) -> RelationScopeNode | None:
    if not source.relation_window_id or not target.relation_window_id:
        return None
    if source.relation_window_id != target.relation_window_id:
        return None
    if (
        source.relation_parent_rid
        and source.relation_parent_rid == target.relation_parent_rid
    ):
        direct_scope = _ancestor_by_rid(source, source.relation_parent_rid)
        if direct_scope is not None and meaningful_relation_scope(
            direct_scope,
            source=source,
            target=target,
        ):
            return direct_scope

    target_distances = {
        scope.rid: distance
        for distance, scope in enumerate(target.relation_ancestor_scopes, start=1)
    }
    for source_distance, scope in enumerate(
        source.relation_ancestor_scopes,
        start=1,
    ):
        target_distance = target_distances.get(scope.rid)
        if target_distance is None:
            continue
        if source_distance > MAX_SCOPE_DISTANCE or target_distance > MAX_SCOPE_DISTANCE:
            continue
        if meaningful_relation_scope(scope, source=source, target=target):
            return scope
    return None


def meaningful_relation_scope(
    scope: RelationScopeNode,
    *,
    source: SemanticNode,
    target: SemanticNode,
) -> bool:
    if scope.window_id != source.relation_window_id:
        return False
    if scope.window_id != target.relation_window_id:
        return False
    if scope.is_window_root:
        return False
    if scope.rid in {
        source.relation_anchor_rid,
        target.relation_anchor_rid,
        source.relation_window_root_rid,
        target.relation_window_root_rid,
    }:
        return False
    if _is_rejected_global_scope(scope):
        return False
    return _has_meaningful_structural_signal(scope)


def is_plausible_page_submit_geometry(
    source: SemanticNode,
    target: SemanticNode,
    scope: RelationScopeNode,
) -> bool:
    if not _center_inside(source.bounds, scope.bounds):
        return False
    if not _center_inside(target.bounds, scope.bounds):
        return False
    return _accepted_stacked_geometry(source.bounds, target.bounds) or (
        _accepted_inline_geometry(source.bounds, target.bounds)
    )


def _node_key(group_name: PublicGroupName, node: SemanticNode) -> NodeKey:
    if not node.relation_anchor_rid:
        return semantic_relation_key(node.group, node)
    return (
        group_name,
        "|".join(
            (
                semantic_relation_identity(group_name, node),
                f"anchor:{node.relation_anchor_rid}",
            )
        ),
    )


def submit_relation_token(
    group_name: PublicGroupName,
    source: SemanticNode,
    target: SemanticNode,
) -> str:
    source_identity = semantic_relation_identity(group_name, source)
    target_identity = semantic_relation_identity(group_name, target)
    return f"{source_identity}->{target_identity}"


def _ancestor_by_rid(
    node: SemanticNode,
    rid: str,
) -> RelationScopeNode | None:
    for scope in node.relation_ancestor_scopes:
        if scope.rid == rid:
            return scope
    return None


def _is_rejected_global_scope(scope: RelationScopeNode) -> bool:
    if _resource_id_is_root_content(scope.resource_id):
        return True
    if _class_name_is_decor_view(scope.class_name):
        return True
    return any(
        token in _canonical_scope_value(value)
        for value in (
            scope.resource_id,
            scope.class_name,
            scope.text,
            scope.content_desc,
            scope.pane_title,
        )
        for token in _GLOBAL_SCOPE_TOKENS
    )


def _has_meaningful_structural_signal(scope: RelationScopeNode) -> bool:
    if scope.resource_id and not _resource_id_is_root_content(scope.resource_id):
        return True
    if scope.pane_title:
        return True
    if scope.text or scope.content_desc:
        return True
    return _canonical_class_name(scope.class_name) in _FORM_CONTAINER_CLASSES


def _resource_id_is_root_content(resource_id: str) -> bool:
    normalized = resource_id.strip().lower()
    if normalized == "android:id/content" or normalized.endswith("android:id/content"):
        return True
    key = canonical_text_key(resource_id)
    return key == "android id content" or key.endswith("android id content")


def _class_name_is_decor_view(class_name: str) -> bool:
    normalized = class_name.strip().lower()
    return normalized == "decorview" or normalized.endswith(".decorview")


def _canonical_scope_value(value: str) -> str:
    return canonical_text_key(value).replace(" ", "")


def _canonical_class_name(value: str) -> str:
    return value.strip().lower()


def _accepted_stacked_geometry(
    source_bounds: tuple[int, int, int, int],
    target_bounds: tuple[int, int, int, int],
) -> bool:
    source_width = _width(source_bounds)
    target_width = _width(target_bounds)
    source_height = _height(source_bounds)
    target_height = _height(target_bounds)
    if min(source_width, target_width, source_height, target_height) <= 0:
        return False
    if target_bounds[1] < source_bounds[3] - EDGE_TOLERANCE_PX:
        return False
    vertical_gap = max(0, target_bounds[1] - source_bounds[3])
    max_gap = max(
        MIN_GAP_CAP_PX,
        MAX_STACKED_GAP_MULTIPLIER * max(source_height, target_height),
    )
    if vertical_gap > max_gap:
        return False
    horizontal_overlap = _overlap(
        source_bounds[0],
        source_bounds[2],
        target_bounds[0],
        target_bounds[2],
    )
    return (
        horizontal_overlap / min(source_width, target_width)
        >= MIN_STACKED_HORIZONTAL_OVERLAP
    )


def _accepted_inline_geometry(
    source_bounds: tuple[int, int, int, int],
    target_bounds: tuple[int, int, int, int],
) -> bool:
    source_width = _width(source_bounds)
    target_width = _width(target_bounds)
    source_height = _height(source_bounds)
    target_height = _height(target_bounds)
    if min(source_width, target_width, source_height, target_height) <= 0:
        return False
    if target_bounds[0] < source_bounds[2] - EDGE_TOLERANCE_PX:
        return False
    horizontal_gap = max(0, target_bounds[0] - source_bounds[2])
    max_gap = max(
        MIN_GAP_CAP_PX,
        MAX_INLINE_GAP_MULTIPLIER * max(source_height, target_height),
    )
    if horizontal_gap > max_gap:
        return False
    vertical_overlap = _overlap(
        source_bounds[1],
        source_bounds[3],
        target_bounds[1],
        target_bounds[3],
    )
    return (
        vertical_overlap / min(source_height, target_height)
        >= MIN_INLINE_VERTICAL_OVERLAP
    )


def _center_inside(
    bounds: tuple[int, int, int, int],
    container_bounds: tuple[int, int, int, int],
) -> bool:
    center_x = (bounds[0] + bounds[2]) / 2
    center_y = (bounds[1] + bounds[3]) / 2
    return (
        container_bounds[0] <= center_x <= container_bounds[2]
        and container_bounds[1] <= center_y <= container_bounds[3]
    )


def _width(bounds: tuple[int, int, int, int]) -> int:
    return bounds[2] - bounds[0]


def _height(bounds: tuple[int, int, int, int]) -> int:
    return bounds[3] - bounds[1]


def _overlap(
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))
