"""Runtime lifecycle, normalization, and progress-lane coordination."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.device.types import (
    BootstrapResult,
    ConnectionConfig,
    RuntimeTransport,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import RuntimeStatus
from androidctld.refs.models import RefRegistry
from androidctld.runtime.lifecycle import RuntimeLifecycleLease, capture_lifecycle_lease
from androidctld.runtime.models import ScreenState, WorkspaceRuntime
from androidctld.runtime.screen_state import get_authoritative_current_basis
from androidctld.runtime.store import RuntimeStore
from androidctld.runtime_policy import (
    QUERY_PROGRESS_POLL_SECONDS,
    QUERY_PROGRESS_WAIT_SECONDS,
)
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot

if TYPE_CHECKING:
    from androidctld.artifacts.writer import StagedArtifactWrite


@dataclass(frozen=True)
class _ScreenRefreshRestoreState:
    screen_sequence: int
    current_screen_id: str | None
    status: RuntimeStatus
    latest_snapshot: RawSnapshot | None
    previous_snapshot: RawSnapshot | None
    screen_state: ScreenState | None
    ref_registry: RefRegistry


@dataclass(frozen=True)
class ScreenshotArtifactAttachment:
    current_screen: PublicScreen | None
    artifacts: ScreenArtifacts


@dataclass(frozen=True)
class ScreenRefreshUpdate:
    sequence: int
    snapshot: RawSnapshot
    public_screen: PublicScreen
    compiled_screen: CompiledScreen
    artifacts: ScreenArtifacts
    ref_registry: RefRegistry
    staged_artifacts: StagedArtifactWrite


class RuntimeKernel:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime_store = runtime_store
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn

    def ensure_runtime(self) -> WorkspaceRuntime:
        runtime = self._runtime_store.get_runtime()
        normalize_stale_ready_runtime(
            runtime,
            persist=self.commit_runtime,
        )
        return runtime

    def capture_lifecycle_lease(
        self,
        runtime: WorkspaceRuntime,
    ) -> RuntimeLifecycleLease:
        with runtime.lock:
            return capture_lifecycle_lease(runtime)

    @contextmanager
    def committed_runtime_mutation(
        self,
        runtime: WorkspaceRuntime,
        *,
        persist: Callable[[WorkspaceRuntime], None] | None = None,
        rollback: Callable[[WorkspaceRuntime], None] | None = None,
    ) -> Iterator[WorkspaceRuntime]:
        with runtime.lock:
            try:
                yield runtime
                (persist or self.commit_runtime)(runtime)
            except Exception:
                if rollback is not None:
                    rollback(runtime)
                raise

    def commit_runtime(self, runtime: WorkspaceRuntime) -> None:
        runtime.artifact_root.mkdir(parents=True, exist_ok=True)
        self._runtime_store.persist_runtime(runtime)

    def commit_screen_refresh(
        self,
        runtime: WorkspaceRuntime,
        *,
        update: ScreenRefreshUpdate,
        pre_commit: Callable[[WorkspaceRuntime], None] | None = None,
    ) -> None:
        restore_state: _ScreenRefreshRestoreState | None = None

        def rollback_refresh(active_runtime: WorkspaceRuntime) -> None:
            if restore_state is None:
                update.staged_artifacts.discard()
                return
            active_runtime.screen_sequence = restore_state.screen_sequence
            active_runtime.current_screen_id = restore_state.current_screen_id
            active_runtime.status = restore_state.status
            active_runtime.latest_snapshot = restore_state.latest_snapshot
            active_runtime.previous_snapshot = restore_state.previous_snapshot
            active_runtime.screen_state = restore_state.screen_state
            active_runtime.ref_registry = restore_state.ref_registry
            update.staged_artifacts.rollback()
            update.staged_artifacts.discard()

        with self.committed_runtime_mutation(
            runtime,
            rollback=rollback_refresh,
        ):
            if pre_commit is not None:
                pre_commit(runtime)
            restore_state = _capture_screen_refresh_restore_state(runtime)
            update.staged_artifacts.commit()
            _commit_screen_refresh_locked(
                runtime,
                sequence=update.sequence,
                snapshot=update.snapshot,
                public_screen=update.public_screen,
                compiled_screen=update.compiled_screen,
                artifacts=update.artifacts,
                ref_registry=update.ref_registry,
                previous_snapshot=restore_state.latest_snapshot,
            )
        update.staged_artifacts.discard()

    def attach_screenshot_artifact(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
        *,
        screenshot_png: str,
    ) -> ScreenshotArtifactAttachment | None:
        with runtime.lock:
            if not lease.is_current(runtime):
                return None
            screen_state = runtime.screen_state
            artifacts = (
                ScreenArtifacts(screen_json=None)
                if screen_state is None or screen_state.artifacts is None
                else screen_state.artifacts
            ).with_screenshot(screenshot_png)
            if screen_state is None:
                runtime.screen_state = ScreenState(
                    public_screen=None,
                    compiled_screen=None,
                    artifacts=artifacts,
                )
                current_screen = None
            else:
                screen_state.artifacts = artifacts
                current_screen = screen_state.public_screen
            return ScreenshotArtifactAttachment(
                current_screen=current_screen,
                artifacts=artifacts,
            )

    def normalize_stale_ready_runtime(
        self,
        runtime: WorkspaceRuntime,
        *,
        persist: Callable[[WorkspaceRuntime], None] | None = None,
    ) -> bool:
        return normalize_stale_ready_runtime(
            runtime,
            persist=persist or self.commit_runtime,
        )

    def begin_connect(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
        *,
        transport: RuntimeTransport,
    ) -> bool:
        with runtime.lock:
            if not lease.is_current(runtime):
                return False
            _clear_runtime_state_locked(runtime)
            runtime.transport = transport
            runtime.status = RuntimeStatus.BOOTSTRAPPING
            self.commit_runtime(runtime)
        return True

    def activate_connect(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
        *,
        bootstrap_result: BootstrapResult,
        device_token: str,
    ) -> bool:
        close_transport = None
        with runtime.lock:
            if not lease.is_current(runtime):
                close_transport = bootstrap_result.transport
            else:
                runtime.connection = bootstrap_result.connection
                runtime.transport = bootstrap_result.transport
                runtime.device_token = device_token
                runtime.device_capabilities = bootstrap_result.meta.capabilities
                runtime.status = RuntimeStatus.CONNECTED
                self.commit_runtime(runtime)
                return True
        if close_transport is not None:
            close_transport.close()
        return False

    def rebootstrap_transport(
        self,
        runtime: WorkspaceRuntime,
        *,
        bootstrap: Callable[[ConnectionConfig], BootstrapResult],
        lease: RuntimeLifecycleLease | None = None,
    ) -> RuntimeTransport:
        with runtime.lock:
            if lease is not None and not lease.is_current(runtime):
                raise _runtime_not_connected_error(runtime)
            if runtime.transport is not None:
                return runtime.transport
            if runtime.connection is None or not runtime.device_token:
                raise _runtime_not_connected_error(runtime)
            active_lease = lease or capture_lifecycle_lease(runtime)
            connection_config = runtime.connection.to_connection_config(
                runtime.device_token
            )

        bootstrap_result = bootstrap(connection_config)
        transport = self.commit_transport_rebootstrap(
            runtime,
            active_lease,
            bootstrap_result=bootstrap_result,
        )
        if transport is None:
            raise _runtime_not_connected_error(runtime)
        return transport

    def commit_transport_rebootstrap(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
        *,
        bootstrap_result: BootstrapResult,
    ) -> RuntimeTransport | None:
        close_transport = None
        result_transport = None
        with runtime.lock:
            if not lease.is_current(runtime):
                close_transport = bootstrap_result.transport
            elif runtime.transport is not None:
                close_transport = bootstrap_result.transport
                result_transport = runtime.transport
            else:
                runtime.connection = bootstrap_result.connection
                runtime.transport = bootstrap_result.transport
                runtime.device_capabilities = bootstrap_result.meta.capabilities
                self.commit_runtime(runtime)
                result_transport = bootstrap_result.transport
        if close_transport is not None:
            close_transport.close()
        return result_transport

    def fail_connect(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
    ) -> bool:
        with runtime.lock:
            if not lease.is_current(runtime):
                return False
            _clear_runtime_state_locked(runtime)
            runtime.status = RuntimeStatus.BROKEN
            self.commit_runtime(runtime)
        return True

    def invalidate_device_credentials(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease | None = None,
    ) -> bool:
        with runtime.lock:
            if lease is not None and not lease.is_current(runtime):
                return False
            _clear_runtime_state_locked(runtime)
            runtime.status = RuntimeStatus.BROKEN
            self.commit_runtime(runtime)
        return True

    def drop_current_screen_authority(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease,
        *,
        discard_transport: bool = False,
    ) -> bool:
        with runtime.lock:
            if not lease.is_current(runtime):
                return False
            _drop_current_screen_authority_locked(
                runtime,
                discard_transport=discard_transport,
            )
            self.commit_runtime(runtime)
        return True

    def acquire_progress_lane(
        self,
        runtime: WorkspaceRuntime,
        *,
        occupant_kind: str,
    ) -> None:
        busy_error: DaemonError | None = None
        with runtime.lock:
            if _try_acquire_progress_lane_locked(
                runtime,
                occupant_kind=occupant_kind,
            ):
                return
            busy_error = _runtime_busy_error_locked(runtime)
        assert busy_error is not None
        raise busy_error

    def acquire_query_lane(self, runtime: WorkspaceRuntime) -> None:
        deadline = self._time_fn() + QUERY_PROGRESS_WAIT_SECONDS
        while True:
            busy_error: DaemonError | None = None
            with runtime.lock:
                if _try_acquire_progress_lane_locked(
                    runtime,
                    occupant_kind="query",
                ):
                    return
                if self._time_fn() >= deadline:
                    busy_error = _runtime_busy_error_locked(runtime)
            if busy_error is not None:
                raise busy_error
            self._sleep_fn(QUERY_PROGRESS_POLL_SECONDS)

    def release_progress_lane(self, runtime: WorkspaceRuntime) -> None:
        with runtime.lock:
            runtime.progress_occupant_kind = None
        runtime.progress_lock.release()

    def close_runtime(self, runtime: WorkspaceRuntime) -> None:
        with runtime.lock:
            _clear_runtime_state_locked(runtime)
            runtime.lifecycle_revision += 1
            runtime.status = RuntimeStatus.CLOSED
            self.commit_runtime(runtime)

    def invalidate_runtime(
        self,
        runtime: WorkspaceRuntime,
        lease: RuntimeLifecycleLease | None = None,
    ) -> bool:
        with runtime.lock:
            if lease is not None and not lease.is_current(runtime):
                return False
            _clear_runtime_state_locked(runtime)
            runtime.lifecycle_revision += 1
        return True


def has_live_public_screen(runtime: WorkspaceRuntime) -> bool:
    with runtime.lock:
        if (
            runtime.transport is None
            or runtime.connection is None
            or runtime.device_token is None
        ):
            return False
        return get_authoritative_current_basis(runtime) is not None


def _capture_screen_refresh_restore_state(
    runtime: WorkspaceRuntime,
) -> _ScreenRefreshRestoreState:
    return _ScreenRefreshRestoreState(
        screen_sequence=runtime.screen_sequence,
        current_screen_id=runtime.current_screen_id,
        status=runtime.status,
        latest_snapshot=runtime.latest_snapshot,
        previous_snapshot=runtime.previous_snapshot,
        screen_state=runtime.screen_state,
        ref_registry=runtime.ref_registry,
    )


def _commit_screen_refresh_locked(
    runtime: WorkspaceRuntime,
    *,
    sequence: int,
    snapshot: RawSnapshot,
    public_screen: PublicScreen,
    compiled_screen: CompiledScreen,
    artifacts: ScreenArtifacts,
    ref_registry: RefRegistry,
    previous_snapshot: RawSnapshot | None,
) -> None:
    runtime.previous_snapshot = previous_snapshot
    runtime.latest_snapshot = snapshot
    runtime.screen_sequence = sequence
    runtime.current_screen_id = public_screen.screen_id
    runtime.status = RuntimeStatus.READY
    runtime.screen_state = ScreenState(
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )
    runtime.ref_registry = ref_registry


def normalize_stale_ready_runtime(
    runtime: WorkspaceRuntime,
    *,
    persist: Callable[[WorkspaceRuntime], None] | None = None,
) -> bool:
    with runtime.lock:
        if runtime.status is not RuntimeStatus.READY:
            return False
        if has_live_public_screen(runtime):
            return False
        runtime.latest_snapshot = None
        runtime.previous_snapshot = None
        runtime.current_screen_id = None
        runtime.screen_state = None
        runtime.ref_registry = RefRegistry()
        if (
            runtime.transport is not None
            and runtime.connection is not None
            and runtime.device_token is not None
        ):
            runtime.status = RuntimeStatus.CONNECTED
        else:
            release_transport(runtime)
            runtime.connection = None
            runtime.device_token = None
            runtime.device_capabilities = None
            runtime.status = RuntimeStatus.BROKEN
        if persist is not None:
            persist(runtime)
    return True


def _drop_current_screen_authority_locked(
    runtime: WorkspaceRuntime,
    *,
    discard_transport: bool = False,
) -> None:
    runtime.latest_snapshot = None
    runtime.previous_snapshot = None
    runtime.current_screen_id = None
    runtime.screen_state = None
    runtime.ref_registry = RefRegistry()
    if runtime.connection is not None and runtime.device_token is not None:
        if discard_transport:
            release_transport(runtime)
            runtime.device_capabilities = None
        runtime.status = RuntimeStatus.CONNECTED
        return
    release_transport(runtime)
    runtime.connection = None
    runtime.device_token = None
    runtime.device_capabilities = None
    runtime.status = RuntimeStatus.BROKEN


def release_transport(runtime: WorkspaceRuntime) -> None:
    transport = runtime.transport
    runtime.transport = None
    if transport is not None:
        transport.close()


def _clear_runtime_state_locked(runtime: WorkspaceRuntime) -> None:
    release_transport(runtime)
    runtime.connection = None
    runtime.device_token = None
    runtime.device_capabilities = None
    runtime.latest_snapshot = None
    runtime.previous_snapshot = None
    runtime.current_screen_id = None
    runtime.screen_state = None
    runtime.ref_registry = RefRegistry()


def _try_acquire_progress_lane_locked(
    runtime: WorkspaceRuntime,
    *,
    occupant_kind: str,
) -> bool:
    if not runtime.progress_lock.acquire(blocking=False):
        return False
    runtime.progress_occupant_kind = occupant_kind
    return True


def _runtime_busy_error_locked(runtime: WorkspaceRuntime) -> DaemonError:
    details = {
        "reason": "runtime_progress_busy",
        "workspaceRoot": runtime.workspace_root.as_posix(),
    }
    return DaemonError(
        code=DaemonErrorCode.RUNTIME_BUSY,
        message="runtime already has an in-flight progress command",
        retryable=True,
        details=details,
        http_status=200,
    )


def _runtime_not_connected_error(runtime: WorkspaceRuntime) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
        message="runtime is not connected to a device",
        retryable=False,
        details={"workspaceRoot": runtime.workspace_root.as_posix()},
        http_status=200,
    )
