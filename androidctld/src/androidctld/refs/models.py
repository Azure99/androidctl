"""Runtime ref registry models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RefFingerprint:
    role: str
    normalized_label: str
    resource_id: str
    class_name: str
    parent_role: str
    parent_label: str
    sibling_labels: tuple[str, ...]
    relative_bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class NodeHandle:
    snapshot_id: int
    rid: str


@dataclass(frozen=True)
class SemanticProfile:
    state: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class RefRepairSourceSignature:
    ref: str
    fingerprint: RefFingerprint
    state: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass
class RefBinding:
    ref: str
    handle: NodeHandle
    fingerprint: RefFingerprint
    semantic_profile: SemanticProfile
    reused: bool = False


@dataclass
class RefRegistry:
    bindings: dict[str, RefBinding] = field(default_factory=dict)

    def get(self, ref: str) -> RefBinding | None:
        return self.bindings.get(ref)
