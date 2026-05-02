"""Validated raw snapshot domain models and parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from androidctld.device.errors import DeviceBootstrapError, device_rpc_failed
from androidctld.schema.core import SchemaDecodeError
from androidctld.schema.validation_errors import validation_error_to_schema_decode_error
from androidctld.snapshots.schema import (
    RawDisplayPayload,
    RawImePayload,
    RawNodePayload,
    RawSnapshotPayload,
    RawWindowPayload,
)


@dataclass(frozen=True)
class RawIme:
    visible: bool
    window_id: str | None


@dataclass(frozen=True)
class RawWindow:
    window_id: str
    type: str
    layer: int
    package_name: str | None
    bounds: tuple[int, int, int, int]
    root_rid: str


@dataclass(frozen=True)
class RawNode:
    rid: str
    window_id: str
    parent_rid: str | None
    child_rids: tuple[str, ...]
    class_name: str
    resource_id: str | None
    text: str | None
    content_desc: str | None
    hint_text: str | None
    state_description: str | None
    pane_title: str | None
    package_name: str | None
    bounds: tuple[int, int, int, int]
    visible_to_user: bool
    important_for_accessibility: bool
    clickable: bool
    enabled: bool
    editable: bool
    focusable: bool
    focused: bool
    checkable: bool
    checked: bool
    selected: bool
    scrollable: bool
    password: bool
    actions: tuple[str, ...]


@dataclass(frozen=True)
class RawSnapshot:
    snapshot_id: int
    captured_at: str
    package_name: str | None
    activity_name: str | None
    ime: RawIme
    windows: tuple[RawWindow, ...]
    nodes: tuple[RawNode, ...]
    display: dict[str, int]


def parse_raw_snapshot(payload: object) -> RawSnapshot:
    try:
        boundary = RawSnapshotPayload.model_validate(payload)
    except ValidationError as error:
        raise invalid_snapshot_validation_error(error, field_name="result") from error
    try:
        return adapt_raw_snapshot(boundary, field_name="result")
    except SchemaDecodeError as error:
        raise invalid_snapshot_payload(error.field, error.problem) from error


def adapt_raw_snapshot(
    payload: RawSnapshotPayload,
    *,
    field_name: str = "result",
) -> RawSnapshot:
    return RawSnapshot(
        snapshot_id=payload.snapshot_id,
        captured_at=payload.captured_at,
        package_name=payload.package_name,
        activity_name=payload.activity_name,
        ime=adapt_ime(payload.ime, field_name=f"{field_name}.ime"),
        windows=tuple(
            adapt_raw_window(window, field_name=f"{field_name}.windows[{index}]")
            for index, window in enumerate(payload.windows)
        ),
        nodes=tuple(
            adapt_raw_node(node, field_name=f"{field_name}.nodes[{index}]")
            for index, node in enumerate(payload.nodes)
        ),
        display=adapt_display(payload.display, field_name=f"{field_name}.display"),
    )


def adapt_display(
    payload: RawDisplayPayload,
    *,
    field_name: str = "result.display",
) -> dict[str, int]:
    del field_name
    return {
        "widthPx": payload.width_px,
        "heightPx": payload.height_px,
        "densityDpi": payload.density_dpi,
        "rotation": payload.rotation,
    }


def adapt_ime(
    payload: RawImePayload,
    *,
    field_name: str = "result.ime",
) -> RawIme:
    del field_name
    return RawIme(
        visible=payload.visible,
        window_id=payload.window_id,
    )


def adapt_raw_window(
    payload: RawWindowPayload,
    *,
    field_name: str,
) -> RawWindow:
    return RawWindow(
        window_id=payload.window_id,
        type=payload.type,
        layer=payload.layer,
        package_name=payload.package_name,
        bounds=adapt_bounds(payload.bounds, field_name=f"{field_name}.bounds"),
        root_rid=payload.root_rid,
    )


def adapt_raw_node(
    payload: RawNodePayload,
    *,
    field_name: str,
) -> RawNode:
    return RawNode(
        rid=payload.rid,
        window_id=payload.window_id,
        parent_rid=payload.parent_rid,
        child_rids=tuple(payload.child_rids),
        class_name=payload.class_name,
        resource_id=payload.resource_id,
        text=payload.text,
        content_desc=payload.content_desc,
        hint_text=payload.hint_text,
        state_description=payload.state_description,
        pane_title=payload.pane_title,
        package_name=payload.package_name,
        bounds=adapt_bounds(payload.bounds, field_name=f"{field_name}.bounds"),
        visible_to_user=payload.visible_to_user,
        important_for_accessibility=payload.important_for_accessibility,
        clickable=payload.clickable,
        enabled=payload.enabled,
        editable=payload.editable,
        focusable=payload.focusable,
        focused=payload.focused,
        checkable=payload.checkable,
        checked=payload.checked,
        selected=payload.selected,
        scrollable=payload.scrollable,
        password=payload.password,
        actions=tuple(payload.actions),
    )


def adapt_bounds(
    bounds: list[int],
    *,
    field_name: str,
) -> tuple[int, int, int, int]:
    if len(bounds) != 4:
        raise SchemaDecodeError(field_name, "must contain exactly 4 integers")
    return (bounds[0], bounds[1], bounds[2], bounds[3])


def invalid_snapshot_validation_error(
    error: ValidationError,
    *,
    field_name: str,
) -> DeviceBootstrapError:
    schema_error = validation_error_to_schema_decode_error(
        error,
        field_name=field_name,
    )
    return invalid_snapshot_payload(schema_error.field, schema_error.problem)


def invalid_snapshot_payload(field_name: str, problem: str) -> DeviceBootstrapError:
    return device_rpc_failed(
        f"device RPC {field_name} {problem}",
        {
            "field": field_name,
            "reason": "invalid_snapshot",
        },
        retryable=False,
    )
