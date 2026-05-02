"""Semantic screen continuity decisions."""

from __future__ import annotations

from dataclasses import dataclass

from androidctld.refs.service import best_candidate_for_binding, binding_for_candidate
from androidctld.semantics.compiler import CompiledScreen


@dataclass(frozen=True)
class ContinuityDecision:
    next_screen_id: str
    continuity_status: str
    changed: bool | None
    code: str | None = None

    @classmethod
    def bootstrap(cls, candidate_screen: CompiledScreen) -> ContinuityDecision:
        return cls(
            next_screen_id=candidate_screen.screen_id,
            continuity_status="none",
            changed=None,
        )

    @classmethod
    def stable(
        cls,
        *,
        next_screen_id: str,
        changed: bool,
    ) -> ContinuityDecision:
        return cls(
            next_screen_id=next_screen_id,
            continuity_status="stable",
            changed=changed,
        )

    @classmethod
    def stale(
        cls,
        *,
        next_screen_id: str,
        changed: bool,
        code: str,
    ) -> ContinuityDecision:
        return cls(
            next_screen_id=next_screen_id,
            continuity_status="stale",
            changed=changed,
            code=code,
        )


def evaluate_continuity(
    *,
    source_screen: CompiledScreen | None,
    candidate_screen: CompiledScreen,
) -> ContinuityDecision:
    if source_screen is None:
        return ContinuityDecision.bootstrap(candidate_screen)
    if (
        candidate_screen.action_surface_fingerprint
        == source_screen.action_surface_fingerprint
    ):
        return ContinuityDecision.stable(
            next_screen_id=source_screen.screen_id,
            changed=False,
        )
    if _strict_repair_succeeds(
        source_screen=source_screen,
        candidate_screen=candidate_screen,
    ):
        return ContinuityDecision.stable(
            next_screen_id=candidate_screen.screen_id,
            changed=True,
        )
    return ContinuityDecision.stale(
        next_screen_id=candidate_screen.screen_id,
        changed=True,
        code="REF_STALE",
    )


def _strict_repair_succeeds(
    *,
    source_screen: CompiledScreen,
    candidate_screen: CompiledScreen,
) -> bool:
    source_candidates = [
        candidate for candidate in source_screen.ref_candidates() if candidate.ref
    ]
    if not source_candidates:
        return False
    remaining_candidates = list(candidate_screen.ref_candidates())
    for source_candidate in source_candidates:
        binding = binding_for_candidate(
            ref=source_candidate.ref,
            candidate=source_candidate,
            snapshot_id=0,
            reused=True,
        )
        match = best_candidate_for_binding(binding, remaining_candidates)
        if match is None:
            return False
        remaining_candidates.remove(match[0])
    return True
