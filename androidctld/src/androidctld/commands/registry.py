"""Semantic command registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from androidctl_contracts.command_catalog import (
    daemon_command_kinds_for_route,
)
from androidctld.commands.command_models import GlobalCommand
from androidctld.protocol import CommandKind

CommandFamily = Literal[
    "connect",
    "observe",
    "list_apps",
    "open",
    "ref_action",
    "type",
    "scroll",
    "global_action",
    "wait",
    "screenshot",
]


@dataclass(frozen=True)
class CommandSpec:
    daemon_kind: str
    family: CommandFamily
    dispatch_method_name: str

    @property
    def command_name(self) -> str:
        return self.daemon_kind


_FAMILY_BY_DAEMON_KIND: dict[str, CommandFamily] = {
    "connect": "connect",
    "observe": "observe",
    "listApps": "list_apps",
    "open": "open",
    "tap": "ref_action",
    "longTap": "ref_action",
    "focus": "ref_action",
    "type": "type",
    "submit": "ref_action",
    "scroll": "scroll",
    "back": "global_action",
    "home": "global_action",
    "recents": "global_action",
    "notifications": "global_action",
    "wait": "wait",
    "screenshot": "screenshot",
}

_FAMILY_DISPATCH_METHOD_NAMES: dict[CommandFamily, str] = {
    "connect": "execute_connect",
    "observe": "execute_observe",
    "list_apps": "execute_list_apps",
    "open": "execute_open",
    "ref_action": "execute_ref_action",
    "type": "execute_ref_action",
    "scroll": "execute_ref_action",
    "global_action": "execute_global_action",
    "wait": "execute_wait",
    "screenshot": "execute_screenshot",
}

_commands_run_daemon_kinds = daemon_command_kinds_for_route("commands_run")
_family_keys = set(_FAMILY_BY_DAEMON_KIND)
if _family_keys != _commands_run_daemon_kinds:
    missing = sorted(_commands_run_daemon_kinds - _family_keys)
    extra = sorted(_family_keys - _commands_run_daemon_kinds)
    raise RuntimeError(
        "daemon command family mapping drifted from shared catalog: "
        f"missing={missing}, extra={extra}"
    )

COMMAND_SPECS: dict[str, CommandSpec] = {}
for daemon_kind in _FAMILY_BY_DAEMON_KIND:
    family = _FAMILY_BY_DAEMON_KIND[daemon_kind]
    COMMAND_SPECS[daemon_kind] = CommandSpec(
        daemon_kind=daemon_kind,
        family=family,
        dispatch_method_name=_FAMILY_DISPATCH_METHOD_NAMES[family],
    )


def get_command_spec(command_name: str) -> CommandSpec:
    return COMMAND_SPECS[command_name]


def resolve_command_spec(command_or_name: object) -> CommandSpec:
    if isinstance(command_or_name, str):
        return get_command_spec(command_or_name)

    if isinstance(command_or_name, GlobalCommand):
        return get_command_spec(command_or_name.action)

    command_name = getattr(command_or_name, "kind", None)
    if isinstance(command_name, CommandKind):
        command_name = command_name.value
    if not isinstance(command_name, str):
        raise KeyError(command_or_name)
    return get_command_spec(command_name)


__all__ = [
    "COMMAND_SPECS",
    "CommandFamily",
    "CommandSpec",
    "get_command_spec",
    "resolve_command_spec",
]
