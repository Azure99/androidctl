"""Adapters from device boundary DTOs into runtime types."""

from __future__ import annotations

from typing import Any

from androidctld.device.errors import (
    DeviceBootstrapError,
    device_agent_unauthorized,
    device_rpc_failed,
)
from androidctld.device.schema import (
    ActionPerformResultPayload,
    ActionResolvedTargetPayload,
    DeviceCapabilitiesPayload,
    DeviceEventPayload,
    EventsPollResultPayload,
    MetaPayload,
    NodeHandlePayload,
    ObservedAppPayload,
    ResolvedCoordinatesTargetPayload,
    ResolvedHandleTargetPayload,
    ResolvedNoneTargetPayload,
    RpcErrorPayload,
    ScreenshotCaptureResultPayload,
)
from androidctld.device.types import (
    ActionPerformResult,
    ActionStatus,
    DeviceCapabilities,
    DeviceEvent,
    EventsPollResult,
    MetaInfo,
    ObservedApp,
    ResolvedCoordinatesTarget,
    ResolvedHandleTarget,
    ResolvedNoneTarget,
    ResolvedTarget,
    ScreenshotCaptureResult,
)
from androidctld.refs.models import NodeHandle
from androidctld.schema.base import dump_api_model
from androidctld.schema.core import SchemaDecodeError


def adapt_meta_payload(
    payload: MetaPayload,
    *,
    field_name: str = "result",
) -> MetaInfo:
    return MetaInfo(
        service=payload.service,
        version=payload.version,
        capabilities=adapt_capabilities(
            payload.capabilities,
            field_name=f"{field_name}.capabilities",
        ),
    )


def adapt_capabilities(
    payload: DeviceCapabilitiesPayload,
    *,
    field_name: str = "result.capabilities",
) -> DeviceCapabilities:
    del field_name
    return DeviceCapabilities(
        supports_events_poll=payload.supports_events_poll,
        supports_screenshot=payload.supports_screenshot,
        action_kinds=list(payload.action_kinds),
    )


def adapt_rpc_error_payload(payload: RpcErrorPayload) -> DeviceBootstrapError:
    details = {
        "deviceCode": payload.code,
        "retryable": payload.retryable,
        "details": dict(payload.details),
    }
    if payload.code == "UNAUTHORIZED":
        return device_agent_unauthorized(payload.message, details)
    return device_rpc_failed(
        payload.message,
        details,
        retryable=payload.retryable,
    )


def adapt_action_perform_result(
    payload: ActionPerformResultPayload,
    *,
    field_name: str = "result",
) -> ActionPerformResult:
    resolved_target = None
    if payload.resolved_target is not None:
        resolved_target = adapt_resolved_target(
            payload.resolved_target,
            field_name=f"{field_name}.resolvedTarget",
        )
    observed = None
    if payload.observed is not None:
        observed = adapt_observed_app(
            payload.observed,
            field_name=f"{field_name}.observed",
        )
    return ActionPerformResult(
        action_id=payload.action_id,
        status=adapt_action_status(payload.status, field_name=f"{field_name}.status"),
        duration_ms=payload.duration_ms,
        resolved_target=resolved_target,
        observed=observed,
    )


def build_node_handle_payload(handle: NodeHandle) -> NodeHandlePayload:
    return NodeHandlePayload(snapshot_id=handle.snapshot_id, rid=handle.rid)


def dump_node_handle(handle: NodeHandle) -> dict[str, Any]:
    return dump_api_model(build_node_handle_payload(handle))


def adapt_action_status(
    status: str,
    *,
    field_name: str,
) -> ActionStatus:
    try:
        return ActionStatus(status)
    except ValueError as error:
        raise SchemaDecodeError(
            field_name,
            "must be one of done|partial|timeout",
        ) from error


def adapt_observed_app(
    payload: ObservedAppPayload,
    *,
    field_name: str = "result.observed",
) -> ObservedApp:
    del field_name
    return ObservedApp(
        package_name=payload.package_name,
        activity_name=payload.activity_name,
    )


def adapt_resolved_target(
    payload: ActionResolvedTargetPayload,
    *,
    field_name: str = "resolvedTarget",
) -> ResolvedTarget:
    if isinstance(payload, ResolvedHandleTargetPayload):
        return ResolvedHandleTarget(
            handle=adapt_node_handle(payload.handle, field_name=f"{field_name}.handle"),
        )
    if isinstance(payload, ResolvedCoordinatesTargetPayload):
        return ResolvedCoordinatesTarget(x=payload.x, y=payload.y)
    if isinstance(payload, ResolvedNoneTargetPayload):
        return ResolvedNoneTarget()
    raise SchemaDecodeError(
        f"{field_name}.kind", "must be one of handle|coordinates|none"
    )


def adapt_node_handle(
    payload: NodeHandlePayload,
    *,
    field_name: str = "handle",
) -> NodeHandle:
    del field_name
    return NodeHandle(
        snapshot_id=payload.snapshot_id,
        rid=payload.rid,
    )


def adapt_events_poll_result(
    payload: EventsPollResultPayload,
    *,
    field_name: str = "result",
) -> EventsPollResult:
    return EventsPollResult(
        events=tuple(
            adapt_device_event(event, field_name=f"{field_name}.events[{index}]")
            for index, event in enumerate(payload.events)
        ),
        latest_seq=payload.latest_seq,
        need_resync=payload.need_resync,
        timed_out=payload.timed_out,
    )


def adapt_device_event(
    payload: DeviceEventPayload,
    *,
    field_name: str = "result.events[0]",
) -> DeviceEvent:
    del field_name
    return DeviceEvent(
        seq=payload.seq,
        type=payload.type,
        timestamp=payload.timestamp,
        data=dict(payload.data),
    )


def adapt_screenshot_capture_result(
    payload: ScreenshotCaptureResultPayload,
    *,
    field_name: str = "result",
) -> ScreenshotCaptureResult:
    del field_name
    return ScreenshotCaptureResult(
        content_type=payload.content_type,
        width_px=payload.width_px,
        height_px=payload.height_px,
        body_base64=payload.body_base64,
    )
