"""Thin semantic command executor."""

from __future__ import annotations

from typing import Protocol

from androidctld.commands.command_models import InternalCommand
from androidctld.commands.registry import resolve_command_spec

CommandResult = dict[str, object]


class CommandHandler(Protocol):
    def __call__(self, *, command: InternalCommand) -> CommandResult: ...


class CommandExecutor:
    def __init__(self, *, handlers: dict[str, CommandHandler]) -> None:
        self._handlers = handlers

    def run(
        self,
        *,
        command: InternalCommand,
    ) -> CommandResult:
        spec = resolve_command_spec(command)
        handler = self._handlers[spec.command_name]
        return handler(command=command)


__all__ = ["CommandExecutor", "CommandHandler", "CommandResult"]
