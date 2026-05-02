"""Semantic action command handler."""

from __future__ import annotations

from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.actions.executor import ActionExecutionFailure, ActionExecutor
from androidctld.commands.command_models import (
    GlobalCommand,
    OpenCommand,
    RefBoundActionCommand,
)
from androidctld.commands.orchestration import current_command_record
from androidctld.commands.result_models import (
    SemanticResultAssemblyInput,
    build_semantic_failure_result,
    build_semantic_success_result,
)
from androidctld.commands.semantic_command_names import (
    semantic_result_command_for_daemon_kind,
)
from androidctld.commands.semantic_error_mapping import (
    SemanticFailure,
    map_daemon_error_to_semantic_failure,
)
from androidctld.commands.semantic_truth import (
    capture_runtime_source_basis,
    resolve_global_action_source_basis,
    resolve_open_changed,
    resolve_runtime_continuity,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_artifacts,
    current_public_screen,
    get_authoritative_current_basis,
)
from androidctld.semantics.compiler import CompiledScreen

_AVAILABILITY_CODES = {
    DaemonErrorCode.RUNTIME_NOT_CONNECTED,
    DaemonErrorCode.SCREEN_NOT_READY,
    DaemonErrorCode.DEVICE_DISCONNECTED,
    DaemonErrorCode.DEVICE_AGENT_UNAVAILABLE,
    DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
    DaemonErrorCode.DEVICE_RPC_FAILED,
    DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
}
_CONFIRMATION_FAILURE_CODES = {
    SemanticResultCode.ACTION_NOT_CONFIRMED,
    SemanticResultCode.TYPE_NOT_CONFIRMED,
    SemanticResultCode.SUBMIT_NOT_CONFIRMED,
}
_POST_ACTION_OBSERVATION_LOST_MESSAGE = (
    "Action may have been dispatched, but no current screen truth is available."
)


def _command_result_payload(
    *,
    command_name: str,
    category: str,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    runtime: WorkspaceRuntime,
    assembly_input: SemanticResultAssemblyInput | None = None,
    require_compiled_source_for_changed: bool = False,
    continuity_status_override: str | None = None,
    changed_override: bool | None = None,
) -> dict[str, object]:
    next_screen = current_public_screen(runtime)
    artifacts = current_artifacts(runtime)
    if continuity_status_override is None and changed_override is None:
        continuity_status, changed = _resolve_continuity(
            category=category,
            source_screen_id=source_screen_id,
            source_compiled_screen=source_compiled_screen,
            runtime=runtime,
            require_compiled_source_for_changed=require_compiled_source_for_changed,
        )
    else:
        continuity_status = (
            continuity_status_override
            if continuity_status_override is not None
            else "none"
        )
        changed = changed_override
    return build_semantic_success_result(
        command=command_name,
        category=category,
        source_screen_id=source_screen_id,
        next_screen=next_screen,
        app_payload=(None if assembly_input is None else assembly_input.app_payload),
        artifacts=artifacts,
        continuity_status=continuity_status,
        execution_outcome=(
            "dispatched"
            if assembly_input is None
            else getattr(assembly_input, "execution_outcome", "dispatched")
        ),
        changed=changed,
        action_target=(
            None
            if assembly_input is None
            else getattr(assembly_input, "action_target", None)
        ),
        warnings=([] if assembly_input is None else list(assembly_input.warnings)),
    ).model_dump(by_alias=True, mode="json")


