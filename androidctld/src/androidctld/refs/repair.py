"""Internal ref repair decisions and diagnostic conversion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.screen_lookup import lookup_source_screen_artifact
from androidctld.commands.results import screen_summary
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import RefBinding, RefRepairSourceSignature
from androidctld.refs.service import (
    repair_source_signature_to_current_snapshot,
    source_signature_from_artifact_payload,
    source_signature_from_binding,
)
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import current_compiled_screen
from androidctld.schema.base import dump_api_model
from androidctld.semantics.public_models import PublicScreen


class RepairDecisionStatus(str, Enum):
    RESOLVED = "resolved"
    LIVE_REF_MISSING = "live_ref_missing"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID_ARTIFACT = "invalid_artifact"
    REPAIR_FAILED = "repair_failed"


_DIAGNOSTIC_SOURCE_ARTIFACT_STATUSES = {
    RepairDecisionStatus.SOURCE_UNAVAILABLE,
    RepairDecisionStatus.INVALID_ARTIFACT,
    RepairDecisionStatus.REPAIR_FAILED,
}


@dataclass(frozen=True)
class RepairDecision:
    ref: str | None
    source_screen_id: str | None
    status: RepairDecisionStatus
    binding: RefBinding | None = None
    source_signature: RefRepairSourceSignature | None = None

    @property
    def is_resolved(self) -> bool:
        return self.status == RepairDecisionStatus.RESOLVED

    @property
    def diagnostic_source_artifact_status(self) -> str | None:
        if self.status not in _DIAGNOSTIC_SOURCE_ARTIFACT_STATUSES:
            return None
        return self.status.value


def resolved_repair_decision(
    *,
    ref: str,
    source_screen_id: str,
    binding: RefBinding,
    source_signature: RefRepairSourceSignature | None = None,
) -> RepairDecision:
    return RepairDecision(
        ref=ref,
        source_screen_id=source_screen_id,
        status=RepairDecisionStatus.RESOLVED,
        binding=binding,
        source_signature=source_signature,
    )


def resolved_source_signature_decision(
    *,
    ref: str,
    source_screen_id: str,
    source_signature: RefRepairSourceSignature,
) -> RepairDecision:
    return RepairDecision(
        ref=ref,
        source_screen_id=source_screen_id,
        status=RepairDecisionStatus.RESOLVED,
        source_signature=source_signature,
    )


def failed_repair_decision(
    *,
    ref: str | None,
    source_screen_id: str | None,
) -> RepairDecision:
    return RepairDecision(
        ref=ref,
        source_screen_id=source_screen_id,
        status=RepairDecisionStatus.REPAIR_FAILED,
    )


def resolve_ref_decision(
    session: WorkspaceRuntime,
    ref: str,
    source_screen_id: str,
) -> RepairDecision:
    source_decision = resolve_source_binding_decision(
        session,
        ref,
        source_screen_id,
    )
    if not source_decision.is_resolved:
        return source_decision
    if source_screen_id == session.current_screen_id:
        return source_decision
    source_signature = source_decision.source_signature
    assert source_signature is not None
    return repair_source_signature_decision(
        session,
        source_signature,
        source_screen_id=source_screen_id,
    )


def resolve_source_binding_decision(
    session: WorkspaceRuntime,
    ref: str,
    source_screen_id: str,
) -> RepairDecision:
    if source_screen_id == session.current_screen_id:
        binding = session.ref_registry.get(ref)
        if binding is None:
            return RepairDecision(
                ref=ref,
                source_screen_id=source_screen_id,
                status=RepairDecisionStatus.LIVE_REF_MISSING,
            )
        return resolved_repair_decision(
            ref=ref,
            source_screen_id=source_screen_id,
            binding=binding,
            source_signature=source_signature_from_binding(binding),
        )

    return load_source_artifact_binding_decision(session, ref, source_screen_id)


def load_source_artifact_binding_decision(
    session: WorkspaceRuntime,
    ref: str,
    source_screen_id: str,
) -> RepairDecision:
    source_screen_lookup = lookup_source_screen_artifact(session, source_screen_id)
    if source_screen_lookup.status == "not_found":
        return RepairDecision(
            ref=ref,
            source_screen_id=source_screen_id,
            status=RepairDecisionStatus.SOURCE_UNAVAILABLE,
        )
    if source_screen_lookup.status == "invalid_artifact":
        return RepairDecision(
            ref=ref,
            source_screen_id=source_screen_id,
            status=RepairDecisionStatus.INVALID_ARTIFACT,
        )
    payload = source_screen_lookup.payload
    if payload is None or payload.screen_id != source_screen_id:
        return RepairDecision(
            ref=ref,
            source_screen_id=source_screen_id,
            status=RepairDecisionStatus.INVALID_ARTIFACT,
        )
    binding_payload = payload.repair_bindings.get(ref)
    if binding_payload is None:
        return RepairDecision(
            ref=ref,
            source_screen_id=source_screen_id,
            status=RepairDecisionStatus.INVALID_ARTIFACT,
        )
    return resolved_source_signature_decision(
        ref=ref,
        source_screen_id=source_screen_id,
        source_signature=source_signature_from_artifact_payload(ref, binding_payload),
    )


def repair_source_signature_decision(
    session: WorkspaceRuntime,
    source_signature: RefRepairSourceSignature,
    *,
    source_screen_id: str,
) -> RepairDecision:
    compiled_screen = current_compiled_screen(session)
    if compiled_screen is None or session.latest_snapshot is None:
        return failed_repair_decision(
            ref=source_signature.ref,
            source_screen_id=source_screen_id,
        )
    repaired_binding = repair_source_signature_to_current_snapshot(
        source_signature,
        compiled_screen=compiled_screen,
        snapshot_id=session.latest_snapshot.snapshot_id,
    )
    if repaired_binding is None:
        return failed_repair_decision(
            ref=source_signature.ref,
            source_screen_id=source_screen_id,
        )
    return resolved_repair_decision(
        ref=source_signature.ref,
        source_screen_id=source_screen_id,
        binding=repaired_binding,
    )


def ref_repair_error(
    decision: RepairDecision,
    public_screen: PublicScreen | None = None,
    artifacts: ScreenArtifacts | None = None,
) -> DaemonError:
    if decision.status == RepairDecisionStatus.LIVE_REF_MISSING:
        return DaemonError(
            code=DaemonErrorCode.REF_RESOLUTION_FAILED,
            message="ref does not exist on the current screen",
            retryable=False,
            details={"ref": decision.ref},
            http_status=200,
        )
    return ref_stale_error(
        decision.ref,
        public_screen=public_screen,
        artifacts=artifacts,
        source_screen_id=decision.source_screen_id,
        source_artifact_status=decision.diagnostic_source_artifact_status,
    )


def ref_stale_error(
    ref: str | None,
    public_screen: PublicScreen | None = None,
    artifacts: ScreenArtifacts | None = None,
    *,
    source_screen_id: str | None = None,
    source_artifact_status: str | RepairDecisionStatus | None = None,
) -> DaemonError:
    artifact_payload = artifacts or ScreenArtifacts(screen_json=None)
    normalized_status = _normalize_source_artifact_status(source_artifact_status)
    details: dict[str, Any] = {
        "ref": ref,
        "screen": (
            screen_summary(
                public_screen,
                artifact_payload,
            )
            if public_screen is not None
            else None
        ),
        "artifacts": dump_api_model(artifact_payload),
    }
    if source_screen_id is not None:
        details["sourceScreenId"] = source_screen_id
    if normalized_status is not None:
        details["sourceArtifactStatus"] = normalized_status
    return DaemonError(
        code=DaemonErrorCode.REF_STALE,
        message="ref could not be repaired",
        retryable=normalized_status in (None, RepairDecisionStatus.REPAIR_FAILED.value),
        details=details,
        http_status=200,
    )


def _normalize_source_artifact_status(
    source_artifact_status: str | RepairDecisionStatus | None,
) -> str | None:
    if source_artifact_status is None:
        return None
    if isinstance(source_artifact_status, RepairDecisionStatus):
        return source_artifact_status.value
    if source_artifact_status in {
        status.value for status in _DIAGNOSTIC_SOURCE_ARTIFACT_STATUSES
    }:
        return source_artifact_status
    raise ValueError(f"unknown sourceArtifactStatus: {source_artifact_status}")
