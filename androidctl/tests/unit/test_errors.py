from __future__ import annotations

import click
import httpx

from androidctl.daemon.client import (
    DaemonApiError,
    IncompatibleDaemonError,
    IncompatibleDaemonVersionError,
)
from androidctl.errors.mapping import map_exception
from androidctl.exit_codes import ExitCode


def test_map_unknown_daemon_api_error_fails_closed() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="SOMETHING_NEW",
            message="daemon returned an unmapped boundary code",
            details={},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "daemon returned an unmapped boundary code"
    assert mapped.hint == "retry the command after the daemon is available"


def test_map_screen_not_ready_to_public_screen_unavailable() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="SCREEN_NOT_READY",
            message="screen is not ready yet",
            details={},
        )
    )

    assert mapped.code == "SCREEN_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ERROR
    assert mapped.hint == "run `androidctl observe` to refresh the current screen"


def test_map_runtime_not_connected_to_public_device_not_connected() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="RUNTIME_NOT_CONNECTED",
            message="runtime is not connected to a device",
            details={},
        )
    )

    assert mapped.code == "DEVICE_NOT_CONNECTED"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.hint == "re-run `androidctl connect`"


def test_map_workspace_busy_to_business_failure_with_hint() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="WORKSPACE_BUSY",
            message="workspace daemon is owned by another shell or agent",
            details={"ownerId": "shell:other:1"},
        )
    )

    assert mapped.code == "WORKSPACE_BUSY"
    assert mapped.exit_code == ExitCode.ERROR
    assert mapped.hint is not None


def test_map_runtime_busy_to_public_business_error_with_hint() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="RUNTIME_BUSY",
            message="runtime already has an in-flight progress command",
            details={},
        )
    )

    assert mapped.code == "RUNTIME_BUSY"
    assert mapped.exit_code == ExitCode.ERROR
    assert mapped.hint == "wait for the active progress command to finish, then retry"


def test_map_workspace_unavailable_preserves_documented_public_code() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="WORKSPACE_UNAVAILABLE",
            message="workspace is not available",
            details={},
        )
    )

    assert mapped.code == "WORKSPACE_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT


def test_map_artifact_root_unwritable_preserves_documented_public_code() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="ARTIFACT_ROOT_UNWRITABLE",
            message="workspace state is not writable",
            details={},
        )
    )

    assert mapped.code == "WORKSPACE_STATE_UNWRITABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT


def test_map_daemon_bad_request_preserves_message_without_retry_hint() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="DAEMON_BAD_REQUEST",
            message="invalid command payload",
            details={},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "invalid command payload"
    assert mapped.hint is None


def test_map_daemon_unauthorized_preserves_message_without_retry_hint() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="DAEMON_UNAUTHORIZED",
            message="missing or invalid daemon token",
            details={},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "missing or invalid daemon token"
    assert mapped.hint is None


def test_map_device_agent_unauthorized_to_daemon_unavailable() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="DEVICE_AGENT_UNAUTHORIZED",
            message="device agent rejected daemon authorization",
            details={},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "device agent rejected daemon authorization"
    assert mapped.hint == (
        "re-run androidctl connect to refresh daemon-to-device authorization"
    )


def test_map_transport_reset_to_fixed_public_surface() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="DEVICE_RPC_TRANSPORT_RESET",
            message="Connection reset by peer while reading response",
            details={"reason": "transport_reset", "exception": "ConnectionResetError"},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "device RPC transport was reset"
    assert mapped.hint == "retry the command after the daemon is available"


def test_map_internal_command_failure_to_fixed_public_surface() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="INTERNAL_COMMAND_FAILURE",
            message="boom",
            details={},
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == "androidctld failed while handling the request"
    assert mapped.hint == "retry the command; if it keeps failing, inspect daemon logs"


def test_map_transport_error_to_daemon_unavailable_environment_exit() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9999/commands/run")
    mapped = map_exception(httpx.ConnectError("connect failed", request=request))

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT


def test_map_incompatible_daemon_release_version_to_install_hint() -> None:
    mapped = map_exception(
        IncompatibleDaemonVersionError(
            expected_version="0.1.0",
            actual_version="0.1.1",
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == (
        "androidctl/androidctld release version mismatch: " "cli=0.1.0 daemon=0.1.1"
    )
    assert mapped.hint == "install matching androidctl and androidctld versions"


def test_map_incompatible_daemon_health_schema_to_install_hint() -> None:
    mapped = map_exception(
        IncompatibleDaemonError(
            "androidctl/androidctld health payload is incompatible; "
            "install matching androidctl and androidctld versions"
        )
    )

    assert mapped.code == "DAEMON_UNAVAILABLE"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.message == (
        "androidctl/androidctld health payload is incompatible; "
        "install matching androidctl and androidctld versions"
    )
    assert mapped.hint == "install matching androidctl and androidctld versions"


def test_map_device_agent_version_mismatch_to_install_hint() -> None:
    mapped = map_exception(
        DaemonApiError(
            code="DEVICE_AGENT_VERSION_MISMATCH",
            message="device agent release version mismatch",
            details={},
        )
    )

    assert mapped.code == "DEVICE_AGENT_VERSION_MISMATCH"
    assert mapped.exit_code == ExitCode.ENVIRONMENT
    assert mapped.hint == "install matching androidctld and Android agent/APK versions"


def test_map_usage_error_to_public_usage_error() -> None:
    mapped = map_exception(click.UsageError("bad args"))

    assert mapped.code == "USAGE_ERROR"
    assert mapped.exit_code == ExitCode.USAGE
