"""Typed command ledger models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctld.commands.semantic_command_names import (
    semantic_result_command_for_daemon_kind,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import CommandKind


class CommandStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class CachedCommandError:
    code: DaemonErrorCode
    message: str
    retryable: bool
    details: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", DaemonErrorCode(self.code))

    @classmethod
    def from_daemon_error(cls, error: DaemonError) -> CachedCommandError:
        return cls(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=dict(error.details),
        )


@dataclass
class CommandRecord:
    command_id: str
    kind: CommandKind
    status: CommandStatus
    started_at: str
    result_command: str | None = None
    completed_at: str | None = None
    result: CommandResultCore | RetainedResultEnvelope | ListAppsResult | None = None
    error: CachedCommandError | None = None

    def __post_init__(self) -> None:
        if self.result_command is None:
            self.result_command = semantic_result_command_for_daemon_kind(self.kind)
            return
        normalized_result_command = self.result_command.strip()
        if not normalized_result_command:
            raise ValueError("result_command must be a non-empty string")
        self.result_command = normalized_result_command
