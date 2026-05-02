"""Typed shared public screen projection models."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Literal, TypeAlias, cast, get_args

from pydantic import (
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from .base import DaemonWireModel

PublicGroupName = Literal["targets", "keyboard", "system", "context", "dialog"]
BlockingGroupName = Literal["dialog", "keyboard", "system"]
OmittedReason = Literal["offscreen", "virtualized", "structureCollapsed"]
TransientKind = Literal["toast", "snackbar", "banner"]
AppMatchType = Literal["exact", "alias"]
PublicItemKind = Literal["node", "container", "text"]
ScrollDirection = Literal["up", "down", "left", "right", "backward"]
PublicNodeRole: TypeAlias = Literal[
    "button",
    "input",
    "switch",
    "checkbox",
    "radio",
    "tab",
    "keyboard-key",
    "image",
    "list-item",
    "text",
    "container",
    "dialog",
    "scroll-container",
]
PublicNodeAction: TypeAlias = Literal[
    "tap",
    "longTap",
    "type",
    "scroll",
    "focus",
    "submit",
]
PublicNodeState: TypeAlias = Literal[
    "checked",
    "unchecked",
    "selected",
    "disabled",
    "focused",
    "password",
    "expanded",
    "collapsed",
]

PUBLIC_GROUP_NAMES: tuple[PublicGroupName, ...] = (
    "targets",
    "keyboard",
    "system",
    "context",
    "dialog",
)
BLOCKING_GROUP_NAMES: tuple[BlockingGroupName, ...] = (
    "dialog",
    "keyboard",
    "system",
)
OMITTED_REASON_VALUES: tuple[OmittedReason, ...] = (
    "offscreen",
    "virtualized",
    "structureCollapsed",
)
TRANSIENT_KIND_VALUES: tuple[TransientKind, ...] = (
    "toast",
    "snackbar",
    "banner",
)
SCROLL_DIRECTION_VALUES: tuple[ScrollDirection, ...] = cast(
    tuple[ScrollDirection, ...],
    get_args(ScrollDirection),
)
PUBLIC_NODE_ROLE_VALUES: tuple[PublicNodeRole, ...] = cast(
    tuple[PublicNodeRole, ...],
    get_args(PublicNodeRole),
)
PUBLIC_NODE_ACTION_VALUES: tuple[PublicNodeAction, ...] = cast(
    tuple[PublicNodeAction, ...],
    get_args(PublicNodeAction),
)
PUBLIC_NODE_STATE_VALUES: tuple[PublicNodeState, ...] = cast(
    tuple[PublicNodeState, ...],
    get_args(PublicNodeState),
)
PUBLIC_NODE_ORIGIN_VALUES: tuple[str, ...] = ()
PUBLIC_NODE_AMBIGUITY_VALUES: tuple[str, ...] = ()
REQUIRED_SCREEN_SEQUENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("groups", "groups"),
    ("omitted", "omitted"),
    ("visible_windows", "visibleWindows"),
    ("transient", "transient"),
)
PUBLIC_REF_RE = re.compile(r"^n[1-9][0-9]*$")


def _list_to_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _iter_public_nodes(nodes: tuple[PublicNode, ...]) -> Iterator[PublicNode]:
    for node in nodes:
        yield node
        yield from _iter_public_nodes(node.children)


def _serializes_public_ref(node: PublicNode) -> bool:
    return (node.kind or "node") != "text" and bool(node.ref)


def _validate_registry_token(
    *,
    field_name: str,
    value: str | None,
    allowed_values: tuple[str, ...],
    allow_empty: bool = False,
) -> str | None:
    if value is None:
        return None
    if allow_empty and value == "":
        return value
    if value not in allowed_values:
        if allowed_values:
            allowed = ", ".join(allowed_values)
            raise ValueError(f"{field_name} must be one of: {allowed}")
        raise ValueError(f"{field_name} does not define any public tokens")
    return value


class PublicScreenWireModel(DaemonWireModel):
    model_config = ConfigDict(strict=True)


class PublicSemanticMeta(PublicScreenWireModel):
    resource_id: str | None = None
    class_name: str = Field(min_length=1)


class PublicNode(PublicScreenWireModel):
    kind: PublicItemKind | None = None
    role: str = ""
    label: str = ""
    text: str | None = None
    ref: str | None = None
    state: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    bounds: tuple[int, int, int, int] | None = None
    meta: PublicSemanticMeta | None = None
    children: tuple[PublicNode, ...] = ()
    scroll_directions: tuple[ScrollDirection, ...] = ()
    submit_refs: tuple[str, ...] = ()
    within: str | None = None
    value: str | None = None
    origin: str | None = None
    window_ref: str | None = None
    ambiguity: str | None = None

    @field_validator(
        "state",
        "actions",
        "bounds",
        "children",
        "scroll_directions",
        "submit_refs",
        mode="before",
    )
    @classmethod
    def coerce_collections(cls, value: object) -> object:
        return _list_to_tuple(value)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        return cast(
            str,
            _validate_registry_token(
                field_name="role",
                value=value,
                allowed_values=PUBLIC_NODE_ROLE_VALUES,
                allow_empty=True,
            ),
        )

    @field_validator("actions")
    @classmethod
    def validate_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _validate_registry_token(
                field_name="actions",
                value=item,
                allowed_values=PUBLIC_NODE_ACTION_VALUES,
            )
        return value

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _validate_registry_token(
                field_name="state",
                value=item,
                allowed_values=PUBLIC_NODE_STATE_VALUES,
            )
        return value

    @field_validator("submit_refs")
    @classmethod
    def validate_submit_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError("submitRefs entries must be non-empty strings")
            if not PUBLIC_REF_RE.fullmatch(item):
                raise ValueError("submitRefs entries must be public refs like n1")
            if item in seen:
                raise ValueError("submitRefs entries must be unique")
            seen.add(item)
        return value

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str | None) -> str | None:
        return _validate_registry_token(
            field_name="origin",
            value=value,
            allowed_values=PUBLIC_NODE_ORIGIN_VALUES,
        )

    @field_validator("ambiguity")
    @classmethod
    def validate_ambiguity(cls, value: str | None) -> str | None:
        return _validate_registry_token(
            field_name="ambiguity",
            value=value,
            allowed_values=PUBLIC_NODE_AMBIGUITY_VALUES,
        )

    @model_validator(mode="before")
    @classmethod
    def normalize_shape(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("children") not in (None, (), []):
            payload.setdefault("kind", "container")
        elif "kind" not in payload:
            payload["kind"] = "node"
        if payload.get("kind") == "text" and ("role" in payload or "label" in payload):
            raise ValueError('kind="text" items cannot include role or label')
        if payload.get("ref") is None:
            payload.pop("meta", None)
        elif payload.get("meta") == {}:
            payload["meta"] = None
        return payload

    @model_validator(mode="after")
    def validate_shape(self) -> PublicNode:
        kind = self.kind or "node"
        if kind == "text":
            if not self.text:
                raise ValueError("text items must include text")
            if self.children:
                raise ValueError("text items cannot include children")
            if self.scroll_directions:
                raise ValueError("text items cannot include scrollDirections")
            if self.submit_refs:
                raise ValueError("text items cannot include submitRefs")
            return self
        if not self.role:
            raise ValueError("node items must include role")
        if not self.label:
            raise ValueError("node items must include label")
        if self.submit_refs and (self.role != "input" or not self.ref):
            raise ValueError("submitRefs requires an input node with a public ref")
        if kind == "node" and self.children:
            raise ValueError("node items cannot include children")
        if kind != "container" and self.scroll_directions:
            raise ValueError("only container items can include scrollDirections")
        return self

    @model_serializer(mode="plain")
    def serialize_model(self) -> dict[str, Any]:
        kind = self.kind or "node"
        if kind == "text":
            payload: dict[str, Any] = {
                "kind": "text",
                "text": self.text,
            }
            self._append_optional_fields(payload)
            return payload

        payload = {
            "role": self.role,
            "label": self.label,
        }
        if kind == "container":
            payload["kind"] = "container"
        if self.ref is not None:
            payload["ref"] = self.ref
            payload["state"] = list(self.state)
            payload["actions"] = list(self.actions)
            payload["bounds"] = None if self.bounds is None else list(self.bounds)
            payload["meta"] = {} if self.meta is None else self.meta.model_dump()
        self._append_optional_fields(payload)
        return payload

    def _append_optional_fields(self, payload: dict[str, Any]) -> None:
        if self.scroll_directions:
            payload["scrollDirections"] = list(self.scroll_directions)
        if self.submit_refs:
            payload["submitRefs"] = list(self.submit_refs)
        if self.within is not None:
            payload["within"] = self.within
        if self.value is not None:
            payload["value"] = self.value
        if self.origin is not None:
            payload["origin"] = self.origin
        if self.window_ref is not None:
            payload["windowRef"] = self.window_ref
        if self.ambiguity is not None:
            payload["ambiguity"] = self.ambiguity
        if self.children:
            payload["children"] = [
                child.model_dump(by_alias=True, mode="json") for child in self.children
            ]


PublicNode.model_rebuild()


class PublicApp(PublicScreenWireModel):
    package_name: str | None = None
    activity_name: str | None = None
    requested_package_name: str | None = None
    resolved_package_name: str | None = None
    match_type: AppMatchType | None = None

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.package_name is None:
            payload.pop("packageName", None)
        if self.activity_name is None:
            payload.pop("activityName", None)
        if self.requested_package_name is None:
            payload.pop("requestedPackageName", None)
        if self.resolved_package_name is None:
            payload.pop("resolvedPackageName", None)
        if self.match_type is None:
            payload.pop("matchType", None)
        return payload


class PublicFocus(PublicScreenWireModel):
    input_ref: str | None = None

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.input_ref is None:
            payload.pop("inputRef", None)
        return payload


class PublicSurface(PublicScreenWireModel):
    keyboard_visible: bool
    blocking_group: BlockingGroupName | None = None
    focus: PublicFocus

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.blocking_group is None:
            payload.pop("blockingGroup", None)
        return payload


class PublicGroup(PublicScreenWireModel):
    name: PublicGroupName
    nodes: tuple[PublicNode, ...] = ()

    @field_validator("nodes", mode="before")
    @classmethod
    def coerce_nodes(cls, value: object) -> object:
        return _list_to_tuple(value)


class OmittedEntry(PublicScreenWireModel):
    group: PublicGroupName
    reason: OmittedReason
    count: int | None = None

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.count is None:
            payload.pop("count", None)
        return payload


class VisibleWindow(PublicScreenWireModel):
    window_ref: str = Field(min_length=1)
    role: str = Field(min_length=1)
    focused: bool = False
    blocking: bool = False


class TransientItem(PublicScreenWireModel):
    text: str = Field(min_length=1)
    kind: TransientKind | None = None

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.kind is None:
            payload.pop("kind", None)
        return payload


class PublicScreen(PublicScreenWireModel):
    screen_id: str = Field(min_length=1)
    app: PublicApp
    surface: PublicSurface
    groups: tuple[PublicGroup, ...]
    omitted: tuple[OmittedEntry, ...] = ()
    visible_windows: tuple[VisibleWindow, ...] = ()
    transient: tuple[TransientItem, ...] = ()

    @field_validator("groups", "omitted", "visible_windows", "transient", mode="before")
    @classmethod
    def coerce_collections(cls, value: object) -> object:
        return _list_to_tuple(value)

    @model_validator(mode="before")
    @classmethod
    def validate_wire_presence(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        missing = [
            alias
            for field_name, alias in REQUIRED_SCREEN_SEQUENCE_FIELDS
            if field_name not in value and alias not in value
        ]
        if missing:
            missing_fields = ", ".join(missing)
            raise ValueError(
                f"screen payload must include {missing_fields} on the wire"
            )
        return value

    @model_validator(mode="after")
    def validate_groups(self) -> PublicScreen:
        group_names = tuple(group.name for group in self.groups)
        if set(group_names) != set(PUBLIC_GROUP_NAMES) or len(group_names) != len(
            PUBLIC_GROUP_NAMES
        ):
            raise ValueError("groups must contain each public group exactly once")
        blocking_group = self.surface.blocking_group
        if blocking_group is None:
            expected_order = PUBLIC_GROUP_NAMES
        else:
            expected_order = (blocking_group,) + tuple(
                group_name
                for group_name in PUBLIC_GROUP_NAMES
                if group_name != blocking_group
            )
        if group_names != expected_order:
            raise ValueError("groups order must match canonical public order")
        return self

    @model_validator(mode="after")
    def validate_window_refs(self) -> PublicScreen:
        declared_window_refs = {window.window_ref for window in self.visible_windows}
        unknown_window_refs = sorted(
            {
                node.window_ref
                for group in self.groups
                for node in _iter_public_nodes(group.nodes)
                if node.window_ref is not None
                and node.window_ref not in declared_window_refs
            }
        )
        if unknown_window_refs:
            raise ValueError(
                "windowRef values must reference declared visibleWindows members: "
                + ", ".join(unknown_window_refs)
            )
        return self

    @model_validator(mode="after")
    def validate_submit_refs(self) -> PublicScreen:
        nodes = [
            node for group in self.groups for node in _iter_public_nodes(group.nodes)
        ]
        has_submit_refs = any(node.submit_refs for node in nodes)
        declared_refs: dict[str, PublicNode] = {}
        duplicate_refs: set[str] = set()
        for node in nodes:
            if not _serializes_public_ref(node):
                continue
            ref = node.ref
            if ref is None:
                continue
            if ref in declared_refs:
                duplicate_refs.add(ref)
            else:
                declared_refs[ref] = node
        if has_submit_refs and duplicate_refs:
            raise ValueError(
                "public refs must be unique within a screen: "
                + ", ".join(sorted(duplicate_refs))
            )

        for node in nodes:
            if not node.submit_refs:
                continue
            if not node.ref or not PUBLIC_REF_RE.fullmatch(node.ref):
                raise ValueError(
                    "nodes with submitRefs must have a valid public ref like n1"
                )
            for target_ref in node.submit_refs:
                if target_ref not in declared_refs:
                    raise ValueError(
                        "submitRefs values must reference same-screen public refs: "
                        + target_ref
                    )
                if target_ref == node.ref:
                    raise ValueError("submitRefs cannot reference the source node")
        return self


__all__ = [
    "BLOCKING_GROUP_NAMES",
    "BlockingGroupName",
    "OmittedEntry",
    "OmittedReason",
    "OMITTED_REASON_VALUES",
    "PUBLIC_GROUP_NAMES",
    "PUBLIC_NODE_ACTION_VALUES",
    "PUBLIC_NODE_AMBIGUITY_VALUES",
    "PUBLIC_NODE_ORIGIN_VALUES",
    "PUBLIC_NODE_ROLE_VALUES",
    "PUBLIC_NODE_STATE_VALUES",
    "PUBLIC_REF_RE",
    "PublicApp",
    "PublicFocus",
    "PublicGroup",
    "PublicGroupName",
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
    "TRANSIENT_KIND_VALUES",
    "VisibleWindow",
]
