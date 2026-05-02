from __future__ import annotations

import json
from http.client import RemoteDisconnected
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from androidctld.device.errors import DeviceBootstrapError
from androidctld.device.parsing import (
    decode_screenshot_body_base64,
    parse_meta_payload,
    parse_rpc_error_payload,
    validate_screenshot_png_bytes,
)
from androidctld.device.rpc import DeviceRpcClient
from androidctld.device.types import (
    ActionStatus,
    DeviceEndpoint,
    ResolvedCoordinatesTarget,
    ResolvedHandleTarget,
    ResolvedNoneTarget,
)
from androidctld.errors import DaemonError
from androidctld.protocol import DeviceRpcErrorCode, DeviceRpcMethod
from androidctld.runtime_policy import (
    DEVICE_RPC_REQUEST_ID_LIST_APPS,
    SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS,
    SCREENSHOT_MAX_OUTPUT_PIXELS,
)

if TYPE_CHECKING:
    from androidctld.device.action_models import DeviceActionRequest, TapActionRequest


def _png_header(width_px: int, height_px: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width_px.to_bytes(4, byteorder="big")
        + height_px.to_bytes(4, byteorder="big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body)
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


class _CaptureUrlopen:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.request_body: bytes | None = None
        self.timeout: object = None

    def __call__(self, request: Any, timeout: object = None) -> _FakeHttpResponse:
        self.request_body = request.data
        self.timeout = timeout
        return _FakeHttpResponse(self.payload)


class _BytesHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size is None or size < 0:
            size = len(self._body)
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk

    def close(self) -> None:
        pass

    def __enter__(self) -> _BytesHttpResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


class _UnreadableHttpResponse:
    def read(self, size: int = -1) -> bytes:
        del size
        raise OSError("read failed")

    def close(self) -> None:
        pass


def _http_error(status: int, body: bytes | None) -> HTTPError:
    fp = None if body is None else _BytesHttpResponse(body)
    return HTTPError(
        "http://127.0.0.1:17631/rpc",
        status,
        "HTTP error",
        hdrs=cast(Any, None),
        fp=cast(Any, fp),
    )


def _rpc_error_body(
    code: str,
    message: str,
    *,
    retryable: bool,
    details: dict[str, object] | None = None,
) -> bytes:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": {} if details is None else details,
            },
        }
    ).encode("utf-8")


def _typed_tap_request() -> TapActionRequest:
    from androidctld.device.action_models import HandleTarget, TapActionRequest
    from androidctld.refs.models import NodeHandle

    return TapActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3")),
        timeout_ms=8000,
    )


def _all_typed_action_requests() -> list[DeviceActionRequest]:
    from androidctld.device.action_models import (
        CoordinatesTarget,
        GlobalActionRequest,
        HandleTarget,
        LaunchAppActionRequest,
        LongTapActionRequest,
        NodeActionRequest,
        NoneTarget,
        OpenUrlActionRequest,
        ScrollActionRequest,
        SwipeActionRequest,
        TapActionRequest,
        TypeActionRequest,
    )
    from androidctld.refs.models import NodeHandle

    handle = HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3"))
    none = NoneTarget()
    return [
        TapActionRequest(target=handle, timeout_ms=8000),
        TapActionRequest(target=CoordinatesTarget(x=120, y=240), timeout_ms=8000),
        LongTapActionRequest(target=handle, timeout_ms=8000),
        LongTapActionRequest(
            target=CoordinatesTarget(x=120, y=240),
            timeout_ms=8000,
        ),
        TypeActionRequest(target=handle, text="wifi", submit=True, timeout_ms=8000),
        NodeActionRequest(target=handle, action="focus", timeout_ms=8000),
        ScrollActionRequest(target=handle, direction="down", timeout_ms=8000),
        SwipeActionRequest(target=none, direction="down", timeout_ms=8000),
        GlobalActionRequest(target=none, action="back", timeout_ms=8000),
        LaunchAppActionRequest(
            target=none,
            package_name="com.android.settings",
            timeout_ms=8000,
        ),
        OpenUrlActionRequest(
            target=none,
            url="https://example.com",
            timeout_ms=8000,
        ),
    ]


def test_device_rpc_client_normalizes_remote_disconnect() -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=ConnectionResetError("reset by peer"),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()
    assert error.value.code == "DEVICE_RPC_TRANSPORT_RESET"
    assert error.value.details["reason"] == "transport_reset"


def test_device_rpc_client_normalizes_wrapped_remote_disconnect() -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=URLError(ConnectionResetError("reset by peer")),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()
    assert error.value.code == "DEVICE_RPC_TRANSPORT_RESET"
    assert error.value.details["reason"] == "transport_reset"
    assert error.value.details["exception"] == "ConnectionResetError"


def test_device_rpc_client_normalizes_transport_abort() -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=URLError(
                ConnectionAbortedError("software caused connection abort")
            ),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()
    assert error.value.code == "DEVICE_RPC_TRANSPORT_RESET"
    assert error.value.details["reason"] == "transport_reset"
    assert error.value.details["exception"] == "ConnectionAbortedError"


def test_device_rpc_client_normalizes_unexpected_remote_close() -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=RemoteDisconnected(
                "remote end closed connection without response"
            ),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()
    assert error.value.code == "DEVICE_RPC_TRANSPORT_RESET"
    assert error.value.details["reason"] == "transport_reset"
    assert error.value.details["exception"] == "RemoteDisconnected"


def test_device_rpc_client_maps_http_unauthorized_status_to_stable_code() -> None:
    request_url = "http://127.0.0.1:17631/rpc"
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=HTTPError(
                request_url,
                401,
                "Unauthorized",
                hdrs=cast(Any, None),
                fp=None,
            ),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()
    assert error.value.code == "DEVICE_AGENT_UNAUTHORIZED"
    assert error.value.details["status"] == 401


