"""Central semantic error mapping for semantic command results."""

from __future__ import annotations

from dataclasses import dataclass

from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.errors import DaemonError, DaemonErrorCode

_DIRECT_SEMANTIC_CODES: dict[DaemonErrorCode, SemanticResultCode] = {
    DaemonErrorCode.REF_STALE: SemanticResultCode.REF_STALE,
    DaemonErrorCode.WAIT_TIMEOUT: SemanticResultCode.WAIT_TIMEOUT,
    DaemonErrorCode.TARGET_BLOCKED: SemanticResultCode.TARGET_BLOCKED,
    DaemonErrorCode.TARGET_NOT_ACTIONABLE: SemanticResultCode.TARGET_NOT_ACTIONABLE,
    DaemonErrorCode.OPEN_FAILED: SemanticResultCode.OPEN_FAILED,
    DaemonErrorCode.ACTION_NOT_CONFIRMED: SemanticResultCode.ACTION_NOT_CONFIRMED,
    DaemonErrorCode.TYPE_NOT_CONFIRMED: SemanticResultCode.TYPE_NOT_CONFIRMED,
    DaemonErrorCode.SUBMIT_NOT_CONFIRMED: SemanticResultCode.SUBMIT_NOT_CONFIRMED,
}

_DEVICE_UNAVAILABLE_CODES = {
    DaemonErrorCode.RUNTIME_NOT_CONNECTED,
    DaemonErrorCode.SCREEN_NOT_READY,
    DaemonErrorCode.DEVICE_DISCONNECTED,
    DaemonErrorCode.DEVICE_AGENT_UNAVAILABLE,
    DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
    DaemonErrorCode.DEVICE_RPC_FAILED,
    DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
}

_MUTATING_COMMANDS = {
    "open",
    "tap",
    "longTap",
    "long-tap",
    "focus",
    "type",
    "submit",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
}

_REF_STALE_PUBLIC_MESSAGE = (
    "The referenced element is no longer available on the current screen."
)


@dataclass(frozen=True)
class SemanticFailure:
    code: SemanticResultCode
    message: str
    continuity_status_override: str | None = None


def map_daemon_error_to_semantic_failure(
    *,
    command_name: str,
    error: DaemonError,
    truth_lost_after_dispatch: bool = False,
) -> SemanticFailure | None:
    if error.code == DaemonErrorCode.REF_STALE:
        return SemanticFailure(
            code=SemanticResultCode.REF_STALE,
            message=_REF_STALE_PUBLIC_MESSAGE,
            continuity_status_override="stale",
        )

    direct_code = _DIRECT_SEMANTIC_CODES.get(error.code)
    if direct_code is not None:
        return SemanticFailure(code=direct_code, message=error.message)

    if error.code not in _DEVICE_UNAVAILABLE_CODES:
        return None

    if command_name in _MUTATING_COMMANDS and truth_lost_after_dispatch:
        return SemanticFailure(
            code=SemanticResultCode.POST_ACTION_OBSERVATION_LOST,
            message=(
                "Action may have been dispatched, but no current screen truth is "
                "available."
            ),
        )

    return SemanticFailure(
        code=SemanticResultCode.DEVICE_UNAVAILABLE,
        message="No current device observation is available.",
    )


__all__ = ["SemanticFailure", "map_daemon_error_to_semantic_failure"]
