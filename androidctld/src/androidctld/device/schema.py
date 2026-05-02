"""Boundary DTOs for device RPC payloads."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, ValidationInfo, field_validator

from androidctld.schema import ApiModel

TrimmedString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]


ActionStatusValue = Literal["done", "partial", "timeout"]


class DeviceCapabilitiesPayload(ApiModel):
    supports_events_poll: bool
    supports_screenshot: bool
    action_kinds: list[TrimmedString]


class MetaPayload(ApiModel):
    service: TrimmedString
    version: TrimmedString
    capabilities: DeviceCapabilitiesPayload


class RpcErrorPayload(ApiModel):
    code: TrimmedString
    message: TrimmedString
    retryable: bool
    details: dict[str, Any]


class NodeHandlePayload(ApiModel):
    snapshot_id: NonNegativeInt
    rid: TrimmedString


class ResolvedHandleTargetPayload(ApiModel):
    kind: Literal["handle"]
    handle: NodeHandlePayload


class ResolvedCoordinatesTargetPayload(ApiModel):
    kind: Literal["coordinates"]
    x: float
    y: float


class ResolvedNoneTargetPayload(ApiModel):
    kind: Literal["none"]


ActionResolvedTargetPayload = Annotated[
    ResolvedHandleTargetPayload
    | ResolvedCoordinatesTargetPayload
    | ResolvedNoneTargetPayload,
    Field(discriminator="kind"),
]


class ObservedAppPayload(ApiModel):
    package_name: str | None = None
    activity_name: str | None = None

    @field_validator("package_name", "activity_name", mode="before")
    @classmethod
    def _normalize_blank_strings(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if isinstance(info.context, dict) and not info.context.get(
            "normalize_blank_observed_strings", True
        ):
            return value
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ActionPerformResultPayload(ApiModel):
    action_id: TrimmedString
    status: ActionStatusValue
    duration_ms: NonNegativeInt | None = None
    resolved_target: ActionResolvedTargetPayload | None = None
    observed: ObservedAppPayload | None = None


class DeviceEventPayload(ApiModel):
    seq: NonNegativeInt
    type: TrimmedString
    timestamp: TrimmedString
    data: dict[str, Any]


class EventsPollResultPayload(ApiModel):
    events: list[DeviceEventPayload]
    latest_seq: NonNegativeInt
    need_resync: bool
    timed_out: bool


class ScreenshotCaptureResultPayload(ApiModel):
    content_type: TrimmedString
    width_px: PositiveInt
    height_px: PositiveInt
    body_base64: TrimmedString