def test_device_rpc_client_preserves_safe_http_error_envelope_context() -> None:
    body = json.dumps(
        {
            "ok": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": "payload is too large",
                "retryable": False,
                "details": {
                    "reason": "request_body_too_large",
                    "path": "/rpc",
                    "method": "POST",
                    "max": 8,
                    "maxBytes": 1048576,
                    "contentLength": 1048577,
                    "field": "body",
                    "params": {"raw": "not copied"},
                    "stackTrace": "not copied",
                    "token": "not copied",
                },
            },
        }
    ).encode("utf-8")

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(413, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details == {
        "status": 413,
        "deviceCode": "INVALID_REQUEST",
        "deviceRetryable": False,
        "deviceMessage": "payload is too large",
        "reason": "request_body_too_large",
        "path": "/rpc",
        "method": "POST",
        "max": 8,
        "maxBytes": 1048576,
        "contentLength": 1048577,
        "field": "body",
    }


def test_device_rpc_client_maps_retryable_http_error_envelope() -> None:
    body = _rpc_error_body(
        "INTERNAL_ERROR",
        "device handler failed",
        retryable=True,
        details={"reason": "internal_error", "method": "POST"},
    )

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(500, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.retryable
    assert error.value.details == {
        "status": 500,
        "deviceCode": "INTERNAL_ERROR",
        "deviceRetryable": True,
        "deviceMessage": "device handler failed",
        "reason": "internal_error",
        "method": "POST",
    }


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (500, True)])
def test_device_rpc_client_maps_http_unauthorized_envelope_by_device_code(
    status: int,
    retryable: bool,
) -> None:
    body = _rpc_error_body(
        "UNAUTHORIZED",
        "authentication required",
        retryable=retryable,
        details={"reason": "missing_authorization"},
    )

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(status, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_AGENT_UNAUTHORIZED"
    assert error.value.retryable is retryable
    assert error.value.details == {
        "status": status,
        "deviceCode": "UNAUTHORIZED",
        "deviceRetryable": retryable,
        "deviceMessage": "authentication required",
        "reason": "missing_authorization",
    }


@pytest.mark.parametrize("status", [401, 403])
def test_device_rpc_client_maps_http_auth_status_with_safe_context(
    status: int,
) -> None:
    body = json.dumps(
        {
            "ok": False,
            "error": {
                "code": "UNAUTHORIZED",
                "message": "authentication required",
                "retryable": False,
                "details": {"reason": "missing_authorization", "method": "POST"},
            },
        }
    ).encode("utf-8")

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(status, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_AGENT_UNAUTHORIZED"
    assert error.value.details == {
        "status": status,
        "deviceCode": "UNAUTHORIZED",
        "deviceRetryable": False,
        "deviceMessage": "authentication required",
        "reason": "missing_authorization",
        "method": "POST",
    }


@pytest.mark.parametrize("status", [401, 403])
def test_device_rpc_client_maps_http_auth_status_with_non_auth_envelope_first(
    status: int,
) -> None:
    body = _rpc_error_body(
        "INVALID_REQUEST",
        "request is invalid",
        retryable=False,
        details={"reason": "invalid_json", "method": "POST"},
    )

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(status, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details == {
        "status": status,
        "deviceCode": "INVALID_REQUEST",
        "deviceRetryable": False,
        "deviceMessage": "request is invalid",
        "reason": "invalid_json",
        "method": "POST",
    }


@pytest.mark.parametrize(
    ("status", "body", "expected_code"),
    [
        (500, b"not-json", "DEVICE_AGENT_UNAVAILABLE"),
        (500, b"[]", "DEVICE_AGENT_UNAVAILABLE"),
        (
            500,
            json.dumps({"ok": True, "result": {}}).encode("utf-8"),
            "DEVICE_AGENT_UNAVAILABLE",
        ),
        (
            500,
            json.dumps({"ok": False, "error": {"code": "INVALID_REQUEST"}}).encode(
                "utf-8"
            ),
            "DEVICE_AGENT_UNAVAILABLE",
        ),
        (
            400,
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "invalid",
                        "retryable": False,
                        "details": [],
                    },
                }
            ).encode("utf-8"),
            "DEVICE_AGENT_UNAVAILABLE",
        ),
        (401, b"not-json", "DEVICE_AGENT_UNAUTHORIZED"),
        (
            403,
            json.dumps({"ok": False, "error": {"code": "INVALID_REQUEST"}}).encode(
                "utf-8"
            ),
            "DEVICE_AGENT_UNAUTHORIZED",
        ),
    ],
)
def test_device_rpc_client_http_error_invalid_body_falls_back_to_status_only(
    status: int,
    body: bytes,
    expected_code: str,
) -> None:
    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(status, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == expected_code
    assert error.value.details == {"status": status}


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(401, "DEVICE_AGENT_UNAUTHORIZED"), (500, "DEVICE_AGENT_UNAVAILABLE")],
)
def test_device_rpc_client_http_error_unreadable_body_falls_back_to_status_only(
    status: int,
    expected_code: str,
) -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=HTTPError(
                "http://127.0.0.1:17631/rpc",
                status,
                "HTTP error",
                hdrs=cast(Any, None),
                fp=cast(Any, _UnreadableHttpResponse()),
            ),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == expected_code
    assert error.value.details == {"status": status}


def test_device_rpc_client_http_error_body_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "androidctld.device.rpc._HTTP_ERROR_ENVELOPE_CONTEXT_MAX_BYTES", 8
    )
    response = _BytesHttpResponse(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "too large",
                    "retryable": False,
                    "details": {"reason": "request_body_too_large"},
                },
            }
        ).encode("utf-8")
    )

    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=HTTPError(
                "http://127.0.0.1:17631/rpc",
                400,
                "Bad Request",
                hdrs=cast(Any, None),
                fp=cast(Any, response),
            ),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert error.value.details == {
        "status": 400,
        "reason": "device_rpc_http_error_body_too_large",
        "maxBytes": 8,
    }
    assert response.read_sizes == [9]


