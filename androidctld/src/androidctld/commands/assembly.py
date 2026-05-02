"""Command service object-graph assembly helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from androidctld.actions.executor import ActionExecutor
from androidctld.actions.repair import ActionCommandRepairer
from androidctld.actions.settle import ActionSettler
from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.dispatch import CommandDispatch
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.handlers.action import ActionCommandHandler
from androidctld.commands.handlers.connect import ConnectCommandHandler
from androidctld.commands.handlers.list_apps import ListAppsCommandHandler
from androidctld.commands.handlers.observe import ObserveCommandHandler
from androidctld.commands.handlers.screenshot import ScreenshotCommandHandler
from androidctld.commands.handlers.wait import WaitCommandHandler
from androidctld.commands.orchestration import CommandRunOrchestrator
from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.interfaces import (
    DeviceClientFactory,
    DeviceClientProvider,
    DeviceRuntimeClient,
)
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease, RuntimeStore
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.refresh import ScreenRefreshService
from androidctld.snapshots.service import SnapshotService
from androidctld.waits.loop import WaitRuntimeLoop

SleepFn = Callable[[float], None]
TimeFn = Callable[[], float]

__all__ = ["CommandServiceAssembly", "SleepFn", "assemble_command_service"]


class _RuntimePersistencePort:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        runtime_store: RuntimeStore,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._runtime_store = runtime_store

    def ensure_runtime(self) -> WorkspaceRuntime:
        return self._runtime_kernel.ensure_runtime()

    def persist_runtime(self, runtime: WorkspaceRuntime) -> None:
        self._runtime_store.persist_runtime(runtime)


@dataclass(slots=True)
class CommandServiceAssembly:
    sleep_fn: SleepFn
    time_fn: TimeFn
    runtime_kernel: RuntimeKernel
    runtime_store: _RuntimePersistencePort
    bootstrapper: DeviceBootstrapper
    snapshot_service: SnapshotService
    semantic_compiler: SemanticCompiler
    device_client_factory: DeviceClientFactory
    artifact_writer: ArtifactWriter
    screen_refresh: ScreenRefreshService
    action_executor: ActionExecutor
    wait_runtime_loop: WaitRuntimeLoop
    connect_handler: ConnectCommandHandler
    observe_handler: ObserveCommandHandler
    list_apps_handler: ListAppsCommandHandler
    action_handler: ActionCommandHandler
    wait_handler: WaitCommandHandler
    screenshot_handler: ScreenshotCommandHandler
    orchestrator: CommandRunOrchestrator
    dispatch: CommandDispatch
    executor: CommandExecutor


def assemble_command_service(
    *,
    runtime_store: RuntimeStore,
    bootstrapper: DeviceBootstrapper | None = None,
    snapshot_service: SnapshotService | None = None,
    semantic_compiler: SemanticCompiler | None = None,
    artifact_writer: ArtifactWriter | None = None,
    device_client_factory: DeviceClientFactory | None = None,
    sleep_fn: SleepFn | None = None,
    time_fn: TimeFn | None = None,
) -> CommandServiceAssembly:
    resolved_sleep_fn = sleep_fn or time.sleep
    resolved_time_fn = time_fn or time.monotonic
    runtime_kernel = RuntimeKernel(
        runtime_store,
        sleep_fn=resolved_sleep_fn,
        time_fn=resolved_time_fn,
    )
    persistence_port = _RuntimePersistencePort(
        runtime_kernel=runtime_kernel,
        runtime_store=runtime_store,
    )
    resolved_bootstrapper = bootstrapper or DeviceBootstrapper()
    default_snapshot_service = SnapshotService(
        bootstrapper=resolved_bootstrapper,
        runtime_kernel=runtime_kernel,
    )
    resolved_snapshot_service = snapshot_service or default_snapshot_service
    resolved_semantic_compiler = semantic_compiler or SemanticCompiler()
    resolved_device_client_factory = _resolve_device_client_factory(
        snapshot_service=resolved_snapshot_service,
        explicit_factory=device_client_factory,
    )
    resolved_artifact_writer = artifact_writer or ArtifactWriter()
    screen_refresh = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
        semantic_compiler=resolved_semantic_compiler,
        artifact_writer=resolved_artifact_writer,
    )
    action_executor = ActionExecutor(
        device_client_factory=resolved_device_client_factory,
        screen_refresh=screen_refresh,
        settler=ActionSettler(
            snapshot_service=resolved_snapshot_service,
            semantic_compiler=resolved_semantic_compiler,
            sleep_fn=resolved_sleep_fn,
            time_fn=resolved_time_fn,
        ),
        repairer=ActionCommandRepairer(
            snapshot_service=resolved_snapshot_service,
            screen_refresh=screen_refresh,
        ),
        runtime_kernel=runtime_kernel,
    )
    wait_runtime_loop = WaitRuntimeLoop(
        snapshot_service=resolved_snapshot_service,
        screen_refresh=screen_refresh,
        device_client_factory=resolved_device_client_factory,
        sleep_fn=resolved_sleep_fn,
        time_fn=resolved_time_fn,
    )
    connect_handler = ConnectCommandHandler(
        runtime_kernel=runtime_kernel,
        bootstrapper=resolved_bootstrapper,
        snapshot_service=resolved_snapshot_service,
        screen_refresh=screen_refresh,
    )
    observe_handler = ObserveCommandHandler(
        runtime_kernel=runtime_kernel,
        snapshot_service=resolved_snapshot_service,
        screen_refresh=screen_refresh,
    )
    list_apps_handler = ListAppsCommandHandler(
        runtime_kernel=runtime_kernel,
        bootstrapper=resolved_bootstrapper,
    )
    action_handler = ActionCommandHandler(
        runtime_kernel=runtime_kernel,
        action_executor=action_executor,
    )
    wait_handler = WaitCommandHandler(
        runtime_kernel=runtime_kernel,
        wait_runtime_loop=wait_runtime_loop,
    )
    screenshot_handler = ScreenshotCommandHandler(
        runtime_kernel=runtime_kernel,
        device_client_factory=resolved_device_client_factory,
        artifact_writer=resolved_artifact_writer,
    )
    orchestrator = CommandRunOrchestrator(
        serial_admission=runtime_store.begin_serial_command,
        time_fn=resolved_time_fn,
    )
    dispatch = CommandDispatch(
        connect_handler=connect_handler,
        observe_handler=observe_handler,
        list_apps_handler=list_apps_handler,
        action_handler=action_handler,
        wait_handler=wait_handler,
        screenshot_handler=screenshot_handler,
    )
    executor = CommandExecutor(handlers=dispatch.build_handlers())
    return CommandServiceAssembly(
        sleep_fn=resolved_sleep_fn,
        time_fn=resolved_time_fn,
        runtime_kernel=runtime_kernel,
        runtime_store=persistence_port,
        bootstrapper=resolved_bootstrapper,
        snapshot_service=resolved_snapshot_service,
        semantic_compiler=resolved_semantic_compiler,
        device_client_factory=resolved_device_client_factory,
        artifact_writer=resolved_artifact_writer,
        screen_refresh=screen_refresh,
        action_executor=action_executor,
        wait_runtime_loop=wait_runtime_loop,
        connect_handler=connect_handler,
        observe_handler=observe_handler,
        list_apps_handler=list_apps_handler,
        action_handler=action_handler,
        wait_handler=wait_handler,
        screenshot_handler=screenshot_handler,
        orchestrator=orchestrator,
        dispatch=dispatch,
        executor=executor,
    )


def _resolve_device_client_factory(
    *,
    snapshot_service: object,
    explicit_factory: DeviceClientFactory | None,
) -> DeviceClientFactory:
    if explicit_factory is not None:
        return explicit_factory
    if isinstance(snapshot_service, DeviceClientProvider):
        return snapshot_service.device_client
    return _missing_device_client_factory


def _missing_device_client_factory(
    session: WorkspaceRuntime,
    *,
    lifecycle_lease: RuntimeLifecycleLease | None = None,
) -> DeviceRuntimeClient:
    del session, lifecycle_lease
    raise RuntimeError(
        "CommandService requires device_client_factory when snapshot_service "
        "does not provide device_client()"
    )
