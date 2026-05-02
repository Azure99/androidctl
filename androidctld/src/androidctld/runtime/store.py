"""Workspace runtime store and persistence."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from androidctld.config import DaemonConfig
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.state_repo import RuntimeStateRepository
from androidctld.schema.persistence import RUNTIME_STATE_FILE_NAME


class RuntimeSerialCommandBusyError(RuntimeError):
    """Raised when a public runtime command is already active."""


class RuntimeStore:
    def __init__(
        self,
        config: DaemonConfig,
        state_repo: RuntimeStateRepository | None = None,
    ) -> None:
        self._config = config
        self._state_repo = state_repo or RuntimeStateRepository()
        self._lock = threading.RLock()
        self._runtime: WorkspaceRuntime | None = None
        self._active_command: str | None = None

    def get_runtime(self) -> WorkspaceRuntime:
        with self._lock:
            if self._runtime is None:
                runtime_path = (
                    self._config.workspace_root
                    / ".androidctl"
                    / RUNTIME_STATE_FILE_NAME
                )
                loaded = self._state_repo.load(runtime_path)
                if loaded is None:
                    artifact_root = runtime_path.parent
                    self._runtime = WorkspaceRuntime(
                        workspace_root=self._config.workspace_root,
                        artifact_root=artifact_root,
                        runtime_path=runtime_path,
                        status=RuntimeStatus.NEW,
                    )
                else:
                    self._runtime = loaded
            return self._runtime

    def persist_runtime(self, runtime: WorkspaceRuntime) -> None:
        artifact_root = self._config.workspace_root / ".androidctl"
        self._state_repo.persist(
            runtime,
            runtime_path=artifact_root / RUNTIME_STATE_FILE_NAME,
        )

    def ensure_runtime(self) -> WorkspaceRuntime:
        return self.get_runtime()

    @contextmanager
    def begin_serial_command(self, command_name: str) -> Iterator[None]:
        with self._lock:
            if self._active_command is not None:
                raise RuntimeSerialCommandBusyError(
                    "overlapping control requests are not allowed"
                )
            self._active_command = command_name
        try:
            yield
        finally:
            with self._lock:
                if self._active_command == command_name:
                    self._active_command = None