def test_device_rpc_client_http_error_strips_secret_and_unsafe_context() -> None:
    body = json.dumps(
        {
            "ok": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Authorization: Bearer not-copied",
                "retryable": False,
                "details": {
                    "reason": "Bearer not-copied",
                    "path": "/rpc?token=not-copied",
                    "method": "POST",
                    "field": "body",
                    "authorization": "Bearer not-copied",
                    "password": "not-copied",
                    "secret": "not-copied",
                    "token": "not-copied",
                    "requestBody": '{"token":"not-copied"}',
                    "responseBody": '{"secret":"not-copied"}',
                },
            },
        }
    ).encode("utf-8")

    with (
        patch("androidctld.device.rpc.urlopen", side_effect=_http_error(400, body)),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details == {
        "status": 400,
        "deviceCode": "INVALID_REQUEST",
        "deviceRetryable": False,
        "method": "POST",
        "field": "body",
    }


def test_device_rpc_response_larger_than_method_cap_fails_without_full_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.rpc.DEVICE_RPC_MAX_RESPONSE_BYTES", 8)
    response = _BytesHttpResponse(b'{"ok":true}' + b" ")

    with (
        patch("androidctld.device.rpc.urlopen", return_value=response),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).meta_get()

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details == {
        "reason": "device_rpc_response_too_large",
        "method": "meta.get",
        "maxBytes": 8,
    }
    assert response.read_sizes == [9]


def test_screenshot_capture_uses_screenshot_response_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.rpc.DEVICE_RPC_MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr("androidctld.device.rpc.SCREENSHOT_MAX_RPC_RESPONSE_BYTES", 128)
    payload = {
        "ok": True,
        "result": {
            "contentType": "image/png",
            "widthPx": 1,
            "heightPx": 1,
            "bodyBase64": "AA==",
        },
    }

    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(payload),
    ):
        result = DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).screenshot_capture(request_id="androidctld-test")

    assert result.body_base64 == "AA=="


def test_generic_screenshot_capture_uses_screenshot_response_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.rpc.DEVICE_RPC_MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr("androidctld.device.rpc.SCREENSHOT_MAX_RPC_RESPONSE_BYTES", 256)
    payload = {
        "ok": True,
        "result": {
            "contentType": "image/jpeg",
            "widthPx": 1,
            "heightPx": 1,
            "bodyBase64": "not typed base64",
        },
    }

    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(payload),
    ):
        result = DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).call_result_payload(
            "screenshot.capture",
            {"format": "jpeg", "scale": 0.5},
            request_id="androidctld-test",
        )

    assert result["contentType"] == "image/jpeg"


def test_screenshot_capture_uses_screenshot_transport_timeout() -> None:
    capture = _CaptureUrlopen(
        {
            "ok": True,
            "result": {
                "contentType": "image/png",
                "widthPx": 1,
                "heightPx": 1,
                "bodyBase64": "AA==",
            },
        }
    )

    with patch("androidctld.device.rpc.urlopen", capture):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).screenshot_capture(request_id="androidctld-test")

    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["method"] == "screenshot.capture"
    assert capture.timeout == SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS


def test_generic_screenshot_capture_uses_screenshot_transport_timeout() -> None:
    capture = _CaptureUrlopen(
        {
            "ok": True,
            "result": {
                "contentType": "image/jpeg",
                "widthPx": 1,
                "heightPx": 1,
                "bodyBase64": "not typed base64",
            },
        }
    )

    with patch("androidctld.device.rpc.urlopen", capture):
        result = DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).call_result_payload(
            "screenshot.capture",
            {"format": "jpeg", "scale": 0.5},
            request_id="androidctld-test",
        )

    assert result["contentType"] == "image/jpeg"
    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["method"] == "screenshot.capture"
    assert capture.timeout == SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS


def test_non_screenshot_rpc_keeps_generic_transport_timeout() -> None:
    capture = _CaptureUrlopen(
        {
            "ok": True,
            "result": {},
        }
    )

    with patch("androidctld.device.rpc.urlopen", capture):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).call_result_payload(
            "apps.list", {}, request_id=DEVICE_RPC_REQUEST_ID_LIST_APPS
        )

    assert capture.timeout == 0.25


def test_screenshot_timeout_error_reports_selected_timeout() -> None:
    with (
        patch("androidctld.device.rpc.urlopen", side_effect=TimeoutError()),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).screenshot_capture(request_id="androidctld-test")

    assert error.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert error.value.details == {
        "reason": "device_rpc_timeout",
        "method": "screenshot.capture",
        "timeoutSeconds": SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS,
    }


def test_generic_screenshot_wrapped_timeout_error_reports_selected_timeout() -> None:
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            side_effect=URLError(TimeoutError()),
        ),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).call_result_payload(
            "screenshot.capture",
            {"format": "jpeg", "scale": 0.5},
            request_id="androidctld-test",
        )

    assert error.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert error.value.details == {
        "reason": "device_rpc_timeout",
        "method": "screenshot.capture",
        "timeoutSeconds": SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS,
    }


def test_non_screenshot_timeout_error_reports_generic_transport_timeout() -> None:
    with (
        patch("androidctld.device.rpc.urlopen", side_effect=TimeoutError()),
        pytest.raises(DaemonError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631),
            "token",
            timeout=0.25,
        ).meta_get(request_id="androidctld-test")

    assert error.value.code == "DEVICE_AGENT_UNAVAILABLE"
    assert error.value.details == {
        "reason": "device_rpc_timeout",
        "method": "meta.get",
        "timeoutSeconds": 0.25,
    }


