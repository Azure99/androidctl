"""Boundary validation entrypoints for device RPC payloads."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import TypeVar

from pydantic import ValidationError

from androidctld.device.adapters import (
    adapt_action_perform_result,
    adapt_events_poll_result,
    adapt_meta_payload,
    adapt_rpc_error_payload,
    adapt_screenshot_capture_result,
)
from androidctld.device.errors import DeviceBootstrapError, device_rpc_failed
from androidctld.device.schema import (
    ActionPerformResultPayload,
    EventsPollResultPayload,
    MetaPayload,
    RpcErrorPayload,
    ScreenshotCaptureResultPayload,
)
from androidctld.device.types import (
    ActionPerformResult,
    EventsPollResult,
    MetaInfo,
    ScreenshotCaptureResult,
)
from androidctld.runtime_policy import (
    SCREENSHOT_MAX_BASE64_CHARS,
    SCREENSHOT_MAX_BINARY_BYTES,
    SCREENSHOT_MAX_OUTPUT_PIXELS,
)
from androidctld.schema import ApiModel
from androidctld.schema.core import SchemaDecodeError
from androidctld.schema.validation_errors import (
    validation_error_to_device_bootstrap_error,
    validation_error_to_schema_decode_error,
)

ModelT = TypeVar("ModelT", bound=ApiModel)
ResultT = TypeVar("ResultT")


def parse_meta_payload(payload: object) -> MetaInfo:
    return _adapt_payload(
        lambda item: adapt_meta_payload(item, field_name="result"),
        _validate_payload(MetaPayload, payload, field_name="result"),
    )


def parse_rpc_error_payload(payload: object) -> DeviceBootstrapError:
    return adapt_rpc_error_payload(
        _validate_payload(RpcErrorPayload, payload, field_name="error")
    )


def parse_action_perform_result(payload: object) -> ActionPerformResult:
    return _adapt_payload(
        lambda item: adapt_action_perform_result(item, field_name="result"),
        _validate_action_perform_result_payload(payload, field_name="result"),
    )


def parse_events_poll_result(payload: object) -> EventsPollResult:
    return _adapt_payload(
        lambda item: adapt_events_poll_result(item, field_name="result"),
        _validate_payload(EventsPollResultPayload, payload, field_name="result"),
    )


def parse_screenshot_capture_result(payload: object) -> ScreenshotCaptureResult:
    screenshot_payload = _validate_payload(
        ScreenshotCaptureResultPayload,
        payload,
        field_name="result",
    )
    _validate_screenshot_capture_payload(screenshot_payload, field_name="result")
    validate_screenshot_body_base64_budget(
        screenshot_payload.body_base64,
        field_name="result.bodyBase64",
    )
    return _adapt_payload(
        lambda item: adapt_screenshot_capture_result(item, field_name="result"),
        screenshot_payload,
    )


def validate_screenshot_body_base64_budget(value: str, *, field_name: str) -> None:
    if len(value) > SCREENSHOT_MAX_BASE64_CHARS:
        raise device_rpc_failed(
            "screenshot bodyBase64 exceeds size budget",
            {
                "field": field_name,
                "reason": "screenshot_base64_too_large",
                "maxChars": SCREENSHOT_MAX_BASE64_CHARS,
            },
            retryable=False,
        )
    estimated_size = _estimated_base64_decoded_size(value)
    if estimated_size > SCREENSHOT_MAX_BINARY_BYTES:
        raise device_rpc_failed(
            "screenshot decoded body exceeds size budget",
            {
                "field": field_name,
                "reason": "screenshot_decoded_too_large",
                "maxBytes": SCREENSHOT_MAX_BINARY_BYTES,
            },
            retryable=False,
        )


def decode_screenshot_body_base64(value: str, *, field_name: str) -> bytes:
    validate_screenshot_body_base64_budget(value, field_name=field_name)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise device_rpc_failed(
            "screenshot bodyBase64 must be valid base64",
            {
                "field": field_name,
                "reason": "invalid_base64",
            },
            retryable=False,
        ) from error
    if len(decoded) > SCREENSHOT_MAX_BINARY_BYTES:
        raise device_rpc_failed(
            "screenshot decoded body exceeds size budget",
            {
                "field": field_name,
                "reason": "screenshot_decoded_too_large",
                "maxBytes": SCREENSHOT_MAX_BINARY_BYTES,
            },
            retryable=False,
        )
    return decoded


def validate_screenshot_png_bytes(
    value: bytes,
    *,
    field_name: str,
    expected_width_px: int,
    expected_height_px: int,
) -> None:
    png_signature = b"\x89PNG\r\n\x1a\n"
    minimum_ihdr_bytes = len(png_signature) + 4 + 4 + 13 + 4
    if len(value) < minimum_ihdr_bytes or not value.startswith(png_signature):
        _raise_invalid_png(field_name)
    ihdr_length = int.from_bytes(value[8:12], byteorder="big")
    ihdr_type = value[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        _raise_invalid_png(field_name)
    width_px = int.from_bytes(value[16:20], byteorder="big")
    height_px = int.from_bytes(value[20:24], byteorder="big")
    if width_px <= 0 or height_px <= 0:
        _raise_invalid_png(field_name)
    if width_px * height_px > SCREENSHOT_MAX_OUTPUT_PIXELS:
        _raise_screenshot_dimensions_too_large(field_name)
    if width_px != expected_width_px or height_px != expected_height_px:
        raise device_rpc_failed(
            "screenshot PNG IHDR dimensions must match typed metadata",
            {
                "field": field_name,
                "reason": "screenshot_dimensions_mismatch",
                "expectedWidthPx": expected_width_px,
                "expectedHeightPx": expected_height_px,
                "actualWidthPx": width_px,
                "actualHeightPx": height_px,
            },
            retryable=False,
        )


def _raise_invalid_png(field_name: str) -> None:
    raise device_rpc_failed(
        "screenshot decoded body must be a PNG with IHDR",
        {
            "field": field_name,
            "reason": "invalid_png",
        },
        retryable=False,
    )


def _raise_screenshot_dimensions_too_large(field_name: str) -> None:
    raise device_rpc_failed(
        "screenshot dimensions exceed pixel budget",
        {
            "field": field_name,
            "reason": "screenshot_dimensions_too_large",
            "maxPixels": SCREENSHOT_MAX_OUTPUT_PIXELS,
        },
        retryable=False,
    )


def _validate_payload(
    model_type: type[ModelT],
    payload: object,
    *,
    field_name: str,
) -> ModelT:
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise validation_error_to_device_bootstrap_error(
            error,
            field_name=field_name,
            retryable=False,
        ) from error


def _adapt_payload(
    adapter: Callable[[ModelT], ResultT],
    payload: ModelT,
) -> ResultT:
    try:
        return adapter(payload)
    except SchemaDecodeError as error:
        raise invalid_device_payload(error.field, error.problem) from error


def _validate_action_perform_result_payload(
    payload: object,
    *,
    field_name: str,
) -> ActionPerformResultPayload:
    try:
        return ActionPerformResultPayload.model_validate(payload)
    except ValidationError as error:
        schema_error = _translate_action_perform_result_payload_error(
            error,
            field_name=field_name,
        )
        raise invalid_device_payload(
            schema_error.field,
            schema_error.problem,
        ) from error


def _translate_action_perform_result_payload_error(
    error: ValidationError,
    *,
    field_name: str,
) -> SchemaDecodeError:
    first_error = error.errors()[0]
    error_location = tuple(first_error["loc"])
    if str(first_error["type"]) in {"literal_error", "union_tag_invalid"} and (
        error_location
        in {
            ("resolvedTarget",),
            ("resolved_target",),
            ("resolvedTarget", "kind"),
            ("resolved_target", "kind"),
        }
    ):
        return SchemaDecodeError(
            f"{field_name}.resolvedTarget.kind",
            "must be one of handle|coordinates|none",
        )
    schema_error = validation_error_to_schema_decode_error(error, field_name=field_name)
    return SchemaDecodeError(
        _normalize_action_result_field(schema_error.field),
        schema_error.problem,
    )


def _normalize_action_result_field(field: str) -> str:
    if field.endswith(".resolvedTarget.handle.handle"):
        return field.removesuffix(".handle")
    return field.replace(
        ".resolvedTarget.handle.handle.",
        ".resolvedTarget.handle.",
    )


def invalid_device_payload(field_name: str, problem: str) -> DeviceBootstrapError:
    return device_rpc_failed(
        f"device RPC {field_name} {problem}",
        {
            "field": field_name,
            "reason": "invalid_payload",
        },
        retryable=False,
    )


def _validate_screenshot_capture_payload(
    payload: ScreenshotCaptureResultPayload,
    *,
    field_name: str,
) -> None:
    if payload.content_type != "image/png":
        raise device_rpc_failed(
            "typed screenshot.capture result must be image/png",
            {
                "field": f"{field_name}.contentType",
                "reason": "unsupported_content_type",
                "contentType": payload.content_type,
                "expected": "image/png",
            },
            retryable=False,
        )
    if payload.width_px * payload.height_px > SCREENSHOT_MAX_OUTPUT_PIXELS:
        _raise_screenshot_dimensions_too_large(field_name)


def _estimated_base64_decoded_size(value: str) -> int:
    if not value:
        return 0
    padding = 0
    if len(value) % 4 == 0:
        padding = len(value) - len(value.rstrip("="))
        padding = min(padding, 2)
    return ((len(value) + 3) // 4) * 3 - padding
