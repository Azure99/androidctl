"""Boundary DTOs for raw snapshot payloads."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from androidctld.schema import ApiModel

TrimmedString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]


class RawDisplayPayload(ApiModel):
    width_px: PositiveInt
    height_px: PositiveInt
    density_dpi: PositiveInt
    rotation: NonNegativeInt


class RawImePayload(ApiModel):
    visible: bool
    window_id: str | None


class RawWindowPayload(ApiModel):
    window_id: TrimmedString
    type: TrimmedString
    layer: int
    package_name: TrimmedString | None
    bounds: list[int]
    root_rid: TrimmedString


class RawNodePayload(ApiModel):
    rid: TrimmedString
    window_id: TrimmedString
    parent_rid: str | None
    child_rids: list[str]
    class_name: TrimmedString
    resource_id: str | None
    text: str | None
    content_desc: str | None
    hint_text: str | None
    state_description: str | None
    pane_title: str | None
    package_name: TrimmedString | None
    bounds: list[int]
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
    actions: list[str]


class RawSnapshotPayload(ApiModel):
    snapshot_id: NonNegativeInt
    captured_at: TrimmedString
    package_name: TrimmedString | None
    activity_name: str | None
    ime: RawImePayload
    windows: list[RawWindowPayload]
    nodes: list[RawNodePayload]
    display: RawDisplayPayload