def test_screenshot_capture_response_larger_than_screenshot_cap_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.rpc.SCREENSHOT_MAX_RPC_RESPONSE_BYTES", 8)
    response = _BytesHttpResponse(b'{"ok":true}' + b" ")

    with (
        patch("androidctld.device.rpc.urlopen", return_value=response),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        DeviceRpcClient(
            DeviceEndpoint(host="127.0.0.1", port=17631), "token"
        ).screenshot_capture(request_id="androidctld-test")

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details == {
        "reason": "device_rpc_response_too_large",
        "method": "screenshot.capture",
        "maxBytes": 8,
    }


def test_dump_handle_target_preserves_wire_shape() -> None:
    from androidctld.device.action_models import HandleTarget
    from androidctld.device.action_serialization import dump_device_action_target
    from androidctld.refs.models import NodeHandle

    target = HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3"))
    assert dump_device_action_target(target) == {
        "kind": "handle",
        "handle": {"snapshotId": 42, "rid": "w1:0.3"},
    }


def test_dump_none_target_preserves_wire_shape() -> None:
    from androidctld.device.action_models import NoneTarget
    from androidctld.device.action_serialization import dump_device_action_target

    assert dump_device_action_target(NoneTarget()) == {"kind": "none"}


def test_dump_tap_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import HandleTarget, TapActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = TapActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3")),
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "tap",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.3"}},
        "options": {"timeoutMs": 8000},
    }


def test_dump_tap_action_request_supports_coordinates_target() -> None:
    from androidctld.device.action_models import CoordinatesTarget, TapActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = TapActionRequest(
        target=CoordinatesTarget(x=120, y=240),
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "tap",
        "target": {"kind": "coordinates", "x": 120, "y": 240},
        "options": {"timeoutMs": 8000},
    }


def test_dump_long_tap_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import HandleTarget, LongTapActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = LongTapActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3")),
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "longTap",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.3"}},
        "options": {"timeoutMs": 8000},
    }


def test_dump_long_tap_action_request_supports_coordinates_target() -> None:
    from androidctld.device.action_models import CoordinatesTarget, LongTapActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = LongTapActionRequest(
        target=CoordinatesTarget(x=120, y=240),
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "longTap",
        "target": {"kind": "coordinates", "x": 120, "y": 240},
        "options": {"timeoutMs": 8000},
    }


def test_dump_type_action_request_preserves_replace_submit_and_focus() -> None:
    from androidctld.device.action_models import HandleTarget, TypeActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = TypeActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.5")),
        text="wifi",
        submit=True,
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "type",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.5"}},
        "input": {
            "text": "wifi",
            "replace": True,
            "submit": True,
            "ensureFocused": True,
        },
        "options": {"timeoutMs": 8000},
    }


def test_dump_type_action_request_always_uses_replace_for_plain_type() -> None:
    from androidctld.device.action_models import HandleTarget, TypeActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = TypeActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.5")),
        text="more",
        submit=False,
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "type",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.5"}},
        "input": {
            "text": "more",
            "replace": True,
            "submit": False,
            "ensureFocused": True,
        },
        "options": {"timeoutMs": 8000},
    }


