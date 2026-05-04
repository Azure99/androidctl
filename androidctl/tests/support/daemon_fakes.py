from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from androidctl.commands.run_pipeline import AppContext
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import CommandRunRequest, RuntimePayload

CommandResultPayload = CommandResultCore | RetainedResultEnvelope | ListAppsResult


@dataclass
class BaseFakeDaemon:
    root: Path
    runtime_payload: RuntimePayload | None = None
    current_screen_id: str | None = "screen-00013"

    def __post_init__(self) -> None:
        self.runtime_calls: list[dict[str, object]] = []
        self.run_calls: list[dict[str, object]] = []

    @property
    def artifact_root(self) -> str:
        return f"{self.root.as_posix()}/.androidctl"

    @property
    def runtime_requests(self) -> int:
        return len(self.runtime_calls)

    def get_runtime(self) -> RuntimePayload:
        runtime = self.runtime_payload or RuntimePayload.model_validate(
            {
                "workspaceRoot": self.root.as_posix(),
                "artifactRoot": self.artifact_root,
                "status": "ready",
                "currentScreenId": self.current_screen_id,
            }
        )
        self.runtime_calls.append(
            {
                "workspaceRoot": runtime.workspace_root,
                "artifactRoot": runtime.artifact_root,
            }
        )
        return runtime

    def close_runtime(self) -> RetainedResultEnvelope:
        raise AssertionError("close_runtime was not expected for this fake daemon")


@dataclass
class RecordingFakeDaemon(BaseFakeDaemon):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.run_requests: list[CommandRunRequest] = []

    @property
    def runs(self) -> list[dict[str, object]]:
        return self.run_calls

    def record_run(self, request: CommandRunRequest) -> dict[str, object]:
        serialized = request.model_dump(exclude_none=True, exclude_defaults=True)
        self.run_requests.append(request)
        self.run_calls.append(serialized)
        return serialized

    def record_command(self, request: CommandRunRequest) -> dict[str, object]:
        self.record_run(request)
        return request.command.model_dump(exclude_none=True)


RunHandler = Callable[
    ["ScriptedRecordingDaemon", CommandRunRequest, dict[str, object]],
    CommandResultPayload,
]
CloseHandler = Callable[["ScriptedRecordingDaemon"], RetainedResultEnvelope]


@dataclass
class ScriptedRecordingDaemon(RecordingFakeDaemon):
    command_handlers: Mapping[str, RunHandler] = field(default_factory=dict)
    close_handler: CloseHandler | None = None

    def run_command(self, *, request: CommandRunRequest) -> CommandResultPayload:
        command = self.record_command(request)
        kind = str(command["kind"])
        handler = self.command_handlers.get(kind)
        if handler is None:
            raise AssertionError(f"unexpected command kind {kind!r}")
        return handler(self, request, command)

    def close_runtime(self) -> RetainedResultEnvelope:
        if self.close_handler is None:
            return super().close_runtime()
        return self.close_handler(self)


TFakeDaemon = TypeVar("TFakeDaemon", bound=BaseFakeDaemon)


def patch_cli_context(
    monkeypatch,
    *,
    tmp_path: Path,
    daemon: TFakeDaemon,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> TFakeDaemon:
    context = AppContext(
        daemon=daemon,
        cwd=cwd or tmp_path,
        env=env or {},
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.build_context", lambda: context
    )
    monkeypatch.setattr(
        "androidctl.commands.execute.run_pipeline.build_context", lambda: context
    )
    return daemon


__all__ = [
    "BaseFakeDaemon",
    "RecordingFakeDaemon",
    "ScriptedRecordingDaemon",
    "patch_cli_context",
]
