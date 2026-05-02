"""Device RPC client for the Android agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from androidctld.device.action_models import DeviceActionRequest
from androidctld.device.action_serialization import dump_device_action_request
from androidctld.device.errors import (
    device_agent_unauthorized,
    device_agent_unavailable,
    device_rpc_failed,
    device_rpc_transport_reset,
)
from androidctld.device.parsing import (
    parse_action_perform_result,
    parse_events_poll_result,
    parse_meta_payload,
    parse_rpc_error_payload,
    parse_screenshot_capture_result,
)
from androidctld.device.types import (
    ActionPerformResult,
    DeviceEndpoint,
    EventsPollResult,
    MetaInfo,
    ScreenshotCaptureResult,
)
from androidctld.protocol import DeviceRpcMethod
from androidctld.runtime_policy import (
    DEFAULT_DEVICE_RPC_TIMEOUT_SECONDS,
    DEVICE_RPC_MAX_RESPONSE_BYTES,
    DEVICE_RPC_REQUEST_ID_BOOTSTRAP,
    SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS,
    SCREENSHOT_MAX_RPC_RESPONSE_BYTES,
    default_screenshot_params,
    default_snapshot_params,
)
from androidctld.snapshots.models import RawSnapshot, parse_raw_snapshot

_RESPONSE_READ_CHUNK_BYTES = 64 * 1024
_HTTP_ERROR_ENVELOPE_CONTEXT_MAX_BYTES = 16 * 1024
_HTTP_ERROR_CONTEXT_STRING_MAX_CHARS = 512
_HTTP_ERROR_SAFE_DETAIL_KEYS = (
    "reason",
    "path",
    "method",
    "max",
    "maxBytes",
    "contentLength",
    "field",
)
_SENSITIVE_DETAIL_KEY_PARTS = (
    "authorization",
    "bearer",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "apiKey",
    "api_key",
    "accessKey",
    "access_key",
)
_SENSITIVE_VALUE_PARTS = (
    "authorization:",
    "bearer ",
    "token=",
    "password=",
    "secret=",
    "http://",
    "https://",
)


@dataclass(frozen=True)
class _SafeHttpErrorEnvelope:
    details: dict[str, Any]
    device_code: str
    device_retryable: bool


@dataclass(frozen=True)
class _HttpErrorContext:
    details: dict[str, Any]
    envelope: _SafeHttpErrorEnvelope | None = None


def _transport_reset_reason(error: URLError | BaseException) -> tuple[str, str] | None:
    candidate: object = error.reason if isinstance(error, URLError) else error
    if isinstance(
        candidate,
        (ConnectionResetError, ConnectionAbortedError, RemoteDisconnected),
    ):
        return ("transport_reset", type(candidate).__name__)
    if isinstance(candidate, OSError) and "reset" in str(candidate).lower():
        return ("transport_reset", type(candidate).__name__)
    return None


class DeviceRpcClient:
    def __init__(
        self,
        endpoint: DeviceEndpoint,
        token: str,
        timeout: float = DEFAULT_DEVICE_RPC_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout

    def call_result_payload(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: str,
    ) -> object:
        if type(method) is not str:
            raise TypeError("device rpc method must be a string")
        return self._call_result_payload(method, params=params, request_id=request_id)

    def meta_get(self, request_id: str = DEVICE_RPC_REQUEST_ID_BOOTSTRAP) -> MetaInfo:
        return parse_meta_payload(
            self._call_result_payload(
                DeviceRpcMethod.META_GET,
                params=None,
                request_id=request_id,
            )
        )

    def snapshot_get(
        self,
        request_id: str = DEVICE_RPC_REQUEST_ID_BOOTSTRAP,
        params: dict[str, Any] | None = None,
    ) -> RawSnapshot:
        return parse_raw_snapshot(
            self._call_result_payload(
                DeviceRpcMethod.SNAPSHOT_GET,
                params=default_snapshot_params() if params is None else params,
                request_id=request_id,
            )
        )

    def action_perform(
        self, request: DeviceActionRequest, request_id: str
    ) -> ActionPerformResult:
        return parse_action_perform_result(
            self._call_result_payload(
                DeviceRpcMethod.ACTION_PERFORM,
                params=dump_device_action_request(request),
                request_id=request_id,
            )
        )

    def events_poll(
        self, after_seq: int, wait_ms: int, limit: int, request_id: str
    ) -> EventsPollResult:
        return parse_events_poll_result(
            self._call_result_payload(
                DeviceRpcMethod.EVENTS_POLL,
                params={
                    "afterSeq": after_seq,
                    "waitMs": wait_ms,
                    "limit": limit,
                },
                request_id=request_id,
            )
        )

    def screenshot_capture(self, request_id: str) -> ScreenshotCaptureResult:
        return parse_screenshot_capture_result(
            self._call_result_payload(
                DeviceRpcMethod.SCREENSHOT_CAPTURE,
                params=default_screenshot_params(),
                request_id=request_id,
            )
        )

    def _call_result_payload(
        self,
        method: DeviceRpcMethod | str,
        params: dict[str, Any] | None,
        request_id: str,
    ) -> object:
        method_name = method.value if isinstance(method, DeviceRpcMethod) else method
        body = json.dumps(
            {
                "id": request_id,
                "method": method_name,
                "params": {} if params is None else params,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self._endpoint.base_url}/rpc",
            method="POST",
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            timeout_seconds = _timeout_seconds(
                method_name,
                default_timeout=self._timeout,
            )
            with urlopen(request, timeout=timeout_seconds) as response:
                try:
                    response_body = _read_limited_response(
                        response,
                        method_name=method_name,
                        max_bytes=_max_response_bytes(method_name),
                    )
                    payload = json.loads(response_body.decode("utf-8"))
                except ValueError as error:
                    raise device_rpc_failed(
                        "device RPC response must be valid JSON",
                        {"reason": str(error)},
                        retryable=False,
                    ) from error
        except HTTPError as error:
            context = _http_error_context(error)
            if context.envelope is not None:
                if context.envelope.device_code == "UNAUTHORIZED":
                    raise device_agent_unauthorized(
                        "device agent rejected HTTP request",
                        context.details,
                        retryable=context.envelope.device_retryable,
                    ) from error
                raise device_rpc_failed(
                    "device agent rejected HTTP request",
                    context.details,
                    retryable=context.envelope.device_retryable,
                ) from error
            if error.code in {401, 403}:
                raise device_agent_unauthorized(
                    "device agent rejected HTTP request", context.details
                ) from error
            raise device_agent_unavailable(
                "device agent rejected HTTP request", context.details
            ) from error
        except (
            ConnectionResetError,
            ConnectionAbortedError,
            RemoteDisconnected,
        ) as error:
            reason, exception_name = _transport_reset_reason(error) or (
                "transport_reset",
                type(error).__name__,
            )
            raise device_rpc_transport_reset(
                "device RPC transport was reset",
                {"reason": reason, "exception": exception_name},
            ) from error
        except TimeoutError as error:
            raise device_agent_unavailable(
                "device RPC timed out",
                {
                    "reason": "device_rpc_timeout",
                    "method": method_name,
                    "timeoutSeconds": timeout_seconds,
                },
            ) from error
        except URLError as error:
            reset = _transport_reset_reason(error)
            if reset is not None:
                reason, exception_name = reset
                raise device_rpc_transport_reset(
                    "device RPC transport was reset",
                    {"reason": reason, "exception": exception_name},
                ) from error
            if isinstance(error.reason, TimeoutError):
                raise device_agent_unavailable(
                    "device RPC timed out",
                    {
                        "reason": "device_rpc_timeout",
                        "method": method_name,
                        "timeoutSeconds": timeout_seconds,
                    },
                ) from error
            raise device_agent_unavailable(
                "device agent is unavailable",
                {"reason": str(error.reason)},
            ) from error

        if not isinstance(payload, dict):
            raise device_rpc_failed(
                "device RPC response must be a JSON object", retryable=False
            )
        ok = payload.get("ok")
        if ok is True:
            return payload.get("result")
        if ok is False:
            raise parse_rpc_error_payload(payload.get("error"))
        raise device_rpc_failed(
            "device RPC ok must be a boolean", {"field": "ok"}, retryable=False
        )


def _max_response_bytes(method_name: str) -> int:
    if method_name == DeviceRpcMethod.SCREENSHOT_CAPTURE.value:
        return SCREENSHOT_MAX_RPC_RESPONSE_BYTES
    return DEVICE_RPC_MAX_RESPONSE_BYTES


def _timeout_seconds(method_name: str, *, default_timeout: float) -> float:
    if method_name == DeviceRpcMethod.SCREENSHOT_CAPTURE.value:
        return SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS
    return default_timeout


def _http_error_context(error: HTTPError) -> _HttpErrorContext:
    details: dict[str, Any] = {"status": error.code}
    read_result = _read_limited_http_error_body(
        error.fp,
        max_bytes=_HTTP_ERROR_ENVELOPE_CONTEXT_MAX_BYTES,
    )
    if read_result is None:
        return _HttpErrorContext(details)
    body, truncated = read_result
    if truncated:
        details.update(
            {
                "reason": "device_rpc_http_error_body_too_large",
                "maxBytes": _HTTP_ERROR_ENVELOPE_CONTEXT_MAX_BYTES,
            }
        )
        return _HttpErrorContext(details)
    if not body:
        return _HttpErrorContext(details)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _HttpErrorContext(details)
    envelope = _safe_http_error_envelope(payload)
    if envelope is None:
        return _HttpErrorContext(details)
    details.update(envelope.details)
    return _HttpErrorContext(details=details, envelope=envelope)


def _read_limited_http_error_body(
    fp: Any,
    *,
    max_bytes: int,
) -> tuple[bytes, bool] | None:
    if fp is None:
        return None
    try:
        chunk = fp.read(max_bytes + 1)
    except (OSError, ValueError):
        return None
    if not isinstance(chunk, bytes):
        return None
    if len(chunk) > max_bytes:
        return chunk[:max_bytes], True
    return chunk, False


def _safe_http_error_envelope(payload: object) -> _SafeHttpErrorEnvelope | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is not False:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = _trimmed_context_string(error.get("code"))
    raw_message = _trimmed_context_string(error.get("message"))
    retryable = error.get("retryable")
    error_details = error.get("details")
    if (
        code is None
        or raw_message is None
        or not isinstance(retryable, bool)
        or not isinstance(error_details, dict)
    ):
        return None

    details: dict[str, Any] = {
        "deviceCode": code,
        "deviceRetryable": retryable,
    }
    message = _safe_context_string(raw_message)
    if message is not None:
        details["deviceMessage"] = message
    for key in _HTTP_ERROR_SAFE_DETAIL_KEYS:
        if _is_sensitive_detail_key(key):
            continue
        value = _safe_http_error_detail_value(key, error_details.get(key))
        if value is not None:
            details[key] = value
    return _SafeHttpErrorEnvelope(
        details=details,
        device_code=code,
        device_retryable=retryable,
    )


def _safe_http_error_detail_value(
    key: str, value: object
) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, str):
        safe_value = _safe_context_string(value)
        if safe_value is None:
            return None
        if key == "path" and (
            not safe_value.startswith("/")
            or "?" in safe_value
            or "#" in safe_value
            or "://" in safe_value
        ):
            return None
        return safe_value
    return None


def _safe_context_string(value: object) -> str | None:
    normalized = _trimmed_context_string(value)
    if normalized is None or _contains_sensitive_value(normalized):
        return None
    return normalized


def _trimmed_context_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > _HTTP_ERROR_CONTEXT_STRING_MAX_CHARS:
        return None
    return normalized


def _is_sensitive_detail_key(key: str) -> bool:
    normalized = key.lower()
    return any(part.lower() in normalized for part in _SENSITIVE_DETAIL_KEY_PARTS)


def _contains_sensitive_value(value: str) -> bool:
    normalized = value.lower()
    return any(part in normalized for part in _SENSITIVE_VALUE_PARTS)


def _read_limited_response(
    response: Any,
    *,
    method_name: str,
    max_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(_RESPONSE_READ_CHUNK_BYTES, max_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise device_rpc_failed(
                "device RPC response exceeds size budget",
                {
                    "reason": "device_rpc_response_too_large",
                    "method": method_name,
                    "maxBytes": max_bytes,
                },
                retryable=False,
            )