def test_dump_node_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import HandleTarget, NodeActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = NodeActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.5")),
        action="focus",
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "node",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.5"}},
        "node": {"action": "focus"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_scroll_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import HandleTarget, ScrollActionRequest
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = ScrollActionRequest(
        target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.9")),
        direction="down",
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "scroll",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.9"}},
        "scroll": {"direction": "down"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_global_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import GlobalActionRequest, NoneTarget
    from androidctld.device.action_serialization import dump_device_action_request

    request = GlobalActionRequest(target=NoneTarget(), action="back", timeout_ms=5000)
    assert dump_device_action_request(request) == {
        "kind": "global",
        "target": {"kind": "none"},
        "global": {"action": "back"},
        "options": {"timeoutMs": 5000},
    }


def test_dump_launch_app_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import LaunchAppActionRequest, NoneTarget
    from androidctld.device.action_serialization import dump_device_action_request

    request = LaunchAppActionRequest(
        target=NoneTarget(),
        package_name="com.android.settings",
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "launchApp",
        "target": {"kind": "none"},
        "intent": {"packageName": "com.android.settings"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_open_url_action_request_preserves_wire_shape() -> None:
    from androidctld.device.action_models import NoneTarget, OpenUrlActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = OpenUrlActionRequest(
        target=NoneTarget(),
        url="https://example.com",
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "openUrl",
        "target": {"kind": "none"},
        "intent": {"url": "https://example.com"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_open_url_action_request_supports_non_web_uri_wire_shape() -> None:
    from androidctld.device.action_models import NoneTarget, OpenUrlActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = OpenUrlActionRequest(
        target=NoneTarget(),
        url="smsto:10086?body=phase-d",
        timeout_ms=8000,
    )
    assert dump_device_action_request(request) == {
        "kind": "openUrl",
        "target": {"kind": "none"},
        "intent": {"url": "smsto:10086?body=phase-d"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_every_action_request_sends_timeout_options() -> None:
    from androidctld.device.action_serialization import dump_device_action_request

    for request in _all_typed_action_requests():
        payload = dump_device_action_request(request)
        assert payload["options"] == {"timeoutMs": 8000}


def test_dump_swipe_action_request_with_none_target() -> None:
    from androidctld.device.action_models import NoneTarget, SwipeActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = SwipeActionRequest(target=NoneTarget(), direction="down", timeout_ms=8000)
    assert dump_device_action_request(request) == {
        "kind": "gesture",
        "target": {"kind": "none"},
        "gesture": {"direction": "down"},
        "options": {"timeoutMs": 8000},
    }


def test_dump_swipe_action_request_rejects_coordinates_target() -> None:
    from androidctld.device.action_models import CoordinatesTarget, SwipeActionRequest
    from androidctld.device.action_serialization import dump_device_action_request

    request = SwipeActionRequest(
        target=CoordinatesTarget(x=120, y=240),  # type: ignore[arg-type]
        direction="down",
        timeout_ms=8000,
    )
    with pytest.raises(TypeError, match="swipe action requires none target"):
        dump_device_action_request(request)


def test_dump_device_action_request_rejects_built_request() -> None:
    from androidctld.device.action_models import (
        BuiltDeviceActionRequest,
        HandleTarget,
        TapActionRequest,
    )
    from androidctld.device.action_serialization import dump_device_action_request
    from androidctld.refs.models import NodeHandle

    request = BuiltDeviceActionRequest(
        payload=TapActionRequest(
            target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.3")),
            timeout_ms=8000,
        ),
        request_handle=NodeHandle(snapshot_id=42, rid="w1:0.3"),
    )
    with pytest.raises(TypeError):
        dump_device_action_request(cast(Any, request))


def test_dump_device_action_request_rejects_dict_payload_passthrough() -> None:
    from androidctld.device.action_serialization import dump_device_action_request

    unsupported_payload = {
        "kind": "tap",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.3"}},
        "options": {"timeoutMs": 8000},
    }
    with pytest.raises(TypeError, match="unsupported device action request"):
        dump_device_action_request(cast(Any, unsupported_payload))


def test_device_runtime_types_do_not_expose_to_json() -> None:
    from androidctld.device.types import (
        ActionPerformResult,
        DeviceEvent,
        EventsPollResult,
        ObservedApp,
        ResolvedHandleTarget,
        ScreenshotCaptureResult,
    )
    from androidctld.refs.models import NodeHandle

    handle = NodeHandle(snapshot_id=42, rid="w1:0.3")
    assert not hasattr(ObservedApp(), "to_json")
    assert not hasattr(ResolvedHandleTarget(handle=handle), "to_json")
    assert not hasattr(
        ActionPerformResult(
            action_id="act-1",
            status=ActionStatus.DONE,
            resolved_target=ResolvedHandleTarget(handle=handle),
            observed=ObservedApp(),
        ),
        "to_json",
    )
    assert not hasattr(
        DeviceEvent(
            seq=1, type="window.changed", timestamp="2026-03-24T00:00:00Z", data={}
        ),
        "to_json",
    )
    assert not hasattr(
        EventsPollResult(events=(), latest_seq=1, need_resync=False, timed_out=False),
        "to_json",
    )
    assert not hasattr(
        ScreenshotCaptureResult(
            content_type="image/png", width_px=1, height_px=1, body_base64="Zm9v"
        ),
        "to_json",
    )


def test_action_perform_boundary_normalizes_blank_observed_strings() -> None:
    from androidctld.device.adapters import adapt_action_perform_result
    from androidctld.device.schema import ActionPerformResultPayload

    dto = ActionPerformResultPayload.model_validate(
        {
            "actionId": "act-1",
            "status": "done",
            "observed": {"packageName": "", "activityName": " \t "},
        }
    )
    assert dto.observed is not None
    assert dto.observed.package_name is None
    assert dto.observed.activity_name is None

    result = adapt_action_perform_result(dto)
    assert result.observed is not None
    assert result.observed.package_name is None
    assert result.observed.activity_name is None


def test_parse_action_perform_result_normalizes_blanks_and_preserves_non_blank() -> (
    None
):
    from androidctld.device.parsing import parse_action_perform_result

    blank_result = parse_action_perform_result(
        {
            "actionId": "act-1",
            "status": "done",
            "observed": {"packageName": "", "activityName": " \t "},
        }
    )

    assert blank_result.observed is not None
    assert blank_result.observed.package_name is None
    assert blank_result.observed.activity_name is None

    result = parse_action_perform_result(
        {
            "actionId": "act-1",
            "status": "done",
            "observed": {
                "packageName": "com.example.app",
                "activityName": "  MainActivity  ",
            },
        }
    )

    assert result.observed is not None
    assert result.observed.package_name == "com.example.app"
    assert result.observed.activity_name == "  MainActivity  "


def test_resolved_target_adapter_builds_runtime_handle() -> None:
    from androidctld.device.adapters import adapt_resolved_target
    from androidctld.device.schema import ResolvedHandleTargetPayload

    target = adapt_resolved_target(
        ResolvedHandleTargetPayload.model_validate(
            {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.3"}}
        )
    )
    assert isinstance(target, ResolvedHandleTarget)
    assert target.handle.snapshot_id == 42
    assert target.handle.rid == "w1:0.3"


def test_resolved_handle_target_payload_requires_handle() -> None:
    from androidctld.device.schema import ResolvedHandleTargetPayload

    with pytest.raises(ValidationError):
        ResolvedHandleTargetPayload.model_validate({"kind": "handle"})


def test_resolved_target_adapter_builds_runtime_coordinates() -> None:
    from androidctld.device.adapters import adapt_resolved_target
    from androidctld.device.schema import ResolvedCoordinatesTargetPayload

    target = adapt_resolved_target(
        ResolvedCoordinatesTargetPayload.model_validate(
            {"kind": "coordinates", "x": 12.5, "y": 34.0}
        )
    )
    assert isinstance(target, ResolvedCoordinatesTarget)
    assert target.x == 12.5
    assert target.y == 34.0


def test_resolved_target_adapter_builds_runtime_none() -> None:
    from androidctld.device.adapters import adapt_resolved_target
    from androidctld.device.schema import ResolvedNoneTargetPayload

    target = adapt_resolved_target(
        ResolvedNoneTargetPayload.model_validate({"kind": "none"})
    )
    assert isinstance(target, ResolvedNoneTarget)
    assert target.kind == "none"


def test_resolved_target_boundary_rejects_unknown_kind() -> None:
    from pydantic import TypeAdapter

    from androidctld.device.schema import ActionResolvedTargetPayload

    with pytest.raises(ValidationError):
        TypeAdapter(ActionResolvedTargetPayload).validate_python(
            {"kind": "node", "handle": {"snapshotId": 42, "rid": "w1:0.3"}}
        )


def test_parse_meta_rejects_string_booleans() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        parse_meta_payload(
            {
                "service": "androidctl-device-agent",
                "version": "0.1.0",
                "capabilities": {
                    "supportsEventsPoll": "false",
                    "supportsScreenshot": True,
                    "actionKinds": ["tap"],
                },
            }
        )
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["field"] == "result.capabilities.supportsEventsPoll"


def test_parse_meta_rejects_non_string_action_kind() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        parse_meta_payload(
            {
                "service": "androidctl-device-agent",
                "version": "0.1.0",
                "capabilities": {
                    "supportsEventsPoll": True,
                    "supportsScreenshot": True,
                    "actionKinds": ["tap", 1],
                },
            }
        )
    assert error.value.details["field"] == "result.capabilities.actionKinds[1]"


def test_parse_meta_rejects_rpc_version_extra_field() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        parse_meta_payload(
            {
                "service": "androidctl-device-agent",
                "version": "0.1.0",
                "rpcVersion": 1,
                "capabilities": {
                    "supportsEventsPoll": True,
                    "supportsScreenshot": True,
                    "actionKinds": ["tap"],
                },
            }
        )
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["rpcVersion"],
    }


def test_parse_meta_rejects_unknown_extra_field_as_generic_schema_failure() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        parse_meta_payload(
            {
                "service": "androidctl-device-agent",
                "version": "0.1.0",
                "extraField": True,
                "capabilities": {
                    "supportsEventsPoll": True,
                    "supportsScreenshot": True,
                    "actionKinds": ["tap"],
                },
            }
        )
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["extraField"],
    }


def test_parse_meta_rejects_rpc_version_plus_extra_fields_as_generic_failure() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        parse_meta_payload(
            {
                "service": "androidctl-device-agent",
                "version": "0.1.0",
                "extraField": True,
                "rpcVersion": 1,
                "capabilities": {
                    "supportsEventsPoll": True,
                    "supportsScreenshot": True,
                    "actionKinds": ["tap"],
                },
            }
        )
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details == {
        "field": "result",
        "reason": "invalid_payload",
        "unknownFields": ["extraField", "rpcVersion"],
    }


def test_error_retryable_must_be_boolean() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "STALE_TARGET",
                        "message": "stale",
                        "retryable": "false",
                        "details": {},
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.action_perform(_typed_tap_request(), request_id="androidctld-test")
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["field"] == "error.retryable"


def test_parse_rpc_error_preserves_target_not_actionable_code() -> None:
    error = parse_rpc_error_payload(
        {
            "code": "TARGET_NOT_ACTIONABLE",
            "message": "target exists but is not actionable",
            "retryable": True,
            "details": {"reason": "disabled"},
        }
    )
    assert error.code == "DEVICE_RPC_FAILED"
    assert error.retryable
    assert DeviceRpcErrorCode.TARGET_NOT_ACTIONABLE.value == error.details["deviceCode"]
    assert error.details["details"] == {"reason": "disabled"}


@pytest.mark.parametrize(
    "device_code",
    [
        DeviceRpcErrorCode.ACTION_FAILED,
        DeviceRpcErrorCode.ACTION_TIMEOUT,
    ],
)
def test_parse_rpc_error_preserves_action_error_code(
    device_code: DeviceRpcErrorCode,
) -> None:
    error = parse_rpc_error_payload(
        {
            "code": device_code.value,
            "message": "action failed",
            "retryable": True,
            "details": {"reason": "gesture_rejected"},
        }
    )
    assert error.code == "DEVICE_RPC_FAILED"
    assert error.retryable
    assert error.details["deviceCode"] == device_code.value
    assert error.details["details"] == {"reason": "gesture_rejected"}


def test_parse_rpc_error_maps_unauthorized_to_stable_daemon_code() -> None:
    error = parse_rpc_error_payload(
        {
            "code": "UNAUTHORIZED",
            "message": "bad token",
            "retryable": False,
            "details": {"phase": "handshake"},
        }
    )
    assert error.code == "DEVICE_AGENT_UNAUTHORIZED"
    assert not error.retryable
    assert error.details["deviceCode"] == "UNAUTHORIZED"
    assert error.details["details"] == {"phase": "handshake"}


def test_events_poll_requires_structured_payload() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "events": [],
                        "latestSeq": "1",
                        "needResync": False,
                        "timedOut": True,
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.events_poll(0, 10, 1, request_id="androidctld-test")
    assert not error.value.retryable
    assert error.value.details["field"] == "result.latestSeq"


def test_call_result_payload_rejects_device_rpc_method_enum_before_transport() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch("androidctld.device.rpc.urlopen") as urlopen,
        pytest.raises(TypeError, match="device rpc method must be a string"),
    ):
        client.call_result_payload(
            cast(str, DeviceRpcMethod.ACTION_PERFORM),
            {"kind": "tap"},
            request_id="androidctld-test",
        )
    urlopen.assert_not_called()


def test_action_perform_returns_typed_result() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {"ok": True, "result": {"actionId": "act-1", "status": "done"}}
        ),
    ):
        payload = client.action_perform(
            _typed_tap_request(), request_id="androidctld-test"
        )
    assert payload.action_id == "act-1"
    assert payload.status == ActionStatus.DONE


def test_action_perform_serializes_typed_request() -> None:
    from androidctld.device.action_models import LaunchAppActionRequest, NoneTarget

    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    capture = _CaptureUrlopen(
        {"ok": True, "result": {"actionId": "act-1", "status": "done"}}
    )
    with patch("androidctld.device.rpc.urlopen", capture):
        payload = client.action_perform(
            LaunchAppActionRequest(
                target=NoneTarget(),
                package_name="com.android.settings",
                timeout_ms=8000,
            ),
            request_id="androidctld-test",
        )
    assert payload.action_id == "act-1"
    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["params"] == {
        "kind": "launchApp",
        "target": {"kind": "none"},
        "intent": {"packageName": "com.android.settings"},
        "options": {"timeoutMs": 8000},
    }


def test_action_perform_serializes_type_input_flags() -> None:
    from androidctld.device.action_models import HandleTarget, TypeActionRequest
    from androidctld.refs.models import NodeHandle

    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    capture = _CaptureUrlopen(
        {"ok": True, "result": {"actionId": "act-1", "status": "done"}}
    )
    with patch("androidctld.device.rpc.urlopen", capture):
        payload = client.action_perform(
            TypeActionRequest(
                target=HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.5")),
                text="wifi",
                submit=True,
                timeout_ms=8000,
            ),
            request_id="androidctld-test",
        )
    assert payload.action_id == "act-1"
    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["method"] == "action.perform"
    assert request_payload["params"] == {
        "kind": "type",
        "target": {"kind": "handle", "handle": {"snapshotId": 42, "rid": "w1:0.5"}},
        "input": {
            "text": "wifi",
            "replace": True,
            "submit": True,
            "ensureFocused": True,
        },
        "options": {"timeoutMs": 8000},
    }


def test_action_perform_parses_resolved_handle_target() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {
                "ok": True,
                "result": {
                    "actionId": "act-1",
                    "status": "done",
                    "resolvedTarget": {
                        "kind": "handle",
                        "handle": {"snapshotId": 42, "rid": "w1:0.3"},
                    },
                },
            }
        ),
    ):
        payload = client.action_perform(
            _typed_tap_request(), request_id="androidctld-test"
        )
    assert payload.resolved_target is not None
    assert isinstance(payload.resolved_target, ResolvedHandleTarget)
    assert payload.resolved_target.handle.snapshot_id == 42
    assert payload.resolved_target.handle.rid == "w1:0.3"


def test_action_perform_requires_supported_status() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {"ok": True, "result": {"actionId": "act-1", "status": "accepted"}}
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.action_perform(_typed_tap_request(), request_id="androidctld-test")
    assert error.value.details["field"] == "result.status"


def test_action_perform_accepts_timeout_status() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {"ok": True, "result": {"actionId": "act-1", "status": "timeout"}}
        ),
    ):
        payload = client.action_perform(
            _typed_tap_request(), request_id="androidctld-test"
        )
    assert payload.status is ActionStatus.TIMEOUT


def test_action_perform_accepts_coordinates_resolved_target() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {
                "ok": True,
                "result": {
                    "actionId": "act-1",
                    "status": "done",
                    "resolvedTarget": {"kind": "coordinates", "x": 12.0, "y": 34.0},
                },
            }
        ),
    ):
        payload = client.action_perform(
            _typed_tap_request(), request_id="androidctld-test"
        )
    assert ResolvedCoordinatesTarget(x=12.0, y=34.0) == payload.resolved_target


