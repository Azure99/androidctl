from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, TypeAlias, TypedDict, cast

from androidctl_contracts.public_screen import (
    BLOCKING_GROUP_NAMES,
    OMITTED_REASON_VALUES,
    PUBLIC_GROUP_NAMES,
    PUBLIC_NODE_ROLE_VALUES,
    TRANSIENT_KIND_VALUES,
    BlockingGroupName,
    OmittedReason,
    PublicGroupName,
    TransientKind,
)
from typing_extensions import NotRequired

from androidctl.renderers import (
    ProjectionDict,
    ProjectionValue,
    RenderPayload,
    projection_dict,
)

XmlScalarAttrs: TypeAlias = dict[str, str]
XmlGroupName: TypeAlias = PublicGroupName
XmlAttrRule: TypeAlias = tuple[str, Callable[[object], str | None]]
XmlGroupItemTag: TypeAlias = str


class XmlSurfaceProjection(TypedDict):
    attrs: XmlScalarAttrs
    focus: XmlScalarAttrs


class XmlNodeGroupItemProjection(TypedDict):
    tag: XmlGroupItemTag
    attrs: XmlScalarAttrs
    children: list[XmlNodeGroupItemProjection]
    text: NotRequired[str]


XmlGroupItemProjection: TypeAlias = XmlNodeGroupItemProjection


class XmlGroupProjection(TypedDict):
    name: XmlGroupName
    items: list[XmlGroupItemProjection]


class XmlOmittedEntryProjection(TypedDict):
    group: XmlGroupName
    reason: OmittedReason
    count: NotRequired[str]


class XmlTransientItemProjection(TypedDict):
    text: str
    kind: NotRequired[TransientKind]


class XmlScreenProjection(TypedDict):
    attrs: XmlScalarAttrs
    app: XmlScalarAttrs
    surface: XmlSurfaceProjection
    groups: list[XmlGroupProjection]
    omitted: list[XmlOmittedEntryProjection]
    visibleWindows: list[XmlScalarAttrs]
    transient: list[XmlTransientItemProjection]


class XmlActionTargetProjection(TypedDict):
    attrs: XmlScalarAttrs


class XmlSemanticProjection(TypedDict):
    kind: Literal["semantic"]
    attrs: XmlScalarAttrs
    message: str | None
    truth: XmlScalarAttrs
    actionTarget: XmlActionTargetProjection | None
    uncertainty: list[str]
    warnings: list[str]
    screen: XmlScreenProjection | None
    artifacts: XmlScalarAttrs


class XmlRetainedProjection(TypedDict):
    kind: Literal["retained"]
    attrs: XmlScalarAttrs
    message: str | None
    details: XmlScalarAttrs
    artifacts: XmlScalarAttrs


class XmlListAppsProjection(TypedDict):
    kind: Literal["listApps"]
    attrs: XmlScalarAttrs
    apps: list[XmlScalarAttrs]


XmlProjection: TypeAlias = (
    XmlSemanticProjection | XmlRetainedProjection | XmlListAppsProjection
)


