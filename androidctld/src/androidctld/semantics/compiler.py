"""Semantic compiler for screen-first flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from androidctld.semantics.labels import (
    GENERIC_SEMANTIC_ROLES,
    LABEL_QUALITY_CONTENT_DESC,
    LABEL_QUALITY_HINT,
    LABEL_QUALITY_NEARBY,
    LABEL_QUALITY_RESOURCE_ID,
    LABEL_QUALITY_STATE_DESCRIPTION,
    LABEL_QUALITY_TEXT,
    LabelInfo,
    extract_state,
    fallback_label,
    infer_role,
    label_from_state_description,
    near_label_for_node,
    normalize_text,
    parent_node_for,
    relative_bounds_for_node,
    semantic_role,
    sibling_labels_for_node,
    state_description_parts,
    states_from_state_description,
    synthesize_label_info,
)
from androidctld.semantics.models import RelationScopeNode, SemanticMeta
from androidctld.semantics.public_models import (
    BlockingGroupName,
    PublicApp,
    PublicFocus,
    PublicGroupName,
    PublicNode,
    PublicScreen,
    PublicSurface,
    build_public_groups,
)
from androidctld.semantics.registries import GROUP_ORDER
from androidctld.semantics.submit_refs import assign_submit_ref_relations
from androidctld.semantics.surface import (
    apply_blocking_policy,
    build_action_surface_fingerprint,
    context_sort_key,
    infer_group,
    node_to_public_node,
    passive_node_dedup_key,
    resolve_blocking_group,
    score_node,
    scroll_directions_for_raw_node,
    semantic_node_fingerprint,
    semantic_relation_identity,
    semantic_relation_key,
    stable_screen_id,
    target_sort_key,
)
from androidctld.semantics.targets import (
    CLICK_ACTIONS,
    LONG_CLICK_ACTIONS,
    SCROLL_ACTIONS,
    TYPE_ACTIONS,
    actionable_anchor_for,
    ancestor_distance,
    can_promote_to_actionable_ancestor,
    normalize_action_name,
    public_primary_actions_for,
    secondary_public_actions_for,
    select_target_sources,
    semantic_actions_for,
    target_source_sort_key,
)
from androidctld.snapshots.models import RawNode, RawSnapshot


@dataclass
class SemanticNode:
    raw_rid: str
    role: str
    label: str
    state: list[str]
    actions: list[str]
    bounds: tuple[int, int, int, int]
    meta: SemanticMeta
    targetable: bool
    score: int
    group: str
    parent_role: str
    parent_label: str
    sibling_labels: list[str]
    relative_bounds: tuple[int, int, int, int]
    scroll_directions: list[str] = field(default_factory=list)
    text_children: list[str] = field(default_factory=list)
    submit_target_keys: list[tuple[str, str]] = field(default_factory=list)
    submit_relation_tokens: list[str] = field(default_factory=list)
    label_quality: int = 0
    ref: str = ""
    relation_anchor_rid: str = ""
    relation_window_id: str = ""
    relation_window_root_rid: str | None = None
    relation_parent_rid: str | None = None
    relation_ancestor_scopes: tuple[RelationScopeNode, ...] = field(
        default_factory=tuple
    )

    @property
    def resource_id(self) -> str:
        return self.meta.resource_id or ""

    @property
    def class_name(self) -> str:
        return self.meta.class_name


@dataclass
class CompiledScreen:
    screen_id: str
    sequence: int
    source_snapshot_id: int
    captured_at: str
    package_name: str | None
    activity_name: str | None
    keyboard_visible: bool
    action_surface_fingerprint: str = ""
    blocking_group: BlockingGroupName | None = None
    targets: list[SemanticNode] = field(default_factory=list)
    context: list[SemanticNode] = field(default_factory=list)
    dialog: list[SemanticNode] = field(default_factory=list)
    keyboard: list[SemanticNode] = field(default_factory=list)
    system: list[SemanticNode] = field(default_factory=list)

    def focused_input_node(self) -> SemanticNode | None:
        for group_name in cast(
            tuple[PublicGroupName, ...],
            ("targets", "dialog", "keyboard", "system", "context"),
        ):
            nodes = cast(list[SemanticNode], getattr(self, group_name))
            for node in nodes:
                if node.role != "input":
                    continue
                if "focused" not in node.state:
                    continue
                return node
        return None

    def ref_candidates(self) -> list[SemanticNode]:
        candidate_groups: tuple[PublicGroupName, ...] = (
            (self.blocking_group,)
            if self.blocking_group is not None
            else cast(
                tuple[PublicGroupName, ...],
                ("targets", "dialog", "keyboard", "system"),
            )
        )
        candidates: list[SemanticNode] = []
        seen_candidates: set[tuple[str, str]] = set()
        for group_name in candidate_groups:
            nodes = cast(list[SemanticNode], getattr(self, group_name))
            for node in nodes:
                candidate_key = (node.group, node.raw_rid)
                if not node.actions or candidate_key in seen_candidates:
                    continue
                candidates.append(node)
                seen_candidates.add(candidate_key)
        focused_input = self.focused_input_node()
        if focused_input is not None:
            candidate_key = (focused_input.group, focused_input.raw_rid)
            if candidate_key not in seen_candidates:
                candidates.append(focused_input)
        return candidates

    def group_order(self) -> tuple[PublicGroupName, ...]:
        if self.blocking_group is None:
            return cast(tuple[PublicGroupName, ...], GROUP_ORDER)
        return (
            self.blocking_group,
            *(
                cast(PublicGroupName, name)
                for name in GROUP_ORDER
                if name != self.blocking_group
            ),
        )

    def focused_input_ref(self) -> str | None:
        focused_input = self.focused_input_node()
        if focused_input is None or not focused_input.ref:
            return None
        return focused_input.ref

    def to_public_screen(self) -> PublicScreen:
        submit_refs_by_node_id = self._submit_refs_by_node_id()
        return PublicScreen(
            screen_id=self.screen_id,
            app=PublicApp(
                package_name=self.package_name,
                activity_name=self.activity_name,
            ),
            surface=PublicSurface(
                keyboard_visible=self.keyboard_visible,
                blocking_group=self.blocking_group,
                focus=PublicFocus(input_ref=self.focused_input_ref()),
            ),
            groups=build_public_groups(
                order=self.group_order(),
                targets=self._public_nodes_for(self.targets, submit_refs_by_node_id),
                keyboard=self._public_nodes_for(self.keyboard, submit_refs_by_node_id),
                system=self._public_nodes_for(self.system, submit_refs_by_node_id),
                context=self._public_nodes_for(self.context, submit_refs_by_node_id),
                dialog=self._public_nodes_for(self.dialog, submit_refs_by_node_id),
            ),
            omitted=(),
            visible_windows=(),
            transient=(),
        )

    def _public_nodes_for(
        self,
        nodes: list[SemanticNode],
        submit_refs_by_node_id: dict[int, tuple[str, ...]],
    ) -> tuple[PublicNode, ...]:
        return tuple(
            node_to_public_node(
                node,
                submit_refs=submit_refs_by_node_id.get(id(node), ()),
            )
            for node in nodes
        )

    def _submit_refs_by_node_id(self) -> dict[int, tuple[str, ...]]:
        nodes = [
            node
            for group_name in self.group_order()
            for node in cast(list[SemanticNode], getattr(self, group_name))
        ]
        nodes_by_key: dict[tuple[str, str], list[SemanticNode]] = {}
        for node in nodes:
            for node_key in _submit_relation_lookup_keys(node.group, node):
                nodes_by_key.setdefault(node_key, []).append(node)

        resolved: dict[int, tuple[str, ...]] = {}
        for node in nodes:
            if node.role != "input" or not node.ref or not node.submit_target_keys:
                continue
            target_refs: list[str] = []
            complete = True
            for target_key in node.submit_target_keys:
                matches = nodes_by_key.get(target_key, [])
                if len(matches) != 1 or not matches[0].ref:
                    complete = False
                    break
                target_refs.append(matches[0].ref)
            if complete:
                resolved[id(node)] = tuple(target_refs)
        return resolved


class SemanticCompiler:
    def compile(self, sequence: int, snapshot: RawSnapshot) -> CompiledScreen:
        ime_window_id = snapshot.ime.window_id if snapshot.ime.visible else None
        raw_nodes_by_rid = {node.rid: node for node in snapshot.nodes}
        root_rid_by_window = {
            window.window_id: window.root_rid for window in snapshot.windows
        }
        label_infos = {
            node.rid: synthesize_label_info(node, raw_nodes_by_rid)
            for node in snapshot.nodes
        }
        actionability = {
            node.rid: semantic_actions_for(node) for node in snapshot.nodes
        }
        target_sources = select_target_sources(
            snapshot.nodes,
            raw_nodes_by_rid,
            label_infos,
            actionability,
        )
        promoted_source_rids = {
            source_rid
            for anchor_rid, source_rid in target_sources.items()
            if source_rid != anchor_rid
        }
        semantic_nodes = []
        seen_passive_nodes: set[tuple[str, str, str, str, str]] = set()
        for raw_node in snapshot.nodes:
            if raw_node.rid in target_sources or raw_node.rid in promoted_source_rids:
                continue
            semantic_node = compile_node(
                raw_node,
                raw_nodes_by_rid,
                label_infos,
                actionability,
                ime_window_id=ime_window_id,
                root_rid_by_window=root_rid_by_window,
            )
            if semantic_node is not None:
                duplicate_key = passive_node_dedup_key(semantic_node)
                if duplicate_key is not None:
                    if duplicate_key in seen_passive_nodes:
                        continue
                    seen_passive_nodes.add(duplicate_key)
                semantic_nodes.append(semantic_node)
        for anchor_rid, source_rid in target_sources.items():
            semantic_node = compile_node(
                raw_nodes_by_rid[source_rid],
                raw_nodes_by_rid,
                label_infos,
                actionability,
                ime_window_id=ime_window_id,
                root_rid_by_window=root_rid_by_window,
                action_node=raw_nodes_by_rid[anchor_rid],
            )
            if semantic_node is not None:
                semantic_nodes.append(semantic_node)

        grouped_nodes = {
            "targets": sorted(
                [node for node in semantic_nodes if node.group == "targets"],
                key=target_sort_key,
            ),
            "keyboard": sorted(
                [node for node in semantic_nodes if node.group == "keyboard"],
                key=target_sort_key,
            ),
            "system": sorted(
                [node for node in semantic_nodes if node.group == "system"],
                key=target_sort_key,
            ),
            "context": sorted(
                [node for node in semantic_nodes if node.group == "context"],
                key=context_sort_key,
            ),
            "dialog": sorted(
                [node for node in semantic_nodes if node.group == "dialog"],
                key=target_sort_key,
            ),
        }
        blocking_group = resolve_blocking_group(
            snapshot=snapshot,
            grouped_nodes=grouped_nodes,
        )
        apply_blocking_policy(grouped_nodes, blocking_group)
        assign_submit_ref_relations(
            grouped_nodes=grouped_nodes,
            blocking_group=blocking_group,
        )
        action_surface_fingerprint = build_action_surface_fingerprint(
            snapshot=snapshot,
            grouped_nodes=grouped_nodes,
            blocking_group=blocking_group,
        )
        return CompiledScreen(
            screen_id=stable_screen_id(action_surface_fingerprint),
            sequence=sequence,
            source_snapshot_id=snapshot.snapshot_id,
            captured_at=snapshot.captured_at,
            package_name=snapshot.package_name,
            activity_name=snapshot.activity_name,
            keyboard_visible=snapshot.ime.visible,
            action_surface_fingerprint=action_surface_fingerprint,
            blocking_group=blocking_group,
            targets=grouped_nodes["targets"],
            context=grouped_nodes["context"],
            dialog=grouped_nodes["dialog"],
            keyboard=grouped_nodes["keyboard"],
            system=grouped_nodes["system"],
        )


__all__ = [
    "CLICK_ACTIONS",
    "GENERIC_SEMANTIC_ROLES",
    "LABEL_QUALITY_CONTENT_DESC",
    "LABEL_QUALITY_HINT",
    "LABEL_QUALITY_NEARBY",
    "LABEL_QUALITY_RESOURCE_ID",
    "LABEL_QUALITY_STATE_DESCRIPTION",
    "LABEL_QUALITY_TEXT",
    "LONG_CLICK_ACTIONS",
    "SCROLL_ACTIONS",
    "TYPE_ACTIONS",
    "CompiledScreen",
    "LabelInfo",
    "SemanticCompiler",
    "SemanticNode",
    "actionable_anchor_for",
    "ancestor_distance",
    "apply_blocking_policy",
    "build_action_surface_fingerprint",
    "can_promote_to_actionable_ancestor",
    "compile_node",
    "context_sort_key",
    "extract_state",
    "fallback_label",
    "infer_group",
    "infer_role",
    "label_from_state_description",
    "near_label_for_node",
    "node_to_public_node",
    "normalize_action_name",
    "normalize_text",
    "parent_node_for",
    "passive_node_dedup_key",
    "relative_bounds_for_node",
    "resolve_blocking_group",
    "score_node",
    "secondary_public_actions_for",
    "select_target_sources",
    "semantic_actions_for",
    "semantic_node_fingerprint",
    "semantic_role",
    "should_filter_node",
    "sibling_labels_for_node",
    "stable_screen_id",
    "state_description_parts",
    "states_from_state_description",
    "synthesize_label_info",
    "target_sort_key",
    "target_source_sort_key",
]


def compile_node(
    raw_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    label_infos: dict[str, LabelInfo],
    actionability: dict[str, list[str]],
    *,
    ime_window_id: str | None,
    root_rid_by_window: dict[str, str],
    action_node: RawNode | None = None,
) -> SemanticNode | None:
    if not raw_node.visible_to_user:
        return None
    anchor_node = action_node or raw_node
    primary_actions = actionability[anchor_node.rid]
    role = semantic_role(raw_node, anchor_node, primary_actions)
    public_primary_actions = public_primary_actions_for(
        anchor_node=anchor_node,
        role=role,
        primary_actions=primary_actions,
    )
    secondary_actions = secondary_public_actions_for(
        anchor_node=anchor_node,
        role=role,
        primary_actions=public_primary_actions,
    )
    public_actions = [*public_primary_actions, *secondary_actions]
    public_role = role
    scroll_directions: list[str] = []
    text_children: list[str] = []
    if "scroll" in public_actions and role == "container":
        public_role = "scroll-container"
        scroll_directions = list(scroll_directions_for_raw_node(anchor_node))
        text_children = list(
            scroll_container_text_children(
                raw_node=anchor_node,
                raw_nodes_by_rid=raw_nodes_by_rid,
                label_infos=label_infos,
            )
        )
    label_info = label_infos[raw_node.rid]
    label = label_info.label
    targetable = bool(public_actions)
    if should_filter_node(raw_node, public_role, label, targetable):
        return None
    parent_node = parent_node_for(raw_node, raw_nodes_by_rid)
    parent_role = infer_role(parent_node) if parent_node is not None else ""
    parent_label = (
        label_infos[parent_node.rid].label or fallback_label(parent_node)
        if parent_node is not None
        else ""
    )
    return SemanticNode(
        raw_rid=anchor_node.rid,
        role=public_role,
        label=label or fallback_label(raw_node),
        state=extract_state(anchor_node),
        actions=public_actions,
        bounds=anchor_node.bounds,
        meta=SemanticMeta(
            resource_id=raw_node.resource_id,
            class_name=raw_node.class_name,
        ),
        targetable=targetable,
        score=score_node(anchor_node, public_role, targetable),
        group=(
            "dialog"
            if parent_role == "dialog"
            else infer_group(
                anchor_node,
                public_role,
                targetable,
                ime_window_id=ime_window_id,
            )
        ),
        parent_role=parent_role,
        parent_label=parent_label,
        sibling_labels=sibling_labels_for_node(raw_node, raw_nodes_by_rid, label_infos),
        relative_bounds=relative_bounds_for_node(raw_node, parent_node),
        scroll_directions=scroll_directions,
        text_children=text_children,
        label_quality=label_info.quality,
        relation_anchor_rid=anchor_node.rid,
        relation_window_id=anchor_node.window_id,
        relation_window_root_rid=root_rid_by_window.get(anchor_node.window_id),
        relation_parent_rid=anchor_node.parent_rid,
        relation_ancestor_scopes=_relation_ancestor_scopes(
            anchor_node=anchor_node,
            raw_nodes_by_rid=raw_nodes_by_rid,
            root_rid_by_window=root_rid_by_window,
        ),
    )


def _submit_relation_lookup_keys(
    group_name: str,
    node: SemanticNode,
) -> tuple[tuple[str, str], ...]:
    keys = [semantic_relation_key(group_name, node)]
    if node.relation_anchor_rid:
        keys.append(
            (
                group_name,
                "|".join(
                    (
                        semantic_relation_identity(group_name, node),
                        f"anchor:{node.relation_anchor_rid}",
                    )
                ),
            )
        )
    return tuple(keys)


def _relation_ancestor_scopes(
    *,
    anchor_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    root_rid_by_window: dict[str, str],
) -> tuple[RelationScopeNode, ...]:
    scopes: list[RelationScopeNode] = []
    seen_rids: set[str] = set()
    parent_rid = anchor_node.parent_rid
    while parent_rid is not None and parent_rid not in seen_rids:
        seen_rids.add(parent_rid)
        parent_node = raw_nodes_by_rid.get(parent_rid)
        if parent_node is None:
            break
        scopes.append(_relation_scope_node(parent_node, root_rid_by_window))
        parent_rid = parent_node.parent_rid
    return tuple(scopes)


def _relation_scope_node(
    node: RawNode,
    root_rid_by_window: dict[str, str],
) -> RelationScopeNode:
    return RelationScopeNode(
        rid=node.rid,
        window_id=node.window_id,
        parent_rid=node.parent_rid,
        bounds=node.bounds,
        resource_id=node.resource_id or "",
        class_name=node.class_name,
        text=node.text or "",
        content_desc=node.content_desc or "",
        pane_title=node.pane_title or "",
        is_window_root=node.rid == root_rid_by_window.get(node.window_id),
    )


def scroll_container_text_children(
    *,
    raw_node: RawNode,
    raw_nodes_by_rid: dict[str, RawNode],
    label_infos: dict[str, LabelInfo],
) -> tuple[str, ...]:
    texts: list[str] = []
    for child_rid in raw_node.child_rids:
        child = raw_nodes_by_rid.get(child_rid)
        if child is None or not child.visible_to_user:
            continue
        text = label_infos[child.rid].label or fallback_label(child)
        normalized = normalize_text(text)
        if not normalized or normalized in texts:
            continue
        texts.append(normalized)
    if texts:
        return tuple(texts)

    fallback_text = label_infos[raw_node.rid].label or fallback_label(raw_node)
    normalized_fallback = normalize_text(fallback_text)
    if not normalized_fallback:
        return ()
    return (normalized_fallback,)


def should_filter_node(
    raw_node: RawNode,
    role: str,
    label: str,
    targetable: bool,
) -> bool:
    if not targetable and not raw_node.important_for_accessibility:
        return True
    if role == "container" and not label and not targetable:
        return True
    return not label and not targetable and role in {"image", "text"}
