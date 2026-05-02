"""Shared semantic continuity truth helpers."""

from __future__ import annotations

from dataclasses import dataclass

from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    get_authoritative_current_basis,
)
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.continuity import evaluate_continuity
from androidctld.semantics.public_models import PublicScreen


@dataclass(frozen=True)
class SemanticContinuityTruth:
    continuity_status: str
    changed: bool | None


@dataclass(frozen=True)
class SemanticSourceBasis:
    source_screen_id: str | None
    source_compiled_screen: CompiledScreen | None


def capture_runtime_source_basis(
    *,
    runtime: WorkspaceRuntime,
) -> SemanticSourceBasis:
    basis = get_authoritative_current_basis(runtime)
    return SemanticSourceBasis(
        source_screen_id=(None if basis is None else basis.screen_id),
        source_compiled_screen=(None if basis is None else basis.compiled_screen),
    )


def resolve_global_action_source_basis(
    *,
    runtime: WorkspaceRuntime,
    source_screen_id: str | None,
) -> SemanticSourceBasis:
    basis = get_authoritative_current_basis(runtime)
    if source_screen_id is None:
        return SemanticSourceBasis(
            source_screen_id=(None if basis is None else basis.screen_id),
            source_compiled_screen=(None if basis is None else basis.compiled_screen),
        )
    if basis is not None and basis.screen_id == source_screen_id:
        return SemanticSourceBasis(
            source_screen_id=source_screen_id,
            source_compiled_screen=basis.compiled_screen,
        )
    return SemanticSourceBasis(
        source_screen_id=source_screen_id,
        source_compiled_screen=None,
    )


def resolve_screen_continuity(
    *,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
    current_screen: PublicScreen | None,
    candidate_compiled_screen: CompiledScreen | None,
) -> SemanticContinuityTruth:
    if source_screen_id is None or current_screen is None:
        return SemanticContinuityTruth("none", None)
    if (
        source_compiled_screen is not None
        and source_compiled_screen.screen_id == source_screen_id
        and candidate_compiled_screen is not None
    ):
        decision = evaluate_continuity(
            source_screen=source_compiled_screen,
            candidate_screen=candidate_compiled_screen,
        )
        return SemanticContinuityTruth(
            continuity_status=decision.continuity_status,
            changed=decision.changed,
        )
    continuity_status = (
        "stable" if source_screen_id == current_screen.screen_id else "stale"
    )
    return SemanticContinuityTruth(
        continuity_status=continuity_status,
        changed=continuity_status == "stale",
    )


def resolve_runtime_continuity(
    *,
    runtime: WorkspaceRuntime,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
) -> SemanticContinuityTruth:
    basis = get_authoritative_current_basis(runtime)
    return resolve_screen_continuity(
        source_screen_id=source_screen_id,
        source_compiled_screen=source_compiled_screen,
        current_screen=(None if basis is None else basis.public_screen),
        candidate_compiled_screen=(None if basis is None else basis.compiled_screen),
    )


def resolve_open_changed(
    *,
    runtime: WorkspaceRuntime,
    source_screen_id: str | None,
    source_compiled_screen: CompiledScreen | None,
) -> bool | None:
    if source_screen_id is None or source_compiled_screen is None:
        return None
    if source_compiled_screen.screen_id != source_screen_id:
        return None
    basis = get_authoritative_current_basis(runtime)
    if basis is None:
        return None
    decision = evaluate_continuity(
        source_screen=source_compiled_screen,
        candidate_screen=basis.compiled_screen,
    )
    return decision.changed


__all__ = [
    "SemanticContinuityTruth",
    "SemanticSourceBasis",
    "capture_runtime_source_basis",
    "resolve_global_action_source_basis",
    "resolve_open_changed",
    "resolve_runtime_continuity",
    "resolve_screen_continuity",
]
