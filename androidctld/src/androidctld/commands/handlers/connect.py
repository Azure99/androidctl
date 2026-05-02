"""Explicit semantic connect handler."""

from __future__ import annotations

from androidctld.commands.command_models import ConnectCommand
from androidctld.commands.result_models import (
    build_projected_retained_failure_result_for_error,
    build_retained_success_result,
)
from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.types import RuntimeTransport
from androidctld.errors import DaemonError
from androidctld.protocol import CommandKind
from androidctld.runtime import RuntimeKernel
from androidctld.snapshots.refresh import ScreenRefreshService
from androidctld.snapshots.service import SnapshotService


class ConnectCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        bootstrapper: DeviceBootstrapper,
        snapshot_service: SnapshotService,
        screen_refresh: ScreenRefreshService,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._bootstrapper = bootstrapper
        self._snapshot_service = snapshot_service
        self._screen_refresh = screen_refresh

    def handle(
        self,
        *,
        command: ConnectCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
        connection_config = command.connection

        handle = None
        connect_started = False
        try:
            handle = self._bootstrapper.establish_transport(connection_config)
            if not self._runtime_kernel.begin_connect(
                runtime,
                lifecycle_lease,
                transport=RuntimeTransport(
                    endpoint=handle.endpoint,
                    close=handle.close,
                ),
            ):
                handle.close()
                raise RuntimeError("runtime lifecycle changed during connect")
            connect_started = True
            bootstrap_result = self._bootstrapper.bootstrap_runtime(
                handle,
                connection_config,
            )
            if not self._runtime_kernel.activate_connect(
                runtime,
                lifecycle_lease,
                bootstrap_result=bootstrap_result,
                device_token=connection_config.token,
            ):
                raise RuntimeError("runtime lifecycle changed during connect")
            snapshot = self._snapshot_service.fetch(
                runtime,
                force_refresh=True,
                lifecycle_lease=lifecycle_lease,
            )
            snapshot, public_screen, artifacts = self._screen_refresh.refresh(
                runtime,
                snapshot,
                lifecycle_lease=lifecycle_lease,
                command_kind=CommandKind.CONNECT,
            )
            del public_screen, artifacts
            return build_retained_success_result(command="connect").model_dump(
                by_alias=True,
                mode="json",
            )
        except Exception as error:
            if connect_started:
                self._runtime_kernel.fail_connect(runtime, lifecycle_lease)
            elif handle is not None:
                handle.close()
            if isinstance(error, DaemonError):
                return build_projected_retained_failure_result_for_error(
                    command="connect",
                    error=error,
                ).model_dump(by_alias=True, mode="json")
            raise
