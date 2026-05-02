"""Shared screen refresh transaction and signature helpers."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.models import CommandRecord
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import CommandKind
from androidctld.refs.models import RefRegistry
from androidctld.refs.service import RefRegistryBuilder
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease
from androidctld.runtime.kernel import ScreenRefreshUpdate
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.compiler import (
    CompiledScreen,
    SemanticCompiler,
    SemanticNode,
)
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot
from androidctld.text_equivalence import canonical_text_key, searchable_raw_node_texts

RefreshScreenResult = tuple[
    RawSnapshot,
    PublicScreen,
    ScreenArtifacts,
]
RefreshCandidateValidator = Callable[[RawSnapshot, PublicScreen, CompiledScreen], None]


@dataclass(frozen=True)
class ScreenRefreshBasis:
    sequence: int
    previous_registry: RefRegistry
    lifecycle_lease: RuntimeLifecycleLease | None
    command_kind: CommandKind | None
    wait_kind: object | None
    record: CommandRecord | None


def _normalize_wait_kind(wait_kind: object | None) -> str | None:
    if wait_kind is None:
        return None
    return str(getattr(wait_kind, "value", wait_kind))


class ScreenRefreshService:
    def __init__(
        self,
        runtime_kernel: RuntimeKernel,
        semantic_compiler: SemanticCompiler | None = None,
        artifact_writer: ArtifactWriter | None = None,
        ref_registry_builder: RefRegistryBuilder | None = None,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._semantic_compiler = semantic_compiler or SemanticCompiler()
        self._artifact_writer = artifact_writer or ArtifactWriter()
        self._ref_registry_builder = ref_registry_builder or RefRegistryBuilder()

    @property
    def runtime_kernel(self) -> RuntimeKernel:
        return self._runtime_kernel

    def refresh(
        self,
        session: WorkspaceRuntime,
        snapshot: RawSnapshot,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
        command_kind: CommandKind | None = None,
        wait_kind: object | None = None,
        record: CommandRecord | None = None,
        candidate_validator: RefreshCandidateValidator | None = None,
    ) -> RefreshScreenResult:
        basis = _capture_refresh_basis(
            session,
            lifecycle_lease=lifecycle_lease,
            command_kind=command_kind,
            wait_kind=wait_kind,
            record=record,
        )
        compiled_screen = self._semantic_compiler.compile(basis.sequence, snapshot)
        reconcile_result = self._ref_registry_builder.finalize_compiled_screen(
            compiled_screen=compiled_screen,
            snapshot_id=snapshot.snapshot_id,
            previous_registry=basis.previous_registry,
        )
        ref_registry = reconcile_result.registry
        finalized_compiled_screen = reconcile_result.compiled_screen
        public_screen = finalized_compiled_screen.to_public_screen()
        staged_artifacts = self._artifact_writer.stage_screen(
            session,
            public_screen,
            sequence=finalized_compiled_screen.sequence,
            source_snapshot_id=finalized_compiled_screen.source_snapshot_id,
            captured_at=finalized_compiled_screen.captured_at,
            ref_registry=ref_registry,
        )
        artifacts = staged_artifacts.artifacts

        def raise_if_refresh_stale(active_session: WorkspaceRuntime) -> None:
            raise_if_stale(
                active_session,
                basis.lifecycle_lease,
                kind=basis.command_kind,
                wait_kind=basis.wait_kind,
                record=basis.record,
            )
            if candidate_validator is not None:
                candidate_validator(snapshot, public_screen, finalized_compiled_screen)

        self._runtime_kernel.commit_screen_refresh(
            session,
            update=ScreenRefreshUpdate(
                sequence=basis.sequence,
                snapshot=snapshot,
                public_screen=public_screen,
                compiled_screen=finalized_compiled_screen,
                artifacts=artifacts,
                ref_registry=ref_registry,
                staged_artifacts=staged_artifacts,
            ),
            pre_commit=raise_if_refresh_stale,
        )
        return snapshot, public_screen, artifacts


def _capture_refresh_basis(
    session: WorkspaceRuntime,
    *,
    lifecycle_lease: RuntimeLifecycleLease | None,
    command_kind: CommandKind | None,
    wait_kind: object | None,
    record: CommandRecord | None,
) -> ScreenRefreshBasis:
    with session.lock:
        raise_if_stale(
            session,
            lifecycle_lease,
            kind=command_kind,
            wait_kind=wait_kind,
            record=record,
        )
        return ScreenRefreshBasis(
            sequence=session.screen_sequence + 1,
            previous_registry=deepcopy(session.ref_registry),
            lifecycle_lease=lifecycle_lease,
            command_kind=command_kind,
            wait_kind=deepcopy(wait_kind),
            record=deepcopy(record),
        )


def raise_if_stale(
    session: WorkspaceRuntime,
    lease: RuntimeLifecycleLease | None,
    *,
    kind: CommandKind | None = None,
    wait_kind: object | None = None,
    record: CommandRecord | None = None,
) -> None:
    if lease is None or lease.is_current(session):
        return
    details = {"workspaceRoot": session.workspace_root.as_posix()}
    if record is not None:
        details["commandId"] = record.command_id
    if kind is not None:
        details["kind"] = kind.value
    normalized_wait_kind = _normalize_wait_kind(wait_kind)
    if kind is CommandKind.WAIT and normalized_wait_kind is not None:
        details["waitKind"] = normalized_wait_kind
    raise DaemonError(
        code=DaemonErrorCode.COMMAND_CANCELLED,
        message=(
            "command was canceled"
            if kind is None
            else (
                f"wait {normalized_wait_kind} was canceled"
                if kind is CommandKind.WAIT and normalized_wait_kind is not None
                else f"{kind.value} was canceled"
            )
        ),
        retryable=False,
        details=details,
        http_status=200,
    )


def compiled_screen_nodes(compiled_screen: CompiledScreen) -> tuple[SemanticNode, ...]:
    return (
        *compiled_screen.targets,
        *compiled_screen.context,
        *compiled_screen.dialog,
        *compiled_screen.keyboard,
        *compiled_screen.system,
    )


def compiled_screen_signature(
    compiled_screen: CompiledScreen | None,
    snapshot: RawSnapshot,
) -> tuple[Any, ...]:
    if compiled_screen is None:
        return (
            snapshot.package_name,
            snapshot.activity_name,
            tuple(
                tuple(
                    canonical_text_key(value)
                    for value in searchable_raw_node_texts(node)
                )
                for node in snapshot.nodes
                if node.visible_to_user
            ),
        )
    return (
        compiled_screen.package_name,
        compiled_screen.activity_name,
        compiled_screen.keyboard_visible,
        tuple(
            (
                node.group,
                node.role,
                canonical_text_key(node.label),
                tuple(canonical_text_key(value) for value in node.state),
                tuple(node.actions),
                node.ref,
                None if node.bounds is None else tuple(node.bounds),
            )
            for node in compiled_screen_nodes(compiled_screen)
        ),
    )


def settle_screen_signature(
    compiled_screen: CompiledScreen | None,
    snapshot: RawSnapshot,
) -> tuple[Any, ...]:
    if compiled_screen is None:
        return (
            snapshot.package_name,
            snapshot.activity_name,
            tuple(
                tuple(
                    canonical_text_key(value)
                    for value in searchable_raw_node_texts(node)
                )
                for node in snapshot.nodes
                if node.visible_to_user
            ),
        )
    return (
        compiled_screen.package_name,
        compiled_screen.activity_name,
        compiled_screen.keyboard_visible,
        tuple(
            (
                node.group,
                node.role,
                canonical_text_key(node.label),
                tuple(canonical_text_key(value) for value in node.state),
                tuple(node.actions),
                None if node.bounds is None else tuple(node.bounds),
            )
            for node in compiled_screen_nodes(compiled_screen)
        ),
    )