def test_action_perform_accepts_none_resolved_target() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {
                "ok": True,
                "result": {
                    "actionId": "act-1",
                    "status": "done",
                    "resolvedTarget": {"kind": "none"},
                },
            }
        ),
    ):
        payload = client.action_perform(
            _typed_tap_request(), request_id="androidctld-test"
        )
    assert ResolvedNoneTarget() == payload.resolved_target


def test_action_perform_rejects_unknown_resolved_target_kind() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "actionId": "act-1",
                        "status": "done",
                        "resolvedTarget": {"kind": "node"},
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.action_perform(_typed_tap_request(), request_id="androidctld-test")
    assert error.value.details["field"] == "result.resolvedTarget.kind"
    expected_message = (
        "device RPC result.resolvedTarget.kind must be one of handle|coordinates|none"
    )
    assert error.value.message == expected_message


def test_action_perform_rejects_handle_resolved_target_without_handle() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "actionId": "act-1",
                        "status": "done",
                        "resolvedTarget": {"kind": "handle"},
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.action_perform(_typed_tap_request(), request_id="androidctld-test")
    assert error.value.details["field"] == "result.resolvedTarget.handle"
    assert error.value.message == "device RPC result.resolvedTarget.handle is required"


def test_action_perform_rejects_dict_request_passthrough() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with pytest.raises(TypeError, match="unsupported device action request"):
        client.action_perform(cast(Any, {"kind": "tap"}), request_id="androidctld-test")


