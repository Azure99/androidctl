"""Render the worker's task projection; selection and identity stay on the device."""

from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any

from .protocol import Fault

ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);")


def query_forms(value: str) -> set[str]:
    def decode(match: re.Match[str]) -> str:
        try:
            return ET.fromstring('<v a="' + match[0] + '"/>').attrib["a"]
        except ET.ParseError:
            return match[0]

    return {value, ENTITY.sub(decode, value)}


def clean(value: Any) -> str:
    return "".join(
        (
            char
            if ord(char) in (9, 10, 13)
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
            else "\ufffd"
        )
        for char in str(value)
    )


def without_defaults(facts: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in facts.items()
        if key not in defaults
        or type(value) is not type(defaults[key])
        or value != defaults[key]
    }


def attributes(node: dict[str, Any], *, all_nodes: bool, boxes: bool) -> dict[str, str]:
    view = node["view"]
    named = [
        action
        for action in ("expand", "collapse", "dismiss")
        if action in (node.get("actions") or [])
    ]
    target = any(
        node.get(key)
        for key in ("clickable", "longClickable", "editable", "checkable", "scrollable")
    ) or bool(named)
    attrs: dict[str, str] = {}
    if target or all_nodes:
        if node["refAvailable"]:
            attrs["ref"] = node["ref"]
        else:
            attrs["ref-unavailable"] = node["refReason"]
    if view["name"] and (all_nodes or not view["nameConsumed"]):
        attrs["name"] = view["name"]
    if node.get("editable"):
        attrs["value"] = "" if node.get("showingHintText") else node.get("text") or ""
        if isinstance(node.get("actions"), list):
            attrs["actions"] = " ".join(node["actions"])
    else:
        if node.get("longClickable") and not node.get("clickable"):
            named.insert(0, "long-click")
        if named:
            attrs["actions"] = " ".join(named)
    details = dict.fromkeys(
        value
        for value in (
            view.get("detail"),
            node.get("stateDescription") if not node.get("checkable") else None,
        )
        if value and value != view["name"]
    )
    if details:
        attrs["detail"] = " | ".join(details)
    if node.get("resourceId") and (all_nodes or target and not view["name"]):
        attrs["id"] = node["resourceId"].split(":id/", 1)[-1]
    for key in ("focused", "selected", "password"):
        if node.get(key):
            attrs[key] = "true"
    if target and node.get("enabled") is False:
        attrs["disabled"] = "true"
    if node.get("visible") is False:
        attrs["offscreen"] = "true"
    unreadable = (
        not view["nameComplete"]
        or not view["detailComplete"]
        or not node["row"]["complete"]
        or bool(node.get("unavailable"))
    )
    if node.get("checkable"):
        if node.get("checkedModel") and node["checkedModel"] != "triState":
            attrs["checkedModel"] = node["checkedModel"]
        if node.get("stateDescription") is not None:
            attrs["stateDescription"] = node["stateDescription"]
        checked = node.get("checked")
        if checked not in ("false", "true", "mixed"):
            unreadable = True
        else:
            attrs["checked"] = checked
    directions = [
        action.removeprefix("scroll-")
        for action in (
            "scroll-forward",
            "scroll-backward",
            "scroll-up",
            "scroll-down",
            "scroll-left",
            "scroll-right",
            "page-up",
            "page-down",
            "page-left",
            "page-right",
        )
        if action in (node.get("actions") or [])
    ]
    if directions:
        attrs["scroll"] = " ".join(directions)
    if node.get("covered"):
        attrs["covered"] = node["covered"]
    if node.get("error"):
        attrs["error"] = node["error"]
    if node.get("contentInvalid") is True:
        attrs["contentInvalid"] = "true"
    if range_info := node.get("rangeInfo"):
        for key in ("type", "current", "min", "max"):
            if range_info.get(key) is not None:
                attrs["range" + key.title()] = str(range_info[key])
    collection = node.get("collectionInfo") or {}
    if any(collection.get(key) is not None for key in ("rows", "columns")):
        collection = without_defaults(collection, {"selectionMode": 0})
    item = node.get("collectionItemInfo") or {}
    if any(item.get(key) is not None for key in ("rowIndex", "columnIndex")):
        item = without_defaults(item, {"rowSpan": 1, "columnSpan": 1})
    for info, keys in (
        (collection, ("rows", "columns", "selectionMode")),
        (item, ("rowIndex", "rowSpan", "columnIndex", "columnSpan")),
    ):
        for key in keys:
            if info.get(key) is not None:
                attrs[key] = str(info[key])
    for field, key in (
        ("collectionInfo", "hierarchical"),
        ("collectionItemInfo", "heading"),
    ):
        if (node.get(field) or {}).get(key):
            attrs[key] = "true"
    if view.get("webEmpty"):
        attrs["empty"] = "true"
    if boxes:
        attrs["box"] = json.dumps(node.get("bounds"), separators=(",", ":"))
    if unreadable or any(clean(value) != value for value in attrs.values()):
        attrs["unreadable"] = "true"
    return {key: clean(value) for key, value in attrs.items()}


