from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

from androidctl.errors.models import ErrorTier, PublicError
from androidctl.renderers import RenderPayload
from androidctl.renderers.xml_projection import (
    XmlActionTargetProjection,
    XmlGroupItemProjection,
    XmlGroupProjection,
    XmlOmittedEntryProjection,
    XmlScalarAttrs,
    XmlScreenProjection,
    XmlSurfaceProjection,
    XmlTransientItemProjection,
    project_xml_payload,
)

_CLOSE_ERROR_FALLBACK_MESSAGE = "close failed"


def render_success_text(
    *,
    payload: dict[str, object],
) -> str:
    return render_xml(payload)


def render_error_text(
    error: PublicError,
    *,
    command: str | None = None,
    tier: ErrorTier,
    execution_outcome: str | None = None,
) -> str:
    del execution_outcome
    return render_error_xml(
        _normalize_error_for_command(error, command=command),
        command=command,
        tier=tier,
    )


def render_xml(payload: RenderPayload) -> str:
    projection = project_xml_payload(payload)
    if projection["kind"] == "listApps":
        root = Element("listAppsResult", projection["attrs"])
        _append_apps(root, projection["apps"])
        return tostring(root, encoding="unicode", short_empty_elements=True)

    if projection["kind"] == "retained":
        root = Element("retainedResult", projection["attrs"])
        message = projection["message"]
        if message is not None:
            SubElement(root, "message").text = message
        _append_details(root, projection["details"])
        _append_artifacts(root, projection["artifacts"])
        return tostring(root, encoding="unicode", short_empty_elements=True)

    root = Element(
        "result",
        projection["attrs"],
    )

    message = projection["message"]
    if message is not None:
        SubElement(root, "message").text = message

    _append_truth(root, projection["truth"])
    action_target = projection["actionTarget"]
    if action_target is not None:
        _append_action_target(root, action_target)
    _append_items(root, "uncertainty", projection["uncertainty"])
    _append_items(root, "warnings", projection["warnings"])

    screen = projection["screen"]
    if screen is not None:
        _append_screen(root, screen)

    _append_artifacts(root, projection["artifacts"])
    return tostring(root, encoding="unicode", short_empty_elements=True)


def render_error_xml(
    error: PublicError,
    *,
    command: str | None = None,
    tier: ErrorTier,
) -> str:
    public_command = command or "observe"
    root = Element(
        "errorResult",
        {
            "ok": "false",
            "code": error.code,
            "exitCode": str(int(error.exit_code)),
            "tier": tier,
            "command": public_command,
        },
    )
    if _has_non_whitespace_text(error.message):
        SubElement(root, "message").text = error.message
    if _has_non_whitespace_text(error.hint):
        SubElement(root, "hint").text = error.hint
    return tostring(root, encoding="unicode", short_empty_elements=True)


def _has_non_whitespace_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _with_close_error_message(error: PublicError) -> PublicError:
    normalized = error.message.strip()
    if normalized:
        return error
    return PublicError(
        code=error.code,
        message=_CLOSE_ERROR_FALLBACK_MESSAGE,
        hint=error.hint,
        exit_code=error.exit_code,
    )


def _normalize_error_for_command(
    error: PublicError,
    *,
    command: str | None,
) -> PublicError:
    if command == "close":
        return _with_close_error_message(error)
    return error


def _append_truth(parent: Element, truth_attrs: XmlScalarAttrs) -> None:
    SubElement(parent, "truth", truth_attrs)


def _append_action_target(
    parent: Element,
    action_target: XmlActionTargetProjection,
) -> None:
    SubElement(parent, "actionTarget", action_target["attrs"])


def _append_items(parent: Element, tag: str, items: list[str]) -> None:
    container = SubElement(parent, tag)
    for item in items:
        SubElement(container, "item").text = item


def _append_artifacts(parent: Element, artifacts_attrs: XmlScalarAttrs) -> None:
    SubElement(parent, "artifacts", artifacts_attrs)


def _append_details(parent: Element, details_attrs: XmlScalarAttrs) -> None:
    if details_attrs:
        SubElement(parent, "details", details_attrs)


def _append_apps(parent: Element, apps: list[XmlScalarAttrs]) -> None:
    apps_elem = SubElement(parent, "apps")
    for item in apps:
        SubElement(apps_elem, "app", item)


def _append_screen(parent: Element, screen: XmlScreenProjection) -> None:
    screen_elem = SubElement(parent, "screen", screen["attrs"])
    _append_app(screen_elem, screen["app"])
    _append_surface(screen_elem, screen["surface"])
    _append_groups(screen_elem, screen["groups"])
    _append_omitted(screen_elem, screen["omitted"])
    _append_visible_windows(screen_elem, screen["visibleWindows"])
    _append_transient(screen_elem, screen["transient"])


def _append_app(parent: Element, app_attrs: XmlScalarAttrs) -> None:
    SubElement(parent, "app", app_attrs)


def _append_surface(parent: Element, surface: XmlSurfaceProjection) -> None:
    surface_elem = SubElement(parent, "surface", surface["attrs"])
    SubElement(surface_elem, "focus", surface["focus"])


def _append_groups(parent: Element, groups: list[XmlGroupProjection]) -> None:
    groups_elem = SubElement(parent, "groups")
    for group in groups:
        group_elem = SubElement(groups_elem, group["name"])
        for item in group["items"]:
            _append_group_item(group_elem, item)


def _append_group_item(parent: Element, item: XmlGroupItemProjection) -> None:
    if "text" in item:
        text_elem = SubElement(parent, item["tag"], item["attrs"])
        text_elem.text = item["text"]
        return

    node_elem = SubElement(parent, item["tag"], item["attrs"])
    for child in item["children"]:
        _append_group_item(node_elem, child)


def _append_omitted(
    parent: Element,
    omitted: list[XmlOmittedEntryProjection],
) -> None:
    omitted_elem = SubElement(parent, "omitted")
    for item in omitted:
        attrs: XmlScalarAttrs = {
            "group": item["group"],
            "reason": item["reason"],
        }
        if "count" in item:
            attrs["count"] = item["count"]
        SubElement(omitted_elem, "entry", attrs)


def _append_visible_windows(parent: Element, windows: list[XmlScalarAttrs]) -> None:
    windows_elem = SubElement(parent, "visibleWindows")
    for item in windows:
        SubElement(windows_elem, "window", item)


def _append_transient(
    parent: Element,
    transient_items: list[XmlTransientItemProjection],
) -> None:
    transient_elem = SubElement(parent, "transient")
    for item in transient_items:
        attrs: XmlScalarAttrs = {}
        if "kind" in item:
            attrs["kind"] = item["kind"]
        SubElement(transient_elem, "item", attrs).text = item["text"]