def _command_error_payload(
    *,
    command_name: str,
    category: str,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    runtime: WorkspaceRuntime,
    error: DaemonError,
    require_compiled_source_for_changed: bool = False,
    continuity_status_override: str | None = None,
    changed_override: bool | None = None,
    dispatch_attempted: bool = False,
    truth_lost_after_dispatch: bool = False,
) -> dict[str, object]:
    mapped = map_daemon_error_to_semantic_failure(
        command_name=command_name,
        error=error,
        truth_lost_after_dispatch=truth_lost_after_dispatch,
    )
    if mapped is None:
        raise error
    if mapped.code in _CONFIRMATION_FAILURE_CODES:
        if truth_lost_after_dispatch:
            basis = None
        else:
            basis = get_authoritative_current_basis(runtime)
        if basis is None:
            next_screen = None
            artifacts = None
            mapped = SemanticFailure(
                code=SemanticResultCode.POST_ACTION_OBSERVATION_LOST,
                message=_POST_ACTION_OBSERVATION_LOST_MESSAGE,
            )
        else:
            next_screen = basis.public_screen
            artifacts = basis.artifacts
    else:
        next_screen = (
            None if truth_lost_after_dispatch else current_public_screen(runtime)
        )
        artifacts = current_artifacts(runtime)
    if continuity_status_override is None and changed_override is None:
        continuity_status, changed = _resolve_continuity(
            category=category,
            source_screen_id=source_screen_id,
            source_compiled_screen=source_compiled_screen,
            runtime=runtime,
            require_compiled_source_for_changed=require_compiled_source_for_changed,
        )
    else:
        continuity_status = (
            continuity_status_override
            if continuity_status_override is not None
            else "none"
        )
        changed = changed_override
    if (
        mapped.continuity_status_override is not None
        and next_screen is not None
        and source_screen_id is not None
    ):
        continuity_status = mapped.continuity_status_override
    return build_semantic_failure_result(
        command=command_name,
        category=category,
        code=mapped.code,
        message=mapped.message,
        execution_outcome=_map_failure_execution_outcome(
            error,
            dispatch_attempted=dispatch_attempted,
            truth_lost_after_dispatch=truth_lost_after_dispatch,
        ),
        source_screen_id=source_screen_id,
        current_screen=next_screen,
        artifacts=artifacts,
        continuity_status=continuity_status,
        observation_quality="authoritative" if next_screen is not None else "none",
        changed=changed,
    ).model_dump(by_alias=True, mode="json")


def _map_failure_execution_outcome(
    error: DaemonError,
    *,
    dispatch_attempted: bool = False,
    truth_lost_after_dispatch: bool = False,
) -> str:
    if truth_lost_after_dispatch:
        return "dispatched"
    if dispatch_attempted and error.code == DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED:
        return "dispatched"
    if (
        error.code
        in {
            DaemonErrorCode.DAEMON_BAD_REQUEST,
            DaemonErrorCode.RUNTIME_BUSY,
            DaemonErrorCode.REF_RESOLUTION_FAILED,
            DaemonErrorCode.REF_STALE,
            DaemonErrorCode.TARGET_BLOCKED,
            DaemonErrorCode.DEVICE_AGENT_CAPABILITY_MISMATCH,
            DaemonErrorCode.ACCESSIBILITY_NOT_READY,
        }
        | _AVAILABILITY_CODES
    ):
        return "notAttempted"
    if error.code == DaemonErrorCode.TARGET_NOT_ACTIONABLE:
        return "dispatched" if dispatch_attempted else "notAttempted"
    if error.code in {
        DaemonErrorCode.OPEN_FAILED,
        DaemonErrorCode.ACTION_NOT_CONFIRMED,
        DaemonErrorCode.TYPE_NOT_CONFIRMED,
        DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
    }:
        return "dispatched"
    return "unknown"


def _resolve_continuity(
    *,
    category: str,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    runtime: WorkspaceRuntime,
    require_compiled_source_for_changed: bool = False,
) -> tuple[str, bool | None]:
    if category == "open" or source_screen_id is None:
        return "none", None
    continuity = resolve_runtime_continuity(
        runtime=runtime,
        source_screen_id=source_screen_id,
        source_compiled_screen=source_compiled_screen,
    )
    changed = (
        None
        if require_compiled_source_for_changed and source_compiled_screen is None
        else continuity.changed
    )
    return continuity.continuity_status, changed


class ActionCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        action_executor: ActionExecutor,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._action_executor = action_executor

    def handle_ref_action(
        self,
        *,
        command: RefBoundActionCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        self._runtime_kernel.normalize_stale_ready_runtime(runtime)
        lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
        source_compiled_screen = capture_runtime_source_basis(
            runtime=runtime
        ).source_compiled_screen
        try:
            result = self._action_executor.execute(
                runtime,
                current_command_record(
                    kind=command.kind,
                    result_command=semantic_result_command_for_daemon_kind(
                        command.kind
                    ),
                ),
                command,
                lifecycle_lease,
            )
        except ActionExecutionFailure as failure:
            return _command_error_payload(
                command_name=semantic_result_command_for_daemon_kind(command.kind),
                category="transition",
                source_screen_id=command.source_screen_id,
                source_compiled_screen=source_compiled_screen,
                runtime=runtime,
                error=failure.normalized_error,
                dispatch_attempted=failure.dispatch_attempted,
                truth_lost_after_dispatch=failure.truth_lost_after_dispatch,
            )
        except DaemonError as error:
            return _command_error_payload(
                command_name=semantic_result_command_for_daemon_kind(command.kind),
                category="transition",
                source_screen_id=command.source_screen_id,
                source_compiled_screen=source_compiled_screen,
                runtime=runtime,
                error=error,
            )
        return _command_result_payload(
            command_name=semantic_result_command_for_daemon_kind(command.kind),
            category="transition",
            source_screen_id=command.source_screen_id,
            source_compiled_screen=source_compiled_screen,
            runtime=runtime,
            assembly_input=result,
        )

    def handle_global_action(
        self,
        *,
        command: GlobalCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
        source_basis = resolve_global_action_source_basis(
            runtime=runtime,
            source_screen_id=command.source_screen_id,
        )
        try:
            result = self._action_executor.execute(
                runtime,
                current_command_record(
                    kind=command.kind,
                    result_command=command.action,
                ),
                command,
                lifecycle_lease,
            )
        except ActionExecutionFailure as failure:
            return _command_error_payload(
                command_name=command.action,
                category="transition",
                source_screen_id=source_basis.source_screen_id,
                source_compiled_screen=source_basis.source_compiled_screen,
                runtime=runtime,
                error=failure.normalized_error,
                require_compiled_source_for_changed=True,
                dispatch_attempted=failure.dispatch_attempted,
                truth_lost_after_dispatch=failure.truth_lost_after_dispatch,
            )
        except DaemonError as error:
            return _command_error_payload(
                command_name=command.action,
                category="transition",
                source_screen_id=source_basis.source_screen_id,
                source_compiled_screen=source_basis.source_compiled_screen,
                runtime=runtime,
                error=error,
                require_compiled_source_for_changed=True,
            )
        return _command_result_payload(
            command_name=command.action,
            category="transition",
            source_screen_id=source_basis.source_screen_id,
            source_compiled_screen=source_basis.source_compiled_screen,
            runtime=runtime,
            assembly_input=result,
            require_compiled_source_for_changed=True,
        )

    def handle_open(
        self,
        *,
        command: OpenCommand,
        source_screen_id: str | None = None,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
        source_basis = capture_runtime_source_basis(runtime=runtime)
        source_compiled_screen = source_basis.source_compiled_screen
        if source_screen_id is None:
            if source_basis.source_screen_id is not None:
                source_screen_id = source_basis.source_screen_id
            else:
                current_screen = current_public_screen(runtime)
                source_screen_id = (
                    None if current_screen is None else current_screen.screen_id
                )
                source_compiled_screen = None
        elif source_screen_id != source_basis.source_screen_id:
            source_compiled_screen = None
        try:
            result = self._action_executor.execute(
                runtime,
                current_command_record(
                    kind=command.kind,
                    result_command="open",
                ),
                command,
                lifecycle_lease,
            )
        except ActionExecutionFailure as failure:
            return _command_error_payload(
                command_name="open",
                category="open",
                source_screen_id=source_screen_id,
                source_compiled_screen=source_compiled_screen,
                runtime=runtime,
                error=failure.normalized_error,
                continuity_status_override="none",
                changed_override=None,
                dispatch_attempted=failure.dispatch_attempted,
                truth_lost_after_dispatch=failure.truth_lost_after_dispatch,
            )
        except DaemonError as error:
            return _command_error_payload(
                command_name="open",
                category="open",
                source_screen_id=source_screen_id,
                source_compiled_screen=source_compiled_screen,
                runtime=runtime,
                error=error,
                continuity_status_override="none",
                changed_override=None,
            )
        changed = resolve_open_changed(
            runtime=runtime,
            source_screen_id=source_screen_id,
            source_compiled_screen=source_compiled_screen,
        )
        return _command_result_payload(
            command_name="open",
            category="open",
            source_screen_id=source_screen_id,
            source_compiled_screen=source_compiled_screen,
            runtime=runtime,
            assembly_input=result,
            continuity_status_override="none",
            changed_override=changed,
        )


__all__ = ["ActionCommandHandler"]
