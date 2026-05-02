"""Shared accessors for current runtime screen state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import (
    PublicScreen,
    iter_public_nodes,
)


@dataclass(frozen=True)
class AuthoritativeCurrentBasis:
    screen_id: str
    screen_sequence: int
    snapshot_id: int
    captured_at: str
    package_name: str | None
    activity_name: str | None
    public_screen: PublicScreen
    compiled_screen: CompiledScreen
    artifacts: ScreenArtifacts | None
    public_refs: frozenset[str]


def get_authoritative_current_basis(
    runtime: WorkspaceRuntime,
) -> AuthoritativeCurrentBasis | None:
    with runtime.lock:
        if runtime.status is not RuntimeStatus.READY:
            return None
        current_screen_id = runtime.current_screen_id
        if not isinstance(current_screen_id, str) or not current_screen_id:
            return None
        latest_snapshot = runtime.latest_snapshot
        screen_state = runtime.screen_state
        if latest_snapshot is None or screen_state is None:
            return None
        public_screen = screen_state.public_screen
        compiled_screen = screen_state.compiled_screen
        if public_screen is None or compiled_screen is None:
            return None
        if public_screen.screen_id != current_screen_id:
            return None
        if compiled_screen.screen_id != current_screen_id:
            return None
        if compiled_screen.source_snapshot_id != latest_snapshot.snapshot_id:
            return None
        if compiled_screen.sequence != runtime.screen_sequence:
            return None
        if compiled_screen.captured_at != latest_snapshot.captured_at:
            return None
        if compiled_screen.package_name != latest_snapshot.package_name:
            return None
        if compiled_screen.activity_name != latest_snapshot.activity_name:
            return None
        if public_screen.app.package_name != latest_snapshot.package_name:
            return None
        if public_screen.app.activity_name != latest_snapshot.activity_name:
            return None

        public_screen_copy = deepcopy(public_screen)
        compiled_screen_copy = deepcopy(compiled_screen)
        artifacts_copy = deepcopy(screen_state.artifacts)
        return AuthoritativeCurrentBasis(
            screen_id=current_screen_id,
            screen_sequence=runtime.screen_sequence,
            snapshot_id=latest_snapshot.snapshot_id,
            captured_at=latest_snapshot.captured_at,
            package_name=latest_snapshot.package_name,
            activity_name=latest_snapshot.activity_name,
            public_screen=public_screen_copy,
            compiled_screen=compiled_screen_copy,
            artifacts=artifacts_copy,
            public_refs=frozenset(
                node.ref
                for group in public_screen_copy.groups
                for node in iter_public_nodes(group.nodes)
                if node.ref
            ),
        )


def current_public_screen(
    runtime: WorkspaceRuntime, *, copy_value: bool = True
) -> PublicScreen | None:
    if runtime.screen_state is None or runtime.screen_state.public_screen is None:
        return None
    if copy_value:
        return deepcopy(runtime.screen_state.public_screen)
    return runtime.screen_state.public_screen


def current_compiled_screen(
    runtime: WorkspaceRuntime, *, copy_value: bool = True
) -> CompiledScreen | None:
    if runtime.screen_state is None or runtime.screen_state.compiled_screen is None:
        return None
    if copy_value:
        return deepcopy(runtime.screen_state.compiled_screen)
    return runtime.screen_state.compiled_screen


def current_artifacts(
    runtime: WorkspaceRuntime, *, copy_value: bool = True
) -> ScreenArtifacts | None:
    if runtime.screen_state is None or runtime.screen_state.artifacts is None:
        return None
    if copy_value:
        return deepcopy(runtime.screen_state.artifacts)
    return runtime.screen_state.artifacts
