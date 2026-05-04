"""Standalone public screen XML rendering owned by the daemon."""

from __future__ import annotations

from collections.abc import Sequence
from xml.etree.ElementTree import Element, SubElement, tostring

from androidctl_contracts.public_screen import (
    BLOCKING_GROUP_NAMES,
    PUBLIC_GROUP_NAMES,
    PUBLIC_NODE_ROLE_VALUES,
    BlockingGroupName,
    PublicGroupName,
)
from androidctld.semantics.public_models import (
    OmittedEntry,
    PublicApp,
    PublicFocus,
    PublicGroup,
    PublicNode,
    PublicScreen,
    PublicSurface,
    TransientItem,
    VisibleWindow,
)

XmlAttrs = dict[str, str]

_APP_ATTRS = (
    "packageName",
    "activityName",
    "requestedPackageName",
    "resolvedPackageName",
    "matchType",
)
_FOCUS_ATTRS = ("inputRef",)
_VISIBLE_WINDOW_ATTRS = ("windowRef", "role", "focused", "blocking")
_BLOCKING_GROUPS: set[BlockingGroupName] = set(BLOCKING_GROUP_NAMES)
_NODE_ROLE_TAGS = frozenset(PUBLIC_NODE_ROLE_VALUES)


def render_screen_xml(screen: PublicScreen) -> str:
    """Render a standalone public ``<screen>`` artifact."""

    root = Element("screen", {"screenId": screen.screen_id})
    _append_app(root, screen.app)
    _append_surface(root, screen.surface)
    _append_groups(root, screen.groups, blocking_group=screen.surface.blocking_group)
    _append_omitted(root, screen.omitted)
    _append_visible_windows(root, screen.visible_windows)
    _append_transient(root, screen.transient)
    return tostring(root, encoding="unicode", short_empty_elements=True)


def _append_app(parent: Element, app: PublicApp) -> None:
    attrs = _non_empty_attrs(
        {
            "packageName": app.package_name,
            "activityName": app.activity_name,
            "requestedPackageName": app.requested_package_name,
            "resolvedPackageName": app.resolved_package_name,
            "matchType": app.match_type,
        },
        _APP_ATTRS,
    )
    SubElement(parent, "app", attrs)


def _append_surface(parent: Element, surface: PublicSurface) -> None:
    attrs = {"keyboardVisible": _bool_string(surface.keyboard_visible)}
    if surface.blocking_group in _BLOCKING_GROUPS:
        attrs["blockingGroup"] = surface.blocking_group
    surface_elem = SubElement(parent, "surface", attrs)
    _append_focus(surface_elem, surface.focus)


def _append_focus(parent: Element, focus: PublicFocus) -> None:
    SubElement(
        parent,
        "focus",
        _non_empty_attrs({"inputRef": focus.input_ref}, _FOCUS_ATTRS),
    )


def _append_groups(
    parent: Element,
    groups: tuple[PublicGroup, ...],
    *,
    blocking_group: BlockingGroupName | None,
) -> None:
    groups_elem = SubElement(parent, "groups")
    groups_by_name = {group.name: group for group in groups}
    for group_name in _group_order(blocking_group):
        group_elem = SubElement(groups_elem, group_name)
        group = groups_by_name.get(group_name)
        if group is None:
            continue
        for node in group.nodes:
            _append_group_item(group_elem, node)


def _append_group_item(parent: Element, node: PublicNode) -> None:
    if node.kind == "text":
        text_elem = SubElement(parent, "literal", _text_attrs(node))
        text_elem.text = node.text
        return

    tag = _node_role_tag(node.role)
    node_elem = SubElement(parent, tag, _node_attrs(node))
    for child in node.children:
        _append_group_item(node_elem, child)


def _append_omitted(parent: Element, omitted: tuple[OmittedEntry, ...]) -> None:
    omitted_elem = SubElement(parent, "omitted")
    for item in omitted:
        attrs: XmlAttrs = {
            "group": item.group,
            "reason": item.reason,
        }
        if item.count is not None:
            attrs["count"] = str(item.count)
        SubElement(omitted_elem, "entry", attrs)


def _append_visible_windows(
    parent: Element,
    visible_windows: tuple[VisibleWindow, ...],
) -> None:
    windows_elem = SubElement(parent, "visibleWindows")
    for window in visible_windows:
        SubElement(
            windows_elem,
            "window",
            _attrs(
                {
                    "windowRef": window.window_ref,
                    "role": window.role,
                    "focused": window.focused,
                    "blocking": window.blocking,
                },
                _VISIBLE_WINDOW_ATTRS,
            ),
        )


def _append_transient(parent: Element, transient: tuple[TransientItem, ...]) -> None:
    transient_elem = SubElement(parent, "transient")
    for item in transient:
        attrs: XmlAttrs = {}
        if item.kind is not None:
            attrs["kind"] = item.kind
        SubElement(transient_elem, "item", attrs).text = item.text


def _node_attrs(node: PublicNode) -> XmlAttrs:
    attrs: XmlAttrs = {}
    if node.ref is not None:
        attrs["ref"] = node.ref
    if node.ref is not None:
        attrs.update(
            _non_empty_sequence_attrs(
                {
                    "actions": node.actions,
                    "state": node.state,
                },
                ("actions", "state"),
            )
        )
    if node.scroll_directions:
        attrs["scrollDirections"] = _sequence_attr(node.scroll_directions)
    if node.submit_refs:
        attrs["submitRefs"] = _sequence_attr(node.submit_refs)
    attrs.update(_text_attrs(node))
    return attrs


def _text_attrs(node: PublicNode) -> XmlAttrs:
    attrs: XmlAttrs = {}
    if node.origin is not None:
        attrs["origin"] = node.origin
    if node.window_ref is not None:
        attrs["windowRef"] = node.window_ref
    if node.ambiguity is not None:
        attrs["ambiguity"] = node.ambiguity
    if node.kind != "text" and node.label:
        attrs["label"] = node.label
    if node.within is not None:
        attrs["within"] = node.within
    if node.value is not None:
        attrs["value"] = node.value
    return attrs


def _group_order(
    blocking_group: BlockingGroupName | None,
) -> tuple[PublicGroupName, ...]:
    if blocking_group not in _BLOCKING_GROUPS:
        return PUBLIC_GROUP_NAMES
    return (
        blocking_group,
        *(
            group_name
            for group_name in PUBLIC_GROUP_NAMES
            if group_name != blocking_group
        ),
    )


def _attrs(values: dict[str, object], keys: Sequence[str]) -> XmlAttrs:
    attrs: XmlAttrs = {}
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            attrs[key] = _bool_string(value)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            attrs[key] = _sequence_attr(value)
        else:
            attrs[key] = str(value)
    return attrs


def _non_empty_attrs(values: dict[str, object], keys: Sequence[str]) -> XmlAttrs:
    return {
        key: str(value)
        for key in keys
        if isinstance((value := values.get(key)), str) and value
    }


def _non_empty_sequence_attrs(
    values: dict[str, object],
    keys: Sequence[str],
) -> XmlAttrs:
    attrs: XmlAttrs = {}
    for key in keys:
        value = values.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str) and value:
            attrs[key] = _sequence_attr(value)
    return attrs


def _sequence_attr(values: Sequence[object]) -> str:
    return " ".join(str(item) for item in values)


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def _node_role_tag(role: str) -> str:
    if role not in _NODE_ROLE_TAGS:
        raise ValueError(f"unsupported public node role for XML tag: {role}")
    return role
