from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import click
import httpx

from androidctl.daemon.client import (
    DaemonApiError,
    DaemonProtocolError,
    IncompatibleDaemonError,
)
from androidctl.errors.models import PublicError
from androidctl.exit_codes import ExitCode


@dataclass(frozen=True)
class _MappedCode:
    code: str
    exit_code: ExitCode
    hint: str | None = None
    message: str | None = None


_DAEMON_CODE_TABLE: Mapping[str, _MappedCode] = {
    # This table only applies to daemon error envelopes, not semantic result codes.
    "DAEMON_BAD_REQUEST": _MappedCode("DAEMON_UNAVAILABLE", ExitCode.ENVIRONMENT),
    "WORKSPACE_BUSY": _MappedCode(
        "WORKSPACE_BUSY",
        ExitCode.ERROR,
        "close the conflicting workspace daemon or use a different workspace",
    ),
    "RUNTIME_BUSY": _MappedCode(
        "RUNTIME_BUSY",
        ExitCode.ERROR,
        "wait for the active progress command to finish, then retry",
    ),
    "RUNTIME_NOT_CONNECTED": _MappedCode(
        "DEVICE_NOT_CONNECTED",
        ExitCode.ENVIRONMENT,
        "re-run `androidctl connect`",
    ),
    "SCREEN_NOT_READY": _MappedCode(
        "SCREEN_UNAVAILABLE",
        ExitCode.ERROR,
        "run `androidctl observe` to refresh the current screen",
    ),
    "REF_RESOLUTION_FAILED": _MappedCode(
        "REF_NOT_FOUND",
        ExitCode.ERROR,
        "run `androidctl observe` and choose a ref from the latest screen",
    ),
    "WORKSPACE_UNAVAILABLE": _MappedCode("WORKSPACE_UNAVAILABLE", ExitCode.ENVIRONMENT),
    "ARTIFACT_ROOT_UNWRITABLE": _MappedCode(
        "WORKSPACE_STATE_UNWRITABLE", ExitCode.ENVIRONMENT
    ),
    "DEVICE_DISCONNECTED": _MappedCode(
        "DEVICE_NOT_CONNECTED",
        ExitCode.ENVIRONMENT,
    ),
    "DEVICE_AGENT_UNAVAILABLE": _MappedCode(
        "DEVICE_AGENT_UNAVAILABLE", ExitCode.ENVIRONMENT
    ),
    "DEVICE_AGENT_VERSION_MISMATCH": _MappedCode(
        "DEVICE_AGENT_VERSION_MISMATCH",
        ExitCode.ENVIRONMENT,
        "install matching androidctld and Android agent/APK versions",
    ),
    "DEVICE_AGENT_UNAUTHORIZED": _MappedCode(
        "DAEMON_UNAVAILABLE",
        ExitCode.ENVIRONMENT,
        "re-run androidctl connect to refresh daemon-to-device authorization",
        "device agent rejected daemon authorization",
    ),
    "ACCESSIBILITY_NOT_READY": _MappedCode("ACCESSIBILITY_NOT_READY", ExitCode.ERROR),
    "DAEMON_UNAUTHORIZED": _MappedCode("DAEMON_UNAVAILABLE", ExitCode.ENVIRONMENT),
    "INTERNAL_COMMAND_FAILURE": _MappedCode(
        "DAEMON_UNAVAILABLE",
        ExitCode.ENVIRONMENT,
        "retry the command; if it keeps failing, inspect daemon logs",
        "androidctld failed while handling the request",
    ),
    "DEVICE_RPC_TRANSPORT_RESET": _MappedCode(
        "DAEMON_UNAVAILABLE",
        ExitCode.ENVIRONMENT,
        "retry the command after the daemon is available",
        "device RPC transport was reset",
    ),
}


def map_exception(error: Exception) -> PublicError:
    if isinstance(error, click.UsageError):
        return PublicError(
            code="USAGE_ERROR",
            message=error.format_message(),
            hint=None,
            exit_code=ExitCode.USAGE,
        )
    if isinstance(error, DaemonApiError):
        return _map_daemon_api_error(error)
    if isinstance(error, IncompatibleDaemonError):
        return PublicError(
            code="DAEMON_UNAVAILABLE",
            message=str(error),
            hint="install matching androidctl and androidctld versions",
            exit_code=ExitCode.ENVIRONMENT,
        )
    if isinstance(error, (httpx.RequestError, httpx.HTTPStatusError)):
        return PublicError(
            code="DAEMON_UNAVAILABLE",
            message="unable to reach androidctld daemon",
            hint="retry the command after the daemon is available",
            exit_code=ExitCode.ENVIRONMENT,
        )
    if isinstance(
        error,
        (DaemonProtocolError, click.ClickException, RuntimeError, OSError),
    ):
        return PublicError(
            code="DAEMON_UNAVAILABLE",
            message=str(error),
            hint="retry the command after the daemon is available",
            exit_code=ExitCode.ENVIRONMENT,
        )
    return PublicError(
        code="DAEMON_UNAVAILABLE",
        message=str(error),
        hint="retry the command after the daemon is available",
        exit_code=ExitCode.ENVIRONMENT,
    )


def _map_daemon_api_error(error: DaemonApiError) -> PublicError:
    mapped = _DAEMON_CODE_TABLE.get(error.code)
    if mapped is None:
        return PublicError(
            code="DAEMON_UNAVAILABLE",
            message=error.message,
            hint="retry the command after the daemon is available",
            exit_code=ExitCode.ENVIRONMENT,
        )
    return PublicError(
        code=mapped.code,
        message=mapped.message or error.message,
        hint=mapped.hint,
        exit_code=mapped.exit_code,
    )
