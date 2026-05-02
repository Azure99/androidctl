from __future__ import annotations

from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.commands.semantic_error_mapping import (
    map_daemon_error_to_semantic_failure,
)
from androidctld.errors import DaemonError, DaemonErrorCode


def _daemon_error(
    code: DaemonErrorCode,
    *,
    message: str = "failure",
    details: dict[str, object] | None = None,
) -> DaemonError:
    return DaemonError(
        code=code,
        message=message,
        retryable=True,
        details={} if details is None else dict(details),
        http_status=200,
    )


def test_mapper_normalizes_runtime_disconnect_to_device_unavailable() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="observe",
        error=_daemon_error(
            DaemonErrorCode.RUNTIME_NOT_CONNECTED,
            message="runtime is not connected to a device",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.DEVICE_UNAVAILABLE
    assert failure.message == "No current device observation is available."


def test_mapper_normalizes_wait_screen_not_ready_to_device_unavailable() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="wait",
        error=_daemon_error(
            DaemonErrorCode.SCREEN_NOT_READY,
            message="No current device observation is available.",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.DEVICE_UNAVAILABLE
    assert failure.message == "No current device observation is available."


def test_mapper_passes_through_wait_timeout_as_semantic_code() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="wait",
        error=_daemon_error(
            DaemonErrorCode.WAIT_TIMEOUT,
            message="Condition was not satisfied before timeout.",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.WAIT_TIMEOUT


def test_mapper_passes_through_target_blocked_as_semantic_code() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="tap",
        error=_daemon_error(
            DaemonErrorCode.TARGET_BLOCKED,
            message="target is blocked",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.TARGET_BLOCKED
    assert failure.continuity_status_override is None


def test_mapper_projects_ref_stale_without_repair_diagnostics() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="tap",
        error=_daemon_error(
            DaemonErrorCode.REF_STALE,
            message="ref could not be repaired: repair_failed",
            details={
                "sourceArtifactStatus": "repair_failed",
                "sourceArtifact": "/workspace/.androidctl/screens/obs-00041.json",
            },
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.REF_STALE
    assert (
        failure.message
        == "The referenced element is no longer available on the current screen."
    )
    assert "repair" not in failure.message
    assert "sourceArtifactStatus" not in failure.message
    assert "repair_failed" not in failure.message
    assert failure.continuity_status_override == "stale"


def test_mapper_passes_through_open_failed_as_semantic_code() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="open",
        error=_daemon_error(
            DaemonErrorCode.OPEN_FAILED,
            message="open failed",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.OPEN_FAILED


def test_mapper_projects_action_not_confirmed_as_semantic_code() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="long-tap",
        error=_daemon_error(
            DaemonErrorCode.ACTION_NOT_CONFIRMED,
            message="action was not confirmed on the refreshed screen",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.ACTION_NOT_CONFIRMED
    assert failure.message == "action was not confirmed on the refreshed screen"


def test_mapper_uses_typed_truth_loss_signal_for_tap_observation_loss() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="tap",
        error=_daemon_error(
            DaemonErrorCode.DEVICE_RPC_FAILED,
            message="device rpc failed",
        ),
        truth_lost_after_dispatch=True,
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert (
        failure.message
        == "Action may have been dispatched, but no current screen truth is available."
    )


def test_mapper_uses_typed_truth_loss_signal_for_long_tap_observation_loss() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="longTap",
        error=_daemon_error(
            DaemonErrorCode.DEVICE_RPC_FAILED,
            message="device rpc failed",
        ),
        truth_lost_after_dispatch=True,
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.POST_ACTION_OBSERVATION_LOST


def test_mapper_keeps_availability_failure_device_unavailable_by_default() -> None:
    failure = map_daemon_error_to_semantic_failure(
        command_name="tap",
        error=_daemon_error(
            DaemonErrorCode.DEVICE_RPC_FAILED,
            message="device rpc failed",
        ),
    )

    assert failure is not None
    assert failure.code is SemanticResultCode.DEVICE_UNAVAILABLE


def test_mapper_leaves_internal_command_failure_in_daemon_envelope() -> None:
    assert (
        map_daemon_error_to_semantic_failure(
            command_name="observe",
            error=_daemon_error(
                DaemonErrorCode.INTERNAL_COMMAND_FAILURE,
                message="internal command failure",
            ),
        )
        is None
    )


def test_mapper_leaves_accessibility_not_ready_in_daemon_envelope() -> None:
    assert (
        map_daemon_error_to_semantic_failure(
            command_name="connect",
            error=_daemon_error(
                DaemonErrorCode.ACCESSIBILITY_NOT_READY,
                message="accessibility not ready",
            ),
        )
        is None
    )
