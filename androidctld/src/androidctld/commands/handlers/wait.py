"""Semantic wait command handler."""

from __future__ import annotations

from androidctld.commands.command_models import (
    GoneWaitPredicate,
    ScreenChangeWaitPredicate,
    WaitCommand,
)
from androidctld.commands.orchestration import current_command_record
from androidctld.commands.result_builders import app_payload
from androidctld.commands.result_models import (
    SemanticResultAssemblyInput,
    build_semantic_failure_result,
    build_semantic_success_result,
)
from androidctld.commands.semantic_error_mapping import (
    map_daemon_error_to_semantic_failure,
)
from androidctld.commands.semantic_truth import (
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
from androidctld.waits.evaluators import WaitMatchData
from androidctld.waits.loop import (
    WaitLoopOutcome,
    WaitRuntimeLoop,
)


class WaitCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        wait_runtime_loop: WaitRuntimeLoop,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._wait_runtime_loop = wait_runtime_loop

    def handle_service_wait(
        self,
        *,
        command: WaitCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        source_screen_id = _wait_basis_screen_id(command)
        source_compiled_screen = None
        try:
            source_compiled_screen = _validate_relative_wait_entry(
                runtime=runtime,
                command=command,
            )
            _ensure_wait_runtime_connected(runtime)
            lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
            outcome = self._wait_runtime_loop.run(
                session=runtime,
                record=current_command_record(
                    kind=command.kind,
                    result_command="wait",
                ),
                command=command,
                lifecycle_lease=lifecycle_lease,
            )
            assembly_input = _wait_loop_result(command=command, outcome=outcome)
            continuity_status, changed = _wait_success_overrides(
                command=command,
                runtime=runtime,
            )
            return _wait_success_payload(
                runtime,
                source_screen_id=source_screen_id,
                source_compiled_screen=source_compiled_screen,
                assembly_input=assembly_input,
                continuity_status=continuity_status,
                changed=changed,
            )
        except DaemonError as error:
            return _wait_failure_payload(
                runtime,
                source_screen_id=source_screen_id,
                source_compiled_screen=source_compiled_screen,
                error=error,
            )


def _ensure_wait_runtime_connected(runtime: WorkspaceRuntime) -> None:
    if runtime.connection is None or runtime.device_token is None:
        raise DaemonError(
            code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
            message="runtime is not connected to a device",
            retryable=False,
            details={"workspaceRoot": runtime.workspace_root.as_posix()},
            http_status=200,
        )


def _wait_loop_result(
    *,
    command: WaitCommand,
    outcome: WaitLoopOutcome,
) -> SemanticResultAssemblyInput:
    if isinstance(outcome, WaitMatchData):
        return SemanticResultAssemblyInput(
            app_payload=app_payload(
                outcome.snapshot,
                app_match=outcome.app_match,
            ),
        )
    raise _wait_timeout_error(command)


def _wait_timeout_error(command: WaitCommand) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.WAIT_TIMEOUT,
        message=f"wait {command.wait_kind.value} timed out",
        retryable=True,
        details={"kind": command.kind.value, "waitKind": command.wait_kind.value},
        http_status=200,
    )


def _wait_success_payload(
    runtime: WorkspaceRuntime,
    *,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    assembly_input: SemanticResultAssemblyInput | None = None,
    continuity_status: str | None = None,
    changed: bool | None = None,
) -> dict[str, object]:
    current_screen = current_public_screen(runtime)
    artifacts = current_artifacts(runtime)
    resolved_continuity, resolved_changed = _resolve_wait_continuity(
        runtime=runtime,
        source_screen_id=source_screen_id,
        source_compiled_screen=source_compiled_screen,
        continuity_status=continuity_status,
        changed=changed,
    )
    return build_semantic_success_result(
        command="wait",
        category="wait",
        source_screen_id=source_screen_id,
        next_screen=current_screen,
        app_payload=(None if assembly_input is None else assembly_input.app_payload),
        artifacts=artifacts,
        continuity_status=resolved_continuity,
        execution_outcome="notApplicable",
        changed=resolved_changed,
        warnings=([] if assembly_input is None else list(assembly_input.warnings)),
    ).model_dump(by_alias=True, mode="json")


def _wait_failure_payload(
    runtime: WorkspaceRuntime,
    *,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    error: DaemonError,
) -> dict[str, object]:
    current_screen = current_public_screen(runtime)
    mapped = map_daemon_error_to_semantic_failure(
        command_name="wait",
        error=error,
    )
    if mapped is None:
        raise error
    continuity_status, changed = _resolve_wait_continuity(
        runtime=runtime,
        source_screen_id=source_screen_id,
        source_compiled_screen=source_compiled_screen,
    )
    return build_semantic_failure_result(
        command="wait",
        category="wait",
        code=mapped.code,
        message=mapped.message,
        source_screen_id=source_screen_id,
        current_screen=current_screen,
        artifacts=current_artifacts(runtime),
        continuity_status=continuity_status,
        observation_quality="authoritative" if current_screen is not None else "none",
        changed=changed,
    ).model_dump(by_alias=True, mode="json")


def _resolve_wait_continuity(
    *,
    runtime: WorkspaceRuntime,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    continuity_status: str | None = None,
    changed: bool | None = None,
) -> tuple[str, bool | None]:
    continuity = resolve_runtime_continuity(
        runtime=runtime,
        source_screen_id=source_screen_id,
        source_compiled_screen=source_compiled_screen,
    )
    return (
        (
            continuity.continuity_status
            if continuity_status is None
            else continuity_status
        ),
        continuity.changed if changed is None else changed,
    )


def _wait_success_overrides(
    *,
    command: WaitCommand,
    runtime: WorkspaceRuntime,
) -> tuple[str | None, bool | None]:
    current_screen = current_public_screen(runtime)
    if current_screen is None:
        return None, None
    predicate = command.predicate
    if isinstance(predicate, GoneWaitPredicate):
        return "stale", True
    if (
        isinstance(predicate, ScreenChangeWaitPredicate)
        and current_screen.screen_id != predicate.source_screen_id
    ):
        return None, True
    return None, None


def _wait_basis_screen_id(command: WaitCommand) -> str | None:
    predicate = _relative_wait_predicate(command)
    if predicate is not None:
        return predicate.source_screen_id
    return None


def _relative_wait_predicate(
    command: WaitCommand,
) -> ScreenChangeWaitPredicate | GoneWaitPredicate | None:
    predicate = command.predicate
    if isinstance(predicate, (ScreenChangeWaitPredicate, GoneWaitPredicate)):
        return predicate
    return None


def _validate_relative_wait_entry(
    *,
    runtime: WorkspaceRuntime,
    command: WaitCommand,
) -> CompiledScreen | None:
    predicate = _relative_wait_predicate(command)
    if predicate is None:
        return None
    basis = get_authoritative_current_basis(runtime)
    if basis is None or basis.screen_id != predicate.source_screen_id:
        raise _relative_wait_entry_unavailable(command)
    if (
        isinstance(predicate, GoneWaitPredicate)
        and predicate.ref not in basis.public_refs
    ):
        raise _relative_wait_entry_unavailable(command)
    return basis.compiled_screen


def _relative_wait_entry_unavailable(command: WaitCommand) -> DaemonError:
    details: dict[str, object] = {"waitKind": command.wait_kind.value}
    predicate = _relative_wait_predicate(command)
    if predicate is not None:
        details["sourceScreenId"] = predicate.source_screen_id
    if isinstance(predicate, GoneWaitPredicate):
        details["ref"] = predicate.ref
    return DaemonError(
        code=DaemonErrorCode.SCREEN_NOT_READY,
        message="No current device observation is available.",
        retryable=True,
        details=details,
        http_status=200,
    )
