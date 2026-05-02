"""Per-command semantic boundary dispatch helpers."""

from __future__ import annotations

from typing import cast

from androidctld.commands.command_models import (
    ConnectCommand,
    GlobalCommand,
    InternalCommand,
    ListAppsCommand,
    ObserveCommand,
    OpenCommand,
    ScreenshotCommand,
    WaitCommand,
    is_ref_bound_action_command,
)
from androidctld.commands.executor import CommandHandler, CommandResult
from androidctld.commands.handlers.action import ActionCommandHandler
from androidctld.commands.handlers.connect import ConnectCommandHandler
from androidctld.commands.handlers.list_apps import ListAppsCommandHandler
from androidctld.commands.handlers.observe import ObserveCommandHandler
from androidctld.commands.handlers.screenshot import ScreenshotCommandHandler
from androidctld.commands.handlers.wait import WaitCommandHandler
from androidctld.commands.registry import COMMAND_SPECS

__all__ = ["CommandDispatch"]


class CommandDispatch:
    def __init__(
        self,
        *,
        connect_handler: ConnectCommandHandler,
        observe_handler: ObserveCommandHandler,
        list_apps_handler: ListAppsCommandHandler,
        action_handler: ActionCommandHandler,
        wait_handler: WaitCommandHandler,
        screenshot_handler: ScreenshotCommandHandler,
    ) -> None:
        self._connect_handler = connect_handler
        self._observe_handler = observe_handler
        self._list_apps_handler = list_apps_handler
        self._action_handler = action_handler
        self._wait_handler = wait_handler
        self._screenshot_handler = screenshot_handler

    def build_handlers(self) -> dict[str, CommandHandler]:
        return {
            spec.daemon_kind: cast(
                CommandHandler,
                getattr(self, spec.dispatch_method_name),
            )
            for spec in COMMAND_SPECS.values()
        }

    def execute_connect(self, *, command: InternalCommand) -> CommandResult:
        if not isinstance(command, ConnectCommand):
            raise TypeError(f"connect handler received {command.kind!r} command")
        return self._connect_handler.handle(command=command)

    def execute_observe(self, *, command: InternalCommand) -> CommandResult:
        if not isinstance(command, ObserveCommand):
            raise TypeError(f"observe handler received {command.kind!r} command")
        return self._observe_handler.handle(command=command)

    def execute_list_apps(self, *, command: InternalCommand) -> CommandResult:
        if not isinstance(command, ListAppsCommand):
            raise TypeError(f"list-apps handler received {command.kind!r} command")
        return self._list_apps_handler.handle(command=command)

    def execute_open(self, *, command: InternalCommand) -> CommandResult:
        if not isinstance(command, OpenCommand):
            raise TypeError(f"open handler received {command.kind!r} command")
        return self._action_handler.handle_open(command=command)

    def execute_ref_action(self, *, command: InternalCommand) -> CommandResult:
        if not is_ref_bound_action_command(command):
            raise TypeError(f"ref action handler received {command.kind!r} command")
        return self._action_handler.handle_ref_action(command=command)

    def execute_global_action(
        self,
        *,
        command: InternalCommand,
    ) -> CommandResult:
        if not isinstance(command, GlobalCommand):
            raise TypeError(f"global action handler received {command.kind!r} command")
        return self._action_handler.handle_global_action(command=command)

    def execute_wait(self, *, command: InternalCommand) -> CommandResult:
        if isinstance(command, WaitCommand):
            return self._wait_handler.handle_service_wait(command=command)
        raise TypeError(f"wait handler received {command.kind!r} command")

    def execute_screenshot(self, *, command: InternalCommand) -> CommandResult:
        if not isinstance(command, ScreenshotCommand):
            raise TypeError(f"screenshot handler received {command.kind!r} command")
        return self._screenshot_handler.handle(command=command)
