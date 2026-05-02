"""Error models and helpers for androidctld."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from androidctl_contracts.errors import (
    DaemonError as ContractDaemonError,
)
from androidctl_contracts.errors import DaemonErrorCode as ContractDaemonErrorCode


class DaemonErrorCode(str, Enum):
    DAEMON_BAD_REQUEST = "DAEMON_BAD_REQUEST"
    DAEMON_UNAUTHORIZED = "DAEMON_UNAUTHORIZED"
    WORKSPACE_BUSY = "WORKSPACE_BUSY"
    RUNTIME_BUSY = "RUNTIME_BUSY"
    RUNTIME_NOT_CONNECTED = "RUNTIME_NOT_CONNECTED"
    SCREEN_NOT_READY = "SCREEN_NOT_READY"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    COMMAND_CANCELLED = "COMMAND_CANCELLED"
    REF_RESOLUTION_FAILED = "REF_RESOLUTION_FAILED"
    REF_STALE = "REF_STALE"
    TARGET_BLOCKED = "TARGET_BLOCKED"
    TARGET_NOT_ACTIONABLE = "TARGET_NOT_ACTIONABLE"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    DEVICE_RPC_FAILED = "DEVICE_RPC_FAILED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    DEVICE_AGENT_UNAVAILABLE = "DEVICE_AGENT_UNAVAILABLE"
    DEVICE_AGENT_UNAUTHORIZED = "DEVICE_AGENT_UNAUTHORIZED"
    DEVICE_AGENT_VERSION_MISMATCH = "DEVICE_AGENT_VERSION_MISMATCH"
    DEVICE_AGENT_CAPABILITY_MISMATCH = "DEVICE_AGENT_CAPABILITY_MISMATCH"
    ACCESSIBILITY_NOT_READY = "ACCESSIBILITY_NOT_READY"
    OPEN_FAILED = "OPEN_FAILED"
    ACTION_NOT_CONFIRMED = "ACTION_NOT_CONFIRMED"
    TYPE_NOT_CONFIRMED = "TYPE_NOT_CONFIRMED"
    SUBMIT_NOT_CONFIRMED = "SUBMIT_NOT_CONFIRMED"
    DEVICE_RPC_TRANSPORT_RESET = "DEVICE_RPC_TRANSPORT_RESET"
    INTERNAL_COMMAND_FAILURE = "INTERNAL_COMMAND_FAILURE"
    WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"
    ARTIFACT_ROOT_UNWRITABLE = "ARTIFACT_ROOT_UNWRITABLE"
    ARTIFACT_WRITE_FAILED = "ARTIFACT_WRITE_FAILED"


_WIRE_CODE_BY_VALUE = {code.value: code for code in ContractDaemonErrorCode}


@dataclass
class DaemonError(Exception):
    code: DaemonErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    http_status: int = 200

    def __post_init__(self) -> None:
        self.code = DaemonErrorCode(self.code)

    def to_contract_error(self) -> ContractDaemonError:
        return ContractDaemonError(
            code=_to_contract_code(self.code),
            message=self.message,
            retryable=self.retryable,
            details=dict(self.details),
        )


def bad_request(message: str, details: dict[str, Any] | None = None) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DAEMON_BAD_REQUEST,
        message=message,
        retryable=False,
        details=details or {},
        http_status=400,
    )


def unauthorized(message: str = "missing or invalid daemon token") -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DAEMON_UNAUTHORIZED,
        message=message,
        retryable=False,
        details={},
        http_status=401,
    )


def _to_contract_code(code: DaemonErrorCode) -> ContractDaemonErrorCode:
    try:
        return _WIRE_CODE_BY_VALUE[code.value]
    except KeyError as error:
        raise ValueError(f"{code.value} is not legal in DaemonErrorEnvelope") from error