def project(
    tree: dict[str, Any],
    *,
    all_nodes: bool = False,
    boxes: bool = False,
    ref: str | None = None,
) -> list[ET.Element]:
    children: dict[str | None, list[dict[str, Any]]] = {}
    for node in tree["nodes"]:
        children.setdefault(node.get("parent"), []).append(node)

    roots = (
        [node for node in tree["nodes"] if node["ref"] == ref]
        if ref
        else children.get(None, [])
    )
    if ref and not roots:
        raise Fault("staleRef", "Ref is not in the current snapshot; run snapshot")
    elements: list[ET.Element] = []
    pending: list[tuple[dict[str, Any], list[ET.Element], list[ET.Element] | None]] = [
        (node, elements, None) for node in reversed(roots)
    ]
    while pending:
        node, destination, nested = pending.pop()
        # None schedules children; a list means all children have been projected.
        if nested is None:
            nested = []
            pending.append((node, destination, nested))
            pending.extend(
                (child, nested, None)
                for child in reversed(children.get(node["ref"], []))
            )
            continue
        if not all_nodes and node.get("visible") is False:
            destination.extend(nested)
            continue
        attrs = attributes(node, all_nodes=all_nodes, boxes=boxes)
        role = node["view"]["role"]
        if not attrs and not (nested and role in ("list", "scroll", "item")):
            destination.extend(nested)
            continue
        element = ET.Element(role, attrs)
        element.extend(nested)
        destination.append(element)
    return elements


def folded(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", value).casefold().replace("-", " ").split()
    )


def find(elements: list[ET.Element], query: str) -> list[ET.Element]:
    needles = {folded(value) for value in query_forms(query)}
    found: list[ET.Element] = []

    pending = list(reversed(elements))
    while pending:
        element = pending.pop()
        values = (
            folded(value)
            for key, value in element.attrib.items()
            if key in ("name", "value", "detail", "stateDescription", "id")
        )
        if any(needle in value for value in values for needle in needles):
            found.append(element)
        else:
            pending.extend(reversed(element))
    return found


def window(tree: dict[str, Any]) -> dict[str, Any]:
    return next(
        (value for value in tree["windows"] if value["id"] == tree["scope"]["window"]),
        {},
    )


def app_title(tree: dict[str, Any], selected: dict[str, Any]) -> tuple[str, str]:
    return tree["scope"].get("package") or "", selected.get("title") or ""


def screen(tree: dict[str, Any], elements: list[ET.Element]) -> ET.Element:
    selected = window(tree)
    app, title = app_title(tree, selected)
    attrs = {
        "app": app,
        "title": title,
        "keyboard": tree["keyboard"],
    }
    cleaned = {key: clean(value) for key, value in attrs.items()}
    unreadable = tree.get("partial") or cleaned != attrs
    if selected.get("titleTruncated") or cleaned["title"] != attrs["title"]:
        cleaned["titleIncomplete"] = "true"
        unreadable = True
    if unreadable:
        cleaned["unreadable"] = "true"
    root = ET.Element("screen", cleaned)
    for value in tree["windows"]:
        facts = {
            key: clean(value[key])
            for key in ("id", "type", "layer", "title", "packageName")
            if value.get(key) is not None
        }
        if value.get("titleTruncated") or facts.get("title", "") != (
            value.get("title") or ""
        ):
            facts["titleIncomplete"] = "true"
        if value["id"] == tree["scope"]["window"] and facts.get("title"):
            del facts["title"]
        for key in ("active", "focused", "rootAvailable"):
            if key in value and (key == "rootAvailable" or value[key] is not False):
                facts[key] = str(value[key]).lower()
        if value.get("bounds") is not None:
            facts["box"] = json.dumps(value["bounds"], separators=(",", ":"))
        ET.SubElement(root, "window", facts)
    root.extend(elements)
    return root


