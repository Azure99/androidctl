"""Runtime state persistence repository."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.schema.persistence import (
    RUNTIME_STATE_SCHEMA_VERSION,
    RuntimeStateFile,
    build_persistence_model,
    validate_persistence_payload,
)
from androidctld.schema.persistence_io import atomic_write_json, load_json_object


class RuntimeStateRepository:
    def load(self, runtime_path: Path) -> WorkspaceRuntime | None:
        if not runtime_path.exists():
            return None
        state_payload = load_json_object(runtime_path)
        parsed = validate_persistence_payload(
            RuntimeStateFile,
            state_payload,
            field_name="runtime",
            schema_version=RUNTIME_STATE_SCHEMA_VERSION,
        )
        artifact_root = runtime_path.parent.resolve()
        workspace_root = artifact_root.parent.resolve()
        return WorkspaceRuntime(
            workspace_root=workspace_root,
            artifact_root=artifact_root,
            runtime_path=runtime_path.resolve(),
            status=parsed.status,
            screen_sequence=parsed.screen_sequence,
            current_screen_id=None,
        )

    def persist(
        self,
        runtime: WorkspaceRuntime,
        *,
        runtime_path: Path | None = None,
    ) -> None:
        target_runtime_path = runtime_path or runtime.runtime_path
        state_payload = build_persistence_model(
            RuntimeStateFile,
            status=_status_for_persisted_runtime(runtime.status),
            screen_sequence=runtime.screen_sequence,
            updated_at=now_isoformat(),
        ).model_dump(by_alias=True, mode="json")
        state_payload["schemaVersion"] = RUNTIME_STATE_SCHEMA_VERSION
        atomic_write_json(target_runtime_path, state_payload)


def now_isoformat() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _status_for_persisted_runtime(status: RuntimeStatus) -> RuntimeStatus:
    if status is RuntimeStatus.READY:
        return RuntimeStatus.BROKEN
    return status
