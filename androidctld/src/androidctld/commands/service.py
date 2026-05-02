"""Semantic daemon-boundary command service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.assembly import SleepFn, assemble_command_service
from androidctld.commands.command_models import InternalCommand
from androidctld.commands.executor import CommandExecutor
from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.interfaces import DeviceClientFactory
from androidctld.runtime import RuntimeStore
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.service import SnapshotService

__all__ = ["CommandService"]


class CommandService:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        bootstrapper: DeviceBootstrapper | None = None,
        snapshot_service: SnapshotService | None = None,
        semantic_compiler: SemanticCompiler | None = None,
        artifact_writer: ArtifactWriter | None = None,
        device_client_factory: DeviceClientFactory | None = None,
        sleep_fn: SleepFn | None = None,
        time_fn: Callable[[], float] | None = None,
        executor: CommandExecutor | None = None,
    ) -> None:
        assembly = assemble_command_service(
            runtime_store=runtime_store,
            bootstrapper=bootstrapper,
            snapshot_service=snapshot_service,
            semantic_compiler=semantic_compiler,
            artifact_writer=artifact_writer,
            device_client_factory=device_client_factory,
            sleep_fn=sleep_fn,
            time_fn=time_fn,
        )
        self._runtime_kernel = assembly.runtime_kernel
        self._runtime_store = assembly.runtime_store
        self._orchestrator = assembly.orchestrator
        self._executor = assembly.executor if executor is None else executor

    def run(
        self,
        command: InternalCommand,
    ) -> dict[str, Any]:
        runtime = self._runtime_store.ensure_runtime()
        return self._orchestrator.run(
            runtime=runtime,
            command=command,
            execute=lambda: self._executor.run(
                command=command,
            ),
        )

    def close_runtime(self) -> dict[str, Any]:
        runtime = self._runtime_store.ensure_runtime()
        return self._orchestrator.close_runtime(
            runtime=runtime,
            close=lambda: self._runtime_kernel.close_runtime(runtime),
        )
