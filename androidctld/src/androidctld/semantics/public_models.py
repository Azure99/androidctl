"""Daemon helpers for shared public screen contract models."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from androidctl_contracts.public_screen import (
    BLOCKING_GROUP_NAMES,
    PUBLIC_GROUP_NAMES,
    PUBLIC_NODE_ACTION_VALUES,
    PUBLIC_NODE_AMBIGUITY_VALUES,
    PUBLIC_NODE_ORIGIN_VALUES,
    PUBLIC_NODE_ROLE_VALUES,
    PUBLIC_NODE_STATE_VALUES,
    SCROLL_DIRECTION_VALUES,
    AppMatchType,
    BlockingGroupName,
    OmittedEntry,
    OmittedReason,
    PublicApp,
    PublicFocus,
    PublicGroup,
    PublicGroupName,
    PublicItemKind,
    PublicNode,
    PublicNodeAction,
    PublicNodeRole,
    PublicNodeState,
    PublicScreen,
    PublicSemanticMeta,
    PublicSurface,
    ScrollDirection,
    TransientItem,
    TransientKind,
    VisibleWindow,
)

GROUP_NAMES = PUBLIC_GROUP_NAMES


def build_public_groups(
    *,
    order: tuple[PublicGroupName, ...] = PUBLIC_GROUP_NAMES,
    targets: tuple[PublicNode, ...] = (),
    keyboard: tuple[PublicNode, ...] = (),
    system: tuple[PublicNode, ...] = (),
    context: tuple[PublicNode, ...] = (),
    dialog: tuple[PublicNode, ...] = (),
) -> tuple[PublicGroup, ...]:
    nodes_by_group: dict[PublicGroupName, tuple[PublicNode, ...]] = {
        "targets": targets,
        "keyboard": keyboard,
        "system": system,
        "context": context,
        "dialog": dialog,
    }
    if set(order) != set(PUBLIC_GROUP_NAMES) or len(order) != len(PUBLIC_GROUP_NAMES):
        raise ValueError("groups order must contain each public group exactly once")
    return tuple(
        PublicGroup(name=group_name, nodes=nodes_by_group[group_name])
        for group_name in order
    )


def public_groups_by_name(
    screen: PublicScreen,
) -> dict[PublicGroupName, tuple[PublicNode, ...]]:
    return {group.name: group.nodes for group in screen.groups}


def public_group_nodes(
    screen: PublicScreen,
    group_name: PublicGroupName,
) -> tuple[PublicNode, ...]:
    return public_groups_by_name(screen).get(group_name, ())


def iter_public_nodes(nodes: Iterable[PublicNode]) -> Iterator[PublicNode]:
    for node in nodes:
        yield node
        yield from iter_public_nodes(node.children)


def dump_public_screen(screen: PublicScreen) -> dict[str, object]:
    return screen.model_dump(by_alias=True, mode="json")


__all__ = [
    "AppMatchType",
    "BLOCKING_GROUP_NAMES",
    "BlockingGroupName",
    "GROUP_NAMES",
    "OmittedEntry",
    "OmittedReason",
    "PUBLIC_GROUP_NAMES",
    "PUBLIC_NODE_ACTION_VALUES",
    "PUBLIC_NODE_AMBIGUITY_VALUES",
    "PUBLIC_NODE_ORIGIN_VALUES",
    "PUBLIC_NODE_ROLE_VALUES",
    "PUBLIC_NODE_STATE_VALUES",
    "PublicApp",
    "PublicFocus",
    "PublicGroup",
    "PublicGroupName",
    "PublicItemKind",
    "PublicNode",
    "PublicNodeAction",
    "PublicNodeRole",
    "PublicNodeState",
    "PublicScreen",
    "PublicSemanticMeta",
    "PublicSurface",
    "SCROLL_DIRECTION_VALUES",
    "ScrollDirection",
    "TransientItem",
    "TransientKind",
    "VisibleWindow",
    "build_public_groups",
    "dump_public_screen",
    "iter_public_nodes",
    "public_group_nodes",
    "public_groups_by_name",
]
