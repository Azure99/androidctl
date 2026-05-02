"""Payload builders for persisted screen artifacts."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from androidctld.refs.models import (
    RefBinding,
    RefFingerprint,
    RefRegistry,
    SemanticProfile,
)
from androidctld.schema import ApiModel
from androidctld.semantics.public_models import PublicGroup, PublicScreen


class RepairFingerprintPayload(ApiModel):
    role: str
    normalized_label: str
    resource_id: str
    class_name: str
    parent_role: str
    parent_label: str
    sibling_labels: tuple[str, ...] = Field(default_factory=tuple)
    relative_bounds: tuple[int, int, int, int]


class RepairSemanticProfilePayload(ApiModel):
    state: tuple[str, ...] = Field(default_factory=tuple)
    actions: tuple[str, ...] = Field(default_factory=tuple)


class RepairBindingPayload(ApiModel):
    fingerprint: RepairFingerprintPayload
    semantic_profile: RepairSemanticProfilePayload


class ScreenArtifactPayload(ApiModel):
    model_config = ConfigDict(extra="forbid")

    screen_id: str
    sequence: int
    source_snapshot_id: int
    captured_at: str
    package_name: str | None
    activity_name: str | None
    keyboard_visible: bool
    groups: tuple[PublicGroup, ...]
    repair_bindings: dict[str, RepairBindingPayload] = Field(default_factory=dict)


def build_repair_fingerprint_payload(
    fingerprint: RefFingerprint,
) -> RepairFingerprintPayload:
    return RepairFingerprintPayload(
        role=fingerprint.role,
        normalized_label=fingerprint.normalized_label,
        resource_id=fingerprint.resource_id,
        class_name=fingerprint.class_name,
        parent_role=fingerprint.parent_role,
        parent_label=fingerprint.parent_label,
        sibling_labels=fingerprint.sibling_labels,
        relative_bounds=fingerprint.relative_bounds,
    )


def build_repair_semantic_profile_payload(
    semantic_profile: SemanticProfile,
) -> RepairSemanticProfilePayload:
    return RepairSemanticProfilePayload(
        state=semantic_profile.state,
        actions=semantic_profile.actions,
    )


def build_repair_binding_payload(binding: RefBinding) -> RepairBindingPayload:
    return RepairBindingPayload(
        fingerprint=build_repair_fingerprint_payload(binding.fingerprint),
        semantic_profile=build_repair_semantic_profile_payload(
            binding.semantic_profile
        ),
    )


def build_screen_artifact_payload(
    public_screen: PublicScreen,
    ref_registry: RefRegistry,
    *,
    sequence: int,
    source_snapshot_id: int,
    captured_at: str,
) -> dict[str, Any]:
    payload = ScreenArtifactPayload(
        screen_id=public_screen.screen_id,
        sequence=sequence,
        source_snapshot_id=source_snapshot_id,
        captured_at=captured_at,
        package_name=public_screen.app.package_name,
        activity_name=public_screen.app.activity_name,
        keyboard_visible=public_screen.surface.keyboard_visible,
        groups=public_screen.groups,
        repair_bindings={
            ref: build_repair_binding_payload(binding)
            for ref, binding in ref_registry.bindings.items()
        },
    )
    return payload.model_dump(by_alias=True, mode="json")
