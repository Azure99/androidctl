"""Ref registry reconciliation and repair helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from androidctld.artifacts.screen_payloads import RepairBindingPayload
from androidctld.refs.models import (
    NodeHandle,
    RefBinding,
    RefFingerprint,
    RefRegistry,
    RefRepairSourceSignature,
    SemanticProfile,
)
from androidctld.runtime_policy import NON_NUMERIC_REF_SORT_BUCKET
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.text_equivalence import canonical_text_key

HIGH_CONFIDENCE_BUCKET = 2
MEDIUM_CONFIDENCE_BUCKET = 1
STRONG_BOUNDS_DISTANCE = 8
GAP_THRESHOLD_ANCHORED = 2
GAP_THRESHOLD_CONTEXTUAL = 3


@dataclass(frozen=True)
class RepairEvidence:
    label_match: bool
    resource_match: bool
    class_match: bool
    parent_match: bool
    sibling_overlap: int
    state_overlap: int
    actions_overlap: int
    bounds_distance: int

    @property
    def identity_anchor_count(self) -> int:
        return int(self.label_match) + int(self.resource_match)

    @property
    def semantic_signal_count(self) -> int:
        return (
            int(self.sibling_overlap > 0)
            + int(self.state_overlap > 0)
            + int(self.actions_overlap > 0)
            + int(self.strong_bounds_match)
        )

    @property
    def corroboration_count(self) -> int:
        return (
            int(self.class_match)
            + int(self.parent_match)
            + int(self.sibling_overlap > 0)
            + int(self.state_overlap > 0)
            + int(self.actions_overlap > 0)
            + int(self.strong_bounds_match)
        )

    @property
    def strong_bounds_match(self) -> bool:
        return self.bounds_distance <= STRONG_BOUNDS_DISTANCE

    @property
    def contextual_anchor(self) -> bool:
        return self.class_match and self.parent_match and self.semantic_signal_count > 0


@dataclass(frozen=True)
class RepairConfidence:
    bucket: int
    score: int
    evidence: RepairEvidence

    @property
    def sort_key(self) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            self.bucket,
            self.score,
            self.evidence.identity_anchor_count,
            self.evidence.corroboration_count,
            self.evidence.sibling_overlap,
            self.evidence.state_overlap,
            self.evidence.actions_overlap,
            -self.evidence.bounds_distance,
        )

    @property
    def is_high_confidence(self) -> bool:
        return self.bucket >= HIGH_CONFIDENCE_BUCKET


@dataclass(frozen=True)
class RefReconcileResult:
    registry: RefRegistry
    compiled_screen: CompiledScreen


class RefRegistryBuilder:
    def finalize_compiled_screen(
        self,
        *,
        compiled_screen: CompiledScreen,
        snapshot_id: int,
        previous_registry: RefRegistry | None,
    ) -> RefReconcileResult:
        finalized_compiled_screen = deepcopy(compiled_screen)
        registry = self.reconcile(
            compiled_screen=finalized_compiled_screen,
            snapshot_id=snapshot_id,
            previous_registry=previous_registry,
        )
        return RefReconcileResult(
            registry=registry,
            compiled_screen=finalized_compiled_screen,
        )

    def reconcile(
        self,
        compiled_screen: CompiledScreen,
        snapshot_id: int,
        previous_registry: RefRegistry | None,
    ) -> RefRegistry:
        clear_candidate_refs(compiled_screen)
        registry = RefRegistry()
        candidates = list(compiled_screen.ref_candidates())
        remaining_candidates = list(candidates)
        used_refs = set()

        if previous_registry is not None:
            for binding in sorted(
                previous_registry.bindings.values(),
                key=lambda item: ref_sort_key(item.ref),
            ):
                match = best_candidate_for_binding(binding, remaining_candidates)
                if match is None:
                    continue
                candidate, _ = match
                candidate.ref = binding.ref
                used_refs.add(binding.ref)
                registry.bindings[binding.ref] = binding_for_candidate(
                    ref=binding.ref,
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    reused=True,
                )
                remaining_candidates.remove(candidate)

        next_index = 1
        for candidate in candidates:
            if candidate.ref:
                continue
            while f"n{next_index}" in used_refs:
                next_index += 1
            ref = f"n{next_index}"
            candidate.ref = ref
            used_refs.add(ref)
            registry.bindings[ref] = binding_for_candidate(
                ref=ref,
                candidate=candidate,
                snapshot_id=snapshot_id,
                reused=False,
            )
        return registry


def clear_candidate_refs(compiled_screen: CompiledScreen) -> None:
    for candidate in compiled_screen.ref_candidates():
        candidate.ref = ""


def binding_for_candidate(
    ref: str,
    candidate: SemanticNode,
    snapshot_id: int,
    reused: bool,
) -> RefBinding:
    return RefBinding(
        ref=ref,
        handle=NodeHandle(
            snapshot_id=snapshot_id,
            rid=candidate.raw_rid,
        ),
        fingerprint=fingerprint_for_candidate(candidate),
        semantic_profile=SemanticProfile(
            state=tuple(candidate.state),
            actions=tuple(candidate.actions),
        ),
        reused=reused,
    )


def fingerprint_for_candidate(candidate: SemanticNode) -> RefFingerprint:
    return RefFingerprint(
        role=candidate.role,
        normalized_label=canonical_text_key(candidate.label),
        resource_id=canonical_text_key(candidate.resource_id),
        class_name=canonical_text_key(candidate.class_name),
        parent_role=canonical_text_key(candidate.parent_role),
        parent_label=canonical_text_key(candidate.parent_label),
        sibling_labels=tuple(
            canonical_text_key(label)
            for label in candidate.sibling_labels
            if canonical_text_key(label)
        ),
        relative_bounds=candidate.relative_bounds,
    )


def best_candidate_for_binding(
    binding: RefBinding, candidates: Sequence[SemanticNode]
) -> tuple[SemanticNode, RepairConfidence] | None:
    return best_candidate_for_source_signature(
        source_signature_from_binding(binding),
        candidates,
    )


def best_candidate_for_source_signature(
    source: RefRepairSourceSignature,
    candidates: Sequence[SemanticNode],
) -> tuple[SemanticNode, RepairConfidence] | None:
    scored: list[tuple[RepairConfidence, str, SemanticNode]] = []
    for candidate in candidates:
        confidence = candidate_match_confidence(source, candidate)
        if confidence is None:
            continue
        scored.append((confidence, candidate.raw_rid, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0].sort_key, item[1]), reverse=True)
    best_confidence, _, best_candidate = scored[0]
    if not best_confidence.is_high_confidence:
        return None
    if len(scored) > 1 and repair_gap_too_small(best_confidence, scored[1][0]):
        return None
    return best_candidate, best_confidence


def candidate_match_confidence(
    source: RefRepairSourceSignature, candidate: SemanticNode
) -> RepairConfidence | None:
    fingerprint = source.fingerprint
    candidate_fingerprint = fingerprint_for_candidate(candidate)
    if candidate_fingerprint.role != fingerprint.role:
        return None

    label_match = (
        candidate_fingerprint.normalized_label == fingerprint.normalized_label
        and bool(fingerprint.normalized_label)
    )
    resource_match = (
        candidate_fingerprint.resource_id == fingerprint.resource_id
        and bool(fingerprint.resource_id)
    )
    class_match = candidate_fingerprint.class_name == fingerprint.class_name and bool(
        fingerprint.class_name
    )
    parent_match = (
        candidate_fingerprint.parent_role == fingerprint.parent_role
        and candidate_fingerprint.parent_label == fingerprint.parent_label
        and bool(fingerprint.parent_role or fingerprint.parent_label)
    )
    sibling_overlap = len(
        set(candidate_fingerprint.sibling_labels).intersection(
            fingerprint.sibling_labels
        )
    )
    bounds_distance = bounds_distance_score(
        candidate_fingerprint.relative_bounds, fingerprint.relative_bounds
    )
    state_overlap = set_overlap(
        source.state,
        candidate.state,
    )
    actions_overlap = set_overlap(
        source.actions,
        candidate.actions,
    )
    evidence = RepairEvidence(
        label_match=label_match,
        resource_match=resource_match,
        class_match=class_match,
        parent_match=parent_match,
        sibling_overlap=sibling_overlap,
        state_overlap=state_overlap,
        actions_overlap=actions_overlap,
        bounds_distance=bounds_distance,
    )
    if not (evidence.identity_anchor_count or evidence.contextual_anchor):
        return None

    bucket = confidence_bucket(evidence)
    if bucket is None:
        return None
    return RepairConfidence(
        bucket=bucket,
        score=confidence_score(evidence),
        evidence=evidence,
    )


def source_signature_from_binding(binding: RefBinding) -> RefRepairSourceSignature:
    return RefRepairSourceSignature(
        ref=binding.ref,
        fingerprint=binding.fingerprint,
        state=binding.semantic_profile.state,
        actions=binding.semantic_profile.actions,
    )


def source_signature_from_artifact_payload(
    ref: str,
    payload: RepairBindingPayload,
) -> RefRepairSourceSignature:
    return RefRepairSourceSignature(
        ref=ref,
        fingerprint=RefFingerprint(
            role=payload.fingerprint.role,
            normalized_label=payload.fingerprint.normalized_label,
            resource_id=payload.fingerprint.resource_id,
            class_name=payload.fingerprint.class_name,
            parent_role=payload.fingerprint.parent_role,
            parent_label=payload.fingerprint.parent_label,
            sibling_labels=payload.fingerprint.sibling_labels,
            relative_bounds=payload.fingerprint.relative_bounds,
        ),
        state=payload.semantic_profile.state,
        actions=payload.semantic_profile.actions,
    )


def repair_source_signature_to_current_snapshot(
    source: RefRepairSourceSignature,
    *,
    compiled_screen: CompiledScreen,
    snapshot_id: int,
) -> RefBinding | None:
    match = best_candidate_for_source_signature(
        source,
        compiled_screen.ref_candidates(),
    )
    if match is None:
        return None
    candidate, _ = match
    return binding_for_candidate(
        ref=source.ref,
        candidate=candidate,
        snapshot_id=snapshot_id,
        reused=True,
    )


def bounds_distance_score(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> int:
    return sum(abs(a - b) for a, b in zip(left, right, strict=True))


def set_overlap(left: Sequence[str], right: Sequence[str]) -> int:
    normalized_left = {
        canonical_text_key(value) for value in left if canonical_text_key(value)
    }
    normalized_right = {
        canonical_text_key(value) for value in right if canonical_text_key(value)
    }
    return len(normalized_left.intersection(normalized_right))


def confidence_bucket(evidence: RepairEvidence) -> int | None:
    if evidence.identity_anchor_count >= 2:
        return HIGH_CONFIDENCE_BUCKET
    if evidence.identity_anchor_count == 1:
        if evidence.semantic_signal_count >= 1 or evidence.corroboration_count >= 2:
            return HIGH_CONFIDENCE_BUCKET
        return MEDIUM_CONFIDENCE_BUCKET
    if evidence.contextual_anchor:
        if evidence.semantic_signal_count >= 2 and evidence.corroboration_count >= 4:
            return HIGH_CONFIDENCE_BUCKET
        return MEDIUM_CONFIDENCE_BUCKET
    return None


def confidence_score(evidence: RepairEvidence) -> int:
    return (
        evidence.identity_anchor_count * 8
        + int(evidence.class_match) * 3
        + int(evidence.parent_match) * 3
        + min(evidence.sibling_overlap, 2) * 2
        + min(evidence.state_overlap, 2) * 2
        + min(evidence.actions_overlap, 2) * 2
        + int(evidence.strong_bounds_match) * 2
    )


def repair_gap_too_small(
    best_confidence: RepairConfidence,
    runner_up: RepairConfidence,
) -> bool:
    if runner_up.bucket != best_confidence.bucket:
        return False
    gap_threshold = (
        GAP_THRESHOLD_ANCHORED
        if best_confidence.evidence.identity_anchor_count > 0
        else GAP_THRESHOLD_CONTEXTUAL
    )
    return (best_confidence.score - runner_up.score) < gap_threshold


_REF_RE = re.compile(r"^n(\d+)$")


def ref_sort_key(ref: str) -> tuple[int, str]:
    match = _REF_RE.match(ref)
    if match is None:
        return (NON_NUMERIC_REF_SORT_BUCKET, ref)
    return (int(match.group(1)), ref)