def test_action_perform_rejects_built_request_before_transport() -> None:
    from androidctld.device.action_models import BuiltDeviceActionRequest

    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    request = BuiltDeviceActionRequest(payload=_typed_tap_request())
    with (
        patch("androidctld.device.rpc.urlopen") as urlopen_mock,
        pytest.raises(TypeError, match="unsupported device action request"),
    ):
        client.action_perform(cast(Any, request), request_id="androidctld-test")
    urlopen_mock.assert_not_called()


def test_snapshot_get_sends_default_params() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    capture = _CaptureUrlopen(
        {
            "ok": True,
            "result": {
                "snapshotId": 1,
                "capturedAt": "2026-03-17T00:00:00Z",
                "packageName": "com.android.settings",
                "activityName": None,
                "display": {
                    "widthPx": 1,
                    "heightPx": 1,
                    "densityDpi": 1,
                    "rotation": 0,
                },
                "ime": {"visible": False, "windowId": None},
                "windows": [
                    {
                        "windowId": "w1",
                        "type": "application",
                        "layer": 0,
                        "packageName": "com.android.settings",
                        "bounds": [0, 0, 1, 1],
                        "rootRid": "w1:0",
                    }
                ],
                "nodes": [
                    {
                        "rid": "w1:0",
                        "windowId": "w1",
                        "parentRid": None,
                        "childRids": [],
                        "className": "android.view.View",
                        "resourceId": None,
                        "text": "Settings",
                        "contentDesc": None,
                        "hintText": None,
                        "stateDescription": None,
                        "paneTitle": None,
                        "packageName": "com.android.settings",
                        "bounds": [0, 0, 1, 1],
                        "visibleToUser": True,
                        "importantForAccessibility": True,
                        "clickable": False,
                        "enabled": True,
                        "editable": False,
                        "focusable": False,
                        "focused": False,
                        "checkable": False,
                        "checked": False,
                        "selected": False,
                        "scrollable": False,
                        "password": False,
                        "actions": [],
                    }
                ],
            },
        }
    )

    with patch("androidctld.device.rpc.urlopen", capture):
        payload = client.snapshot_get(request_id="androidctld-test")

    assert payload.snapshot_id == 1
    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["method"] == "snapshot.get"
    assert request_payload["params"] == {
        "includeInvisible": True,
        "includeSystemWindows": True,
    }


