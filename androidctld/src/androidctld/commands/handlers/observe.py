"""Semantic observe command handler."""

from __future__ import annotations

from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.commands.command_models import ObserveCommand
from androidctld.commands.result_models import (
    build_semantic_failure_result,
    build_semantic_success_result,
)
from androidctld.commands.semantic_error_mapping import (
    map_daemon_error_to_semantic_failure,
)
from androidctld.commands.semantic_truth import resolve_screen_continuity
from androidctld.errors import DaemonError
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.screen_state import (
    current_compiled_screen,
    get_authoritative_current_basis,
)
from androidctld.snapshots.refresh import ScreenRefreshService
from androidctld.snapshots.service import SnapshotService


class ObserveCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        snapshot_service: SnapshotService,
        screen_refresh: ScreenRefreshService,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._snapshot_service = snapshot_service
        self._screen_refresh = screen_refresh

    def handle(
        self,
        *,
        command: ObserveCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        source_basis = get_authoritative_current_basis(runtime)
        if runtime.connection is None or runtime.device_token is None:
            return build_semantic_failure_result(
                command="observe",
                category="observe",
                code=SemanticResultCode.DEVICE_UNAVAILABLE,
                message="No current device observation is available.",
                source_screen_id=None,
                current_screen=(
                    None if source_basis is None else source_basis.public_screen
                ),
                artifacts=None if source_basis is None else source_basis.artifacts,
            ).model_dump(by_alias=True, mode="json")
        lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
        self._runtime_kernel.acquire_progress_lane(
            runtime,
            occupant_kind=command.kind.value,
        )
        try:
            snapshot = self._snapshot_service.fetch(
                runtime,
                force_refresh=True,
                lifecycle_lease=lifecycle_lease,
            )
            previous_screen = (
                None if source_basis is None else source_basis.public_screen
            )
            previous_compiled = (
                None if source_basis is None else source_basis.compiled_screen
            )
            snapshot, public_screen, artifacts = self._screen_refresh.refresh(
                runtime,
                snapshot,
                lifecycle_lease=lifecycle_lease,
                command_kind=command.kind,
            )
            source_screen_id = (
                None if previous_screen is None else previous_screen.screen_id
            )
            continuity = resolve_screen_continuity(
                source_screen_id=source_screen_id,
                source_compiled_screen=previous_compiled,
                current_screen=public_screen,
                candidate_compiled_screen=current_compiled_screen(
                    runtime,
                    copy_value=False,
                ),
            )
            return build_semantic_success_result(
                command="observe",
                category="observe",
                source_screen_id=source_screen_id,
                next_screen=public_screen,
                artifacts=artifacts,
                continuity_status=continuity.continuity_status,
                execution_outcome="notApplicable",
                changed=continuity.changed,
            ).model_dump(by_alias=True, mode="json")
        except DaemonError as error:
            mapped = map_daemon_error_to_semantic_failure(
                command_name="observe",
                error=error,
            )
            if mapped is None:
                raise
            return build_semantic_failure_result(
                command="observe",
                category="observe",
                code=mapped.code,
                message=mapped.message,
                source_screen_id=None,
                current_screen=(
                    None if source_basis is None else source_basis.public_screen
                ),
                artifacts=None if source_basis is None else source_basis.artifacts,
            ).model_dump(by_alias=True, mode="json")
        finally:
            self._runtime_kernel.release_progress_lane(runtime)