_TRUTH_ATTRS = (
    "executionOutcome",
    "continuityStatus",
    "observationQuality",
    "changed",
)
_ACTION_TARGET_ATTRS = (
    "sourceRef",
    "sourceScreenId",
    "subjectRef",
    "dispatchedRef",
    "nextScreenId",
    "nextRef",
    "identityStatus",
    "evidence",
)
_APP_ATTRS = (
    "packageName",
    "activityName",
    "requestedPackageName",
    "resolvedPackageName",
    "matchType",
)
_FOCUS_ATTRS = ("inputRef",)
_SEMANTIC_ARTIFACT_ATTRS = ("screenshotPng", "screenXml")
_RETAINED_ARTIFACT_ATTRS = ("screenshotPng",)
_LIST_APP_ATTRS = ("packageName", "appLabel")
_RETAINED_DETAILS_ATTRS = (
    "sourceCode",
    "sourceKind",
    "operation",
    "reason",
    "expectedReleaseVersion",
    "actualReleaseVersion",
)
_RETAINED_SOURCE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_RETAINED_DETAIL_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RETAINED_RELEASE_VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$"
)
_DEVICE_SERIAL_DETAIL_RE = re.compile(r"^(?:emulator-\d+|[A-Z0-9]{6,})$")
_FINGERPRINT_DETAIL_RE = re.compile(r"^(?:[0-9a-f]{16,}|[0-9a-f]{2}:){7,}[0-9a-f]{2}$")
_SAFE_TOKEN_DETAIL_VALUES = frozenset({"wrong-token"})
_VISIBLE_WINDOW_ATTRS = ("windowRef", "role", "focused", "blocking")
_GROUP_ORDER: tuple[XmlGroupName, ...] = PUBLIC_GROUP_NAMES
_BLOCKING_GROUPS: set[BlockingGroupName] = set(BLOCKING_GROUP_NAMES)
_OMITTED_REASONS: set[OmittedReason] = set(OMITTED_REASON_VALUES)
_TRANSIENT_KINDS: set[TransientKind] = set(TRANSIENT_KIND_VALUES)
_NODE_ROLE_TAGS = frozenset(PUBLIC_NODE_ROLE_VALUES)
_NODE_TEXT_ATTRS = ("label", "within", "value")
_TEXT_ITEM_ATTRS = ("origin", "windowRef", "ambiguity", "within", "value")
_NODE_SCALAR_ATTRS = (
    "ref",
    "role",
    "origin",
    "windowRef",
    "ambiguity",
)
_NODE_SEQUENCE_ATTRS = (
    "actions",
    "state",
    "scrollDirections",
    "submitRefs",
)


def project_xml_payload(payload: RenderPayload) -> XmlProjection:
    projected = projection_dict(payload)
    if "envelope" in projected:
        return _project_retained_payload(projected)
    if projected.get("command") == "list-apps":
        return _project_list_apps_payload(projected)
    return _project_semantic_payload(projected)


def _project_retained_payload(result: ProjectionDict) -> XmlRetainedProjection:
    attrs = _project_retained_top_level_attrs(result)
    message = result.get("message")
    return {
        "kind": "retained",
        "attrs": attrs,
        "message": message if isinstance(message, str) and message else None,
        "details": _project_retained_details_attrs(
            _mapping(result.get("details")),
            command=attrs.get("command"),
        ),
        "artifacts": _project_non_empty_string_attrs(
            _mapping(result.get("artifacts")),
            _RETAINED_ARTIFACT_ATTRS,
        ),
    }


def _project_list_apps_payload(result: ProjectionDict) -> XmlListAppsProjection:
    return {
        "kind": "listApps",
        "attrs": _project_required_attr_rules(
            result,
            (
                ("ok", _required_ok_attr),
                ("command", _scalar_attr),
            ),
        ),
        "apps": _project_list_app_items(result.get("apps")),
    }


def _project_semantic_payload(result: ProjectionDict) -> XmlSemanticProjection:
    screen_value = result.get("screen")
    screen = (
        _project_screen(screen_value) if isinstance(screen_value, Mapping) else None
    )
    message = result.get("message")
    return {
        "kind": "semantic",
        "attrs": _project_top_level_attrs(result),
        "message": message if isinstance(message, str) and message else None,
        "truth": _project_attrs(_mapping(result.get("truth")), _TRUTH_ATTRS),
        "actionTarget": _project_action_target(result.get("actionTarget")),
        "uncertainty": _project_string_items(result.get("uncertainty")),
        "warnings": _project_string_items(result.get("warnings")),
        "screen": screen,
        "artifacts": _project_non_empty_string_attrs(
            _mapping(result.get("artifacts")),
            _SEMANTIC_ARTIFACT_ATTRS,
        ),
    }


def _project_list_app_items(items_value: object) -> list[XmlScalarAttrs]:
    if not isinstance(items_value, list):
        return []
    items: list[XmlScalarAttrs] = []
    for item in items_value:
        if not isinstance(item, Mapping):
            continue
        items.append(_project_attrs(item, _LIST_APP_ATTRS))
    return items


def _project_action_target(value: object) -> XmlActionTargetProjection | None:
    if not isinstance(value, Mapping):
        return None
    attrs = _project_attrs(value, _ACTION_TARGET_ATTRS)
    return {"attrs": attrs} if attrs else None


