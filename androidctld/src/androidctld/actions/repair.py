"""Ref repair for ref-based mutating actions."""

from __future__ import annotations

from androidctld.actions.request_builder import (
    build_action_request_for_binding,
    ensure_screen_ready,
)
from androidctld.commands.command_models import RefBoundActionCommand
from androidctld.commands.models import CommandRecord
from androidctld.device.action_models import BuiltDeviceActionRequest
from androidctld.refs.models import NodeHandle
from androidctld.refs.repair import (
    ref_repair_error,
    repair_source_signature_decision,
    resolve_source_binding_decision,
)
from androidctld.runtime import RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import current_artifacts, current_public_screen
from androidctld.snapshots.refresh import ScreenRefreshService
from androidctld.snapshots.service import SnapshotService


class ActionCommandRepairer:
    def __init__(
        self,
        snapshot_service: SnapshotService,
        screen_refresh: ScreenRefreshService,
    ) -> None:
        self._snapshot_service = snapshot_service
        self._screen_refresh = screen_refresh

    def repair_action_command(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> BuiltDeviceActionRequest:
        handle = self.repair_action_binding(
            session,
            record,
            command,
            lifecycle_lease=lifecycle_lease,
        )
        return build_action_request_for_binding(handle, command)

    def repair_action_binding(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> NodeHandle:
        ensure_screen_ready(session)
        ref = command.ref
        source_decision = resolve_source_binding_decision(
            session,
            ref,
            command.source_screen_id,
        )
        if not source_decision.is_resolved:
            raise ref_repair_error(
                source_decision,
                public_screen=current_public_screen(session),
                artifacts=current_artifacts(session),
            )
        source_signature = source_decision.source_signature
        if source_signature is None:
            raise RuntimeError("resolved source binding is missing its signature")

        snapshot = self._snapshot_service.fetch(
            session,
            force_refresh=True,
            lifecycle_lease=lifecycle_lease,
        )
        _, public_screen, artifacts = self._screen_refresh.refresh(
            session,
            snapshot,
            lifecycle_lease=lifecycle_lease,
            command_kind=command.kind,
            record=record,
        )
        repair_decision = repair_source_signature_decision(
            session,
            source_signature,
            source_screen_id=command.source_screen_id,
        )
        if not repair_decision.is_resolved:
            raise ref_repair_error(
                repair_decision,
                public_screen=public_screen,
                artifacts=artifacts or current_artifacts(session),
            )
        repaired_binding = repair_decision.binding
        if repaired_binding is None:
            raise RuntimeError("resolved repair decision is missing its binding")
        return repaired_binding.handle