def image_metadata(image: dict[str, Any]) -> dict[str, Any]:
    shape = image.get("shape")
    if not isinstance(shape, dict):
        return image
    shape = without_defaults(
        shape,
        {
            "displayId": 0,
            "userId": 0,
            "interactive": True,
            "keyguardShowing": False,
            "keyguardSecure": False,
            "deviceLocked": False,
        },
    )
    if "windows" in shape:
        windows = []
        for window in shape["windows"]:
            window = without_defaults(window, {"active": False, "focused": False})
            if window.get("transformation") == {
                "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "magnification": [1, 0, 0],
            }:
                del window["transformation"]
            windows.append(window)
        shape["windows"] = windows
    return {**image, "shape": shape}


def metadata(
    invocation: dict[str, Any],
    *,
    source: dict[str, Any],
    snapshot_facts: dict[str, Any],
    image: dict[str, Any] | None,
    public_refs: set[str],
    keyboard: str,
) -> dict[str, Any]:
    facts = without_defaults(
        snapshot_facts,
        {"selectionScope": {}, "partial": False, "missing": [], "nodeUnavailable": []},
    )
    facts.pop("windows", None)
    facts.pop("refContext", None)
    if facts.get("keyboard") == keyboard:
        del facts["keyboard"]
    display = facts.get("display")
    if isinstance(display, dict):
        defaults = {
            "interactive": True,
            "deepIdle": False,
            "userId": 0,
            "keyguardShowing": False,
            "keyguardSecure": False,
            "deviceLocked": False,
        }
        if "displayError" not in display:
            defaults.update(displayId=0, displayState=2)
        if facts.get("finishedMs") is not None:
            defaults["sampleMs"] = facts["finishedMs"]
        facts["display"] = without_defaults(display, defaults)

    missing = []
    summary: dict[str, Any] = {}
    reasons: dict[str, int] = {}
    for entry in snapshot_facts.get("missing", []):
        if entry.get("ref") in public_refs:
            missing.append(
                without_defaults(entry, {"unreadChildren": 0, "unavailableChildren": 0})
            )
        else:
            summary["entries"] = summary.get("entries", 0) + 1
            for key in ("unreadChildren", "unavailableChildren"):
                if entry.get(key) is not None:
                    summary[key] = summary.get(key, 0) + entry[key]
            if "reason" in entry:
                reason = entry["reason"]
                reasons[reason] = reasons.get(reason, 0) + 1
    if missing:
        facts["missing"] = missing
    else:
        facts.pop("missing", None)
    if summary:
        summary = without_defaults(
            summary, {"unreadChildren": 0, "unavailableChildren": 0}
        )
        if reasons:
            summary["reasons"] = reasons
        facts["missingSummary"] = summary

    result = {
        **invocation,
        "source": without_defaults(
            source, {"snapshotCached": False, "snapshotBeforeAction": False}
        ),
        "snapshotFacts": facts,
    }
    if isinstance(invocation.get("args"), dict):
        result["args"] = without_defaults(
            invocation["args"],
            {"timeoutMs": 5000, "settleMs": 1500, "screenshot": False, "scope": {}},
        )
    if image is not None:
        result["image"] = image_metadata(image)
    return result


MAX_INDENT_SPACES = 16


def render(root: ET.Element) -> str:
    lines: list[str] = []
    pending: list[tuple[ET.Element | str, int]] = [(root, 0)]
    while pending:
        element, depth = pending.pop()
        indent = " " * min(depth * 2, MAX_INDENT_SPACES)
        if isinstance(element, str):
            lines.append(indent + element)
            continue
        # Observation elements have attributes and children, with no text or
        # namespaces. Serialize each shallow element so ElementTree owns escaping.
        shallow = ET.Element(element.tag, element.attrib)
        serialized = ET.tostring(
            shallow, encoding="unicode", short_empty_elements=not len(element)
        )
        if len(element):
            closing = f"</{element.tag}>"
            lines.append(indent + serialized.removesuffix(closing))
            pending.append((closing, depth))
            pending.extend((child, depth + 1) for child in reversed(element))
        else:
            lines.append(indent + serialized)
    return "\n".join(lines).replace("&gt;", ">") + "\n"


def header(invocation: dict[str, Any]) -> str:
    command = json.dumps(invocation, ensure_ascii=False, separators=(",", ":"))
    command = "".join(
        (
            f"\\u{ord(char):04x}"
            if 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF}
            else char
        )
        for char in command
    ).replace(">", "\\u003e")
    return (
        ET.tostring(
            ET.ProcessingInstruction("androidctl", "command=" + command),
            encoding="unicode",
        )
        + "\n"
    )