def _project_screen(screen: Mapping[str, ProjectionValue]) -> XmlScreenProjection:
    screen_id = screen.get("screenId")
    attrs = {"screenId": str(screen_id)}
    surface = _mapping(screen.get("surface"))
    surface_attrs = _project_surface_attrs(surface)
    return {
        "attrs": attrs,
        "app": _project_non_empty_string_attrs(_mapping(screen.get("app")), _APP_ATTRS),
        "surface": {
            "attrs": surface_attrs,
            "focus": _project_non_empty_string_attrs(
                _mapping(surface.get("focus")),
                _FOCUS_ATTRS,
            ),
        },
        "groups": _project_groups(
            screen.get("groups"),
            blocking_group=_projected_blocking_group(surface_attrs),
        ),
        "omitted": _project_omitted_entries(screen.get("omitted")),
        "visibleWindows": _project_visible_windows(screen.get("visibleWindows")),
        "transient": _project_transient_items(screen.get("transient")),
    }


def _project_surface_attrs(surface: Mapping[str, ProjectionValue]) -> XmlScalarAttrs:
    return _project_attr_rules(surface, _SURFACE_ATTR_RULES)


def _project_groups(
    groups_value: object,
    *,
    blocking_group: BlockingGroupName | None,
) -> list[XmlGroupProjection]:
    entries_by_name: dict[str, list[XmlGroupItemProjection]] = {
        group_name: [] for group_name in _GROUP_ORDER
    }

    if isinstance(groups_value, list):
        for item in groups_value:
            if not isinstance(item, Mapping):
                continue
            group_name = item.get("name")
            nodes = item.get("nodes")
            if not isinstance(group_name, str) or group_name not in entries_by_name:
                continue
            entries_by_name[group_name] = _project_group_items(nodes)
    group_order = list(_GROUP_ORDER)
    if blocking_group in _BLOCKING_GROUPS:
        group_order.remove(blocking_group)
        group_order.insert(0, blocking_group)
    return [
        {
            "name": group_name,
            "items": entries_by_name[group_name],
        }
        for group_name in group_order
    ]


def _project_group_items(items_value: object) -> list[XmlGroupItemProjection]:
    if not isinstance(items_value, list):
        return []

    items: list[XmlGroupItemProjection] = []
    for item in items_value:
        projected = _project_group_item(item)
        if projected is not None:
            items.append(projected)
    return items


def _project_group_item(item: object) -> XmlGroupItemProjection | None:
    if not isinstance(item, Mapping):
        return None

    kind = _group_item_tag(item)
    if kind == "text":
        text_values = _project_text_values(item.get("text"))
        if not text_values:
            return None
        return {
            "tag": "literal",
            "attrs": _project_attrs(item, _TEXT_ITEM_ATTRS),
            "children": [],
            "text": text_values[0],
        }

    tag = _node_role_tag(item.get("role"))
    if tag is None:
        return None
    attrs = _project_node_attrs(item)
    attrs.pop("role", None)
    attrs.update(_project_attrs(item, _NODE_TEXT_ATTRS))
    children = _project_group_items(item.get("children"))
    if not attrs and not children:
        return None
    return {
        "tag": tag,
        "attrs": attrs,
        "children": children,
    }


def _project_visible_windows(windows_value: object) -> list[XmlScalarAttrs]:
    if not isinstance(windows_value, list):
        return []

    windows: list[XmlScalarAttrs] = []
    for item in windows_value:
        if not isinstance(item, Mapping):
            continue
        attrs = _project_attrs(item, _VISIBLE_WINDOW_ATTRS)
        if attrs:
            windows.append(attrs)
    return windows


def _project_omitted_entries(items_value: object) -> list[XmlOmittedEntryProjection]:
    if not isinstance(items_value, list):
        return []

    items: list[XmlOmittedEntryProjection] = []
    for item in items_value:
        projected = _project_omitted_entry(item)
        if projected is not None:
            items.append(projected)
    return items


def _project_omitted_entry(item: object) -> XmlOmittedEntryProjection | None:
    if not isinstance(item, Mapping):
        return None
    group = _group_name(item.get("group"))
    reason = _omitted_reason(item.get("reason"))
    if group is None or reason is None:
        return None

    entry: XmlOmittedEntryProjection = {
        "group": group,
        "reason": reason,
    }
    count = _count_attr(item.get("count"))
    if count is not None:
        entry["count"] = count
    return entry


def _project_transient_items(
    items_value: object,
) -> list[XmlTransientItemProjection]:
    if not isinstance(items_value, list):
        return []

    items: list[XmlTransientItemProjection] = []
    for item in items_value:
        projected = _project_transient_item(item)
        if projected is not None:
            items.append(projected)
    return items