def test_screenshot_capture_requires_base64_string() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "contentType": "image/png",
                        "widthPx": 1,
                        "heightPx": 1,
                        "bodyBase64": 3,
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.screenshot_capture(request_id="androidctld-test")
    assert error.value.details["field"] == "result.bodyBase64"


def test_screenshot_capture_requires_png_content_type() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "contentType": "image/jpeg",
                        "widthPx": 1,
                        "heightPx": 1,
                        "bodyBase64": "AA==",
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.screenshot_capture(request_id="androidctld-test")
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["field"] == "result.contentType"
    assert error.value.details["reason"] == "unsupported_content_type"


def test_screenshot_capture_rejects_dimensions_over_pixel_budget() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "contentType": "image/png",
                        "widthPx": 4097,
                        "heightPx": 4097,
                        "bodyBase64": "AA==",
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.screenshot_capture(request_id="androidctld-test")
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["reason"] == "screenshot_dimensions_too_large"
    assert error.value.details["maxPixels"] == SCREENSHOT_MAX_OUTPUT_PIXELS


def test_screenshot_capture_rejects_base64_over_char_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.parsing.SCREENSHOT_MAX_BASE64_CHARS", 3)
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "contentType": "image/png",
                        "widthPx": 1,
                        "heightPx": 1,
                        "bodyBase64": "AAAA",
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.screenshot_capture(request_id="androidctld-test")
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details["reason"] == "screenshot_base64_too_large"
    assert error.value.details["maxChars"] == 3


def test_decode_screenshot_body_base64_rejects_invalid_base64() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        decode_screenshot_body_base64(
            "not base64!",
            field_name="result.bodyBase64",
        )
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["reason"] == "invalid_base64"


def test_validate_screenshot_png_bytes_rejects_ihdr_metadata_mismatch() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        validate_screenshot_png_bytes(
            _png_header(1, 1),
            field_name="result.bodyBase64",
            expected_width_px=2,
            expected_height_px=1,
        )

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["reason"] == "screenshot_dimensions_mismatch"
    assert error.value.details["expectedWidthPx"] == 2
    assert error.value.details["actualWidthPx"] == 1


def test_validate_screenshot_png_bytes_rejects_ihdr_dimensions_over_budget() -> None:
    with pytest.raises(DeviceBootstrapError) as error:
        validate_screenshot_png_bytes(
            _png_header(SCREENSHOT_MAX_OUTPUT_PIXELS + 1, 1),
            field_name="result.bodyBase64",
            expected_width_px=SCREENSHOT_MAX_OUTPUT_PIXELS + 1,
            expected_height_px=1,
        )

    assert error.value.code == "DEVICE_RPC_FAILED"
    assert not error.value.retryable
    assert error.value.details["reason"] == "screenshot_dimensions_too_large"
    assert error.value.details["maxPixels"] == SCREENSHOT_MAX_OUTPUT_PIXELS


def test_screenshot_capture_rejects_decoded_body_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("androidctld.device.parsing.SCREENSHOT_MAX_BINARY_BYTES", 1)
    monkeypatch.setattr("androidctld.device.parsing.SCREENSHOT_MAX_BASE64_CHARS", 100)
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with (
        patch(
            "androidctld.device.rpc.urlopen",
            return_value=_FakeHttpResponse(
                {
                    "ok": True,
                    "result": {
                        "contentType": "image/png",
                        "widthPx": 1,
                        "heightPx": 1,
                        "bodyBase64": "AAA=",
                    },
                }
            ),
        ),
        pytest.raises(DeviceBootstrapError) as error,
    ):
        client.screenshot_capture(request_id="androidctld-test")
    assert error.value.code == "DEVICE_RPC_FAILED"
    assert error.value.details["reason"] == "screenshot_decoded_too_large"
    assert error.value.details["maxBytes"] == 1


def test_generic_screenshot_jpeg_result_remains_object_passthrough() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse(
            {
                "ok": True,
                "result": {
                    "contentType": "image/jpeg",
                    "widthPx": 1,
                    "heightPx": 1,
                    "bodyBase64": "not typed base64",
                },
            }
        ),
    ):
        result = client.call_result_payload(
            "screenshot.capture",
            {"format": "jpeg", "scale": 0.5},
            request_id="androidctld-test",
        )

    assert result == {
        "contentType": "image/jpeg",
        "widthPx": 1,
        "heightPx": 1,
        "bodyBase64": "not typed base64",
    }


def test_call_result_payload_allows_non_object_result() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    with patch(
        "androidctld.device.rpc.urlopen",
        return_value=_FakeHttpResponse({"ok": True, "result": ["not-object"]}),
    ):
        result = client.call_result_payload(
            "apps.list",
            {},
            request_id=DEVICE_RPC_REQUEST_ID_LIST_APPS,
        )

    assert result == ["not-object"]


def test_screenshot_capture_sends_default_png_params() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )
    capture = _CaptureUrlopen(
        {
            "ok": True,
            "result": {
                "contentType": "image/png",
                "widthPx": 1,
                "heightPx": 1,
                "bodyBase64": "AA==",
            },
        }
    )

    with patch("androidctld.device.rpc.urlopen", capture):
        client.screenshot_capture(request_id="androidctld-test")

    assert capture.request_body is not None
    request_payload = json.loads(capture.request_body.decode("utf-8"))
    assert request_payload["method"] == "screenshot.capture"
    assert request_payload["params"] == {"format": "png", "scale": 1.0}


def test_screenshot_capture_rejects_caller_params_before_transport() -> None:
    client = DeviceRpcClient(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171), token="device-token"
    )

    with (
        patch("androidctld.device.rpc.urlopen") as urlopen_mock,
        pytest.raises(TypeError),
    ):
        client.screenshot_capture(  # type: ignore[call-arg]
            request_id="androidctld-test",
            params={"format": "jpeg", "scale": 0.5},
        )

    urlopen_mock.assert_not_called()
