"""Session-level snapshot retrieval."""

from __future__ import annotations

from collections.abc import Callable

from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.rpc import DeviceRpcClient
from androidctld.device.types import RuntimeTransport
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime_policy import (
    DEVICE_RPC_REQUEST_ID_SNAPSHOT,
    TRANSIENT_INVALID_SNAPSHOT_RETRY_SECONDS,
)
from androidctld.snapshots.models import RawSnapshot

SleepFn = Callable[[float], None]
TimeFn = Callable[[], float]


class SnapshotService:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        bootstrapper: DeviceBootstrapper | None = None,
    ) -> None:
        self._bootstrapper = bootstrapper or DeviceBootstrapper()
        self._runtime_kernel = runtime_kernel

    def fetch(
        self,
        session: WorkspaceRuntime,
        force_refresh: bool,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
    ) -> RawSnapshot:
        if not force_refresh and session.latest_snapshot is not None:
            _raise_if_stale_lifecycle_lease(session, lifecycle_lease)
            return session.latest_snapshot
        return self.device_client(
            session,
            lifecycle_lease=lifecycle_lease,
        ).snapshot_get(
            request_id=DEVICE_RPC_REQUEST_ID_SNAPSHOT,
        )

    def device_client(
        self,
        session: WorkspaceRuntime,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
    ) -> DeviceRpcClient:
        if session.connection is None or not session.device_token:
            raise DaemonError(
                code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
                message="runtime is not connected to a device",
                retryable=False,
                details={"workspaceRoot": session.workspace_root.as_posix()},
                http_status=200,
            )
        transport = self.ensure_transport(
            session,
            lifecycle_lease=lifecycle_lease,
        )
        return DeviceRpcClient(endpoint=transport.endpoint, token=session.device_token)

    def ensure_transport(
        self,
        session: WorkspaceRuntime,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
    ) -> RuntimeTransport:
        return self._runtime_kernel.rebootstrap_transport(
            session,
            bootstrap=self._bootstrapper.bootstrap,
            lease=lifecycle_lease,
        )


def _raise_if_stale_lifecycle_lease(
    session: WorkspaceRuntime,
    lifecycle_lease: RuntimeLifecycleLease | None,
) -> None:
    if lifecycle_lease is None:
        return
    with session.lock:
        if lifecycle_lease.is_current(session):
            return
    raise DaemonError(
        code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
        message="runtime is not connected to a device",
        retryable=False,
        details={"workspaceRoot": session.workspace_root.as_posix()},
        http_status=200,
    )


def is_transient_invalid_package_snapshot(error: DaemonError) -> bool:
    return (
        error.code == DaemonErrorCode.DEVICE_RPC_FAILED
        and error.details.get("reason") == "invalid_snapshot"
        and error.details.get("field") == "result.packageName"
    )


def fetch_with_transient_invalid_snapshot_retry(
    snapshot_service: SnapshotService,
    *,
    session: WorkspaceRuntime,
    force_refresh: bool,
    lifecycle_lease: RuntimeLifecycleLease | None = None,
    deadline_at: float,
    max_retries: int,
    sleep_fn: SleepFn,
    time_fn: TimeFn,
) -> RawSnapshot:
    attempts = 0
    while True:
        try:
            return snapshot_service.fetch(
                session,
                force_refresh=force_refresh,
                lifecycle_lease=lifecycle_lease,
            )
        except DaemonError as error:
            if not is_transient_invalid_package_snapshot(error):
                raise
            now = time_fn()
            if attempts >= max_retries or now >= deadline_at:
                raise
            attempts += 1
            remaining = max(deadline_at - now, 0.0)
            sleep_duration = min(remaining, TRANSIENT_INVALID_SNAPSHOT_RETRY_SECONDS)
            if sleep_duration > 0.0:
                sleep_fn(sleep_duration)
