"""Runtime lifecycle coordination helpers."""

from __future__ import annotations

from dataclasses import dataclass

from androidctld.runtime.models import WorkspaceRuntime


@dataclass(frozen=True)
class RuntimeLifecycleLease:
    revision: int

    def is_current(self, runtime: WorkspaceRuntime) -> bool:
        return self.revision == runtime.lifecycle_revision


def capture_lifecycle_lease(runtime: WorkspaceRuntime) -> RuntimeLifecycleLease:
    return RuntimeLifecycleLease(revision=runtime.lifecycle_revision)
