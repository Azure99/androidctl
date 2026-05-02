"""Device bootstrap and RPC errors."""

from __future__ import annotations

from typing import Any

from androidctld.errors import DaemonError, DaemonErrorCode


class DeviceBootstrapError(DaemonError):
    pass


def device_agent_unavailable(
    message: str, details: dict[str, Any] | None = None
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_AGENT_UNAVAILABLE,
        message=message,
        retryable=True,
        details=details or {},
        http_status=200,
    )


def device_agent_unauthorized(
    message: str,
    details: dict[str, Any] | None = None,
    *,
    retryable: bool = False,
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
        message=message,
        retryable=retryable,
        details=details or {},
        http_status=200,
    )


def version_mismatch(
    message: str, details: dict[str, Any] | None = None
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_AGENT_VERSION_MISMATCH,
        message=message,
        retryable=False,
        details=details or {},
        http_status=200,
    )


def capability_mismatch(
    message: str, details: dict[str, Any] | None = None
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_AGENT_CAPABILITY_MISMATCH,
        message=message,
        retryable=False,
        details=details or {},
        http_status=200,
    )


def accessibility_not_ready(
    message: str, details: dict[str, Any] | None = None
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.ACCESSIBILITY_NOT_READY,
        message=message,
        retryable=True,
        details=details or {},
        http_status=200,
    )


def device_rpc_failed(
    message: str,
    details: dict[str, Any] | None = None,
    retryable: bool = True,
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_RPC_FAILED,
        message=message,
        retryable=retryable,
        details=details or {},
        http_status=200,
    )


def device_rpc_transport_reset(
    message: str, details: dict[str, Any] | None = None
) -> DeviceBootstrapError:
    return DeviceBootstrapError(
        code=DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
        message=message,
        retryable=True,
        details=details or {},
        http_status=200,
    )