def _project_transient_item(item: object) -> XmlTransientItemProjection | None:
    if not isinstance(item, Mapping):
        return None

    text = item.get("text")
    if not isinstance(text, str) or not text:
        return None

    projected: XmlTransientItemProjection = {"text": text}
    kind = _transient_kind(item.get("kind"))
    if kind is not None:
        projected["kind"] = kind
    return projected


def _project_string_items(items_value: object) -> list[str]:
    if not isinstance(items_value, list):
        return []
    return [str(item) for item in items_value if item is not None]


def _project_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _project_attrs(
    value: Mapping[str, object],
    keys: Sequence[str],
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key in keys:
        if key not in value:
            continue
        projected = _scalar_attr(value.get(key))
        if projected is not None:
            attrs[key] = projected
    return attrs


def _project_node_attrs(value: Mapping[str, object]) -> XmlScalarAttrs:
    attrs = _project_attrs(value, _NODE_SCALAR_ATTRS)
    attrs.update(_project_non_empty_sequence_attrs(value, _NODE_SEQUENCE_ATTRS))
    return attrs


def _project_non_empty_sequence_attrs(
    value: Mapping[str, object],
    keys: Sequence[str],
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key in keys:
        if key not in value:
            continue
        projected = _non_empty_sequence_attr(value.get(key))
        if projected is not None:
            attrs[key] = projected
    return attrs


def _project_top_level_attrs(result: Mapping[str, object]) -> XmlScalarAttrs:
    attrs = _project_required_attr_rules(result, _TOP_LEVEL_REQUIRED_ATTR_RULES)
    attrs.update(_project_attr_rules(result, _TOP_LEVEL_OPTIONAL_ATTR_RULES))
    return attrs


def _project_retained_top_level_attrs(result: Mapping[str, object]) -> XmlScalarAttrs:
    attrs = _project_required_attr_rules(
        result,
        _RETAINED_TOP_LEVEL_REQUIRED_ATTR_RULES,
    )
    attrs.update(_project_attr_rules(result, _RETAINED_TOP_LEVEL_OPTIONAL_ATTR_RULES))
    return attrs


def _project_non_empty_string_attrs(
    value: Mapping[str, object],
    keys: Sequence[str],
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key in keys:
        projected = _non_empty_string_attr(value.get(key))
        if projected is not None:
            attrs[key] = projected
    return attrs


def _project_retained_details_attrs(
    value: Mapping[str, object],
    *,
    command: str | None,
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key in _RETAINED_DETAILS_ATTRS:
        projected = _retained_detail_attr(key, value.get(key), command=command)
        if projected is not None:
            attrs[key] = projected
    return attrs


def _project_attr_rules(
    value: Mapping[str, object],
    rules: Sequence[XmlAttrRule],
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key, projector in rules:
        if key not in value:
            continue
        projected = projector(value.get(key))
        if projected is not None:
            attrs[key] = projected
    return attrs


def _project_required_attr_rules(
    value: Mapping[str, object],
    rules: Sequence[XmlAttrRule],
) -> XmlScalarAttrs:
    attrs: XmlScalarAttrs = {}
    for key, projector in rules:
        projected = projector(value[key])
        if projected is not None:
            attrs[key] = projected
    return attrs


def _scalar_attr(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return _bool_string(value)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return " ".join(str(item) for item in value)
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _non_empty_string_attr(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _non_empty_sequence_attr(value: object) -> str | None:
    if isinstance(value, Sequence) and not isinstance(value, str) and value:
        return " ".join(str(item) for item in value)
    return None


def _retained_detail_attr(
    key: str,
    value: object,
    *,
    command: str | None,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized != value:
        return None
    if key == "sourceCode":
        return normalized if _safe_retained_source_code(normalized) else None
    if key in {"sourceKind", "operation", "reason"}:
        return (
            normalized
            if _safe_retained_detail_slug(normalized, key=key, command=command)
            else None
        )
    if key in {"expectedReleaseVersion", "actualReleaseVersion"}:
        return normalized if _safe_retained_release_version(normalized) else None
    return None


def _safe_retained_source_code(value: str) -> bool:
    if len(value) > 64:
        return False
    if _DEVICE_SERIAL_DETAIL_RE.fullmatch(value):
        return False
    if _FINGERPRINT_DETAIL_RE.fullmatch(value.lower()):
        return False
    return _RETAINED_SOURCE_CODE_RE.fullmatch(value) is not None


def _safe_retained_detail_slug(
    value: str,
    *,
    key: str,
    command: str | None,
) -> bool:
    if not _RETAINED_DETAIL_SLUG_RE.fullmatch(value):
        return False
    lower = value.lower()
    if _DEVICE_SERIAL_DETAIL_RE.fullmatch(value):
        return False
    if _FINGERPRINT_DETAIL_RE.fullmatch(lower):
        return False
    if "token" in lower and lower not in _SAFE_TOKEN_DETAIL_VALUES:
        return False
    if any(
        marker in lower
        for marker in (
            "bearer",
            "://",
            "www.",
            ".androidctl",
            "artifact-root",
            "artifact_path",
            "artifact-path",
            "raw-rid",
            "raw_rid",
            "rawrid",
            "snapshot",
            "fingerprint",
        )
    ):
        return False
    return not lower.startswith(("rid-", "rid_", "snapshot-", "snapshot_"))


def _safe_retained_release_version(value: str) -> bool:
    if len(value) > 32:
        return False
    return _RETAINED_RELEASE_VERSION_RE.fullmatch(value) is not None


def _required_ok_attr(value: object) -> str | None:
    return _scalar_attr(value) or "false"


def _required_stringified_attr(value: object) -> str | None:
    return str(value)


def _bool_attr(value: object) -> str | None:
    if isinstance(value, bool):
        return _bool_string(value)
    return None


def _blocking_group_attr(value: object) -> str | None:
    if isinstance(value, str) and value in _BLOCKING_GROUPS:
        return value
    return None


def _group_name(value: object) -> XmlGroupName | None:
    if isinstance(value, str) and value in _GROUP_ORDER:
        return cast(XmlGroupName, value)
    return None


def _omitted_reason(value: object) -> OmittedReason | None:
    if isinstance(value, str) and value in _OMITTED_REASONS:
        return cast(OmittedReason, value)
    return None


def _transient_kind(value: object) -> TransientKind | None:
    if isinstance(value, str) and value in _TRANSIENT_KINDS:
        return cast(TransientKind, value)
    return None


def _count_attr(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    return None


_TOP_LEVEL_REQUIRED_ATTR_RULES: tuple[XmlAttrRule, ...] = (
    ("ok", _required_ok_attr),
    ("command", _required_stringified_attr),
    ("category", _required_stringified_attr),
    ("payloadMode", _required_stringified_attr),
)
_TOP_LEVEL_OPTIONAL_ATTR_RULES: tuple[XmlAttrRule, ...] = (
    ("sourceScreenId", _non_empty_string_attr),
    ("nextScreenId", _non_empty_string_attr),
    ("code", _non_empty_string_attr),
)
_RETAINED_TOP_LEVEL_REQUIRED_ATTR_RULES: tuple[XmlAttrRule, ...] = (
    ("ok", _required_ok_attr),
    ("command", _required_stringified_attr),
    ("envelope", _required_stringified_attr),
)
_RETAINED_TOP_LEVEL_OPTIONAL_ATTR_RULES: tuple[XmlAttrRule, ...] = (
    ("code", _non_empty_string_attr),
)
_SURFACE_ATTR_RULES: tuple[XmlAttrRule, ...] = (
    ("keyboardVisible", _bool_attr),
    ("blockingGroup", _blocking_group_attr),
)


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def _projected_blocking_group(
    surface_attrs: XmlScalarAttrs,
) -> BlockingGroupName | None:
    blocking_group = surface_attrs.get("blockingGroup")
    if blocking_group == "dialog":
        return "dialog"
    if blocking_group == "keyboard":
        return "keyboard"
    if blocking_group == "system":
        return "system"
    return None


def _mapping(value: object) -> Mapping[str, ProjectionValue]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _group_item_tag(item: Mapping[str, object]) -> XmlGroupItemTag:
    kind = item.get("kind")
    if kind == "container":
        return "container"
    if kind == "text":
        return "text"
    return "node"


def _node_role_tag(role: object) -> str | None:
    if isinstance(role, str) and role in _NODE_ROLE_TAGS:
        return role
    return None
