"""Runtime command models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias, TypeGuard

from androidctld.commands.open_targets import OpenAppTarget, OpenUrlTarget
from androidctld.device.types import ConnectionConfig
from androidctld.protocol import CommandKind


class WaitKind(str, Enum):
    TEXT = "text"
    SCREEN_CHANGE = "screen-change"
    GONE = "gone"
    APP = "app"
    IDLE = "idle"


@dataclass(frozen=True)
class ConnectCommand:
    connection: ConnectionConfig
    kind: CommandKind = field(default=CommandKind.CONNECT, init=False)


@dataclass(frozen=True)
class ObserveCommand:
    kind: CommandKind = field(default=CommandKind.OBSERVE, init=False)


@dataclass(frozen=True)
class ScreenshotCommand:
    kind: CommandKind = field(default=CommandKind.SCREENSHOT, init=False)


@dataclass(frozen=True)
class ListAppsCommand:
    kind: CommandKind = field(default=CommandKind.LIST_APPS, init=False)


class _TypedActionCommandMixin:
    kind: CommandKind


@dataclass(frozen=True)
class OpenCommand(_TypedActionCommandMixin):
    target: OpenAppTarget | OpenUrlTarget
    kind: CommandKind = field(default=CommandKind.OPEN, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.target, (OpenAppTarget, OpenUrlTarget)):
            raise ValueError("open requires typed target")


@dataclass(frozen=True)
class TapCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    kind: CommandKind = field(default=CommandKind.TAP, init=False)


@dataclass(frozen=True)
class LongTapCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    kind: CommandKind = field(default=CommandKind.LONG_TAP, init=False)


@dataclass(frozen=True)
class TypeCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    text: str
    kind: CommandKind = field(default=CommandKind.TYPE, init=False)


@dataclass(frozen=True)
class FocusCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    kind: CommandKind = field(default=CommandKind.FOCUS, init=False)


@dataclass(frozen=True)
class SubmitCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    kind: CommandKind = field(default=CommandKind.SUBMIT, init=False)


@dataclass(frozen=True)
class ScrollCommand(_TypedActionCommandMixin):
    ref: str
    source_screen_id: str
    direction: str
    kind: CommandKind = field(default=CommandKind.SCROLL, init=False)


@dataclass(frozen=True)
class GlobalCommand(_TypedActionCommandMixin):
    action: str
    source_screen_id: str | None = None
    kind: CommandKind = field(default=CommandKind.GLOBAL, init=False)


@dataclass(frozen=True, slots=True)
class TextWaitPredicate:
    text: str
    wait_kind: WaitKind = field(default=WaitKind.TEXT, init=False)


@dataclass(frozen=True, slots=True)
class ScreenChangeWaitPredicate:
    source_screen_id: str
    wait_kind: WaitKind = field(default=WaitKind.SCREEN_CHANGE, init=False)


@dataclass(frozen=True, slots=True)
class GoneWaitPredicate:
    source_screen_id: str
    ref: str
    wait_kind: WaitKind = field(default=WaitKind.GONE, init=False)


@dataclass(frozen=True, slots=True)
class AppWaitPredicate:
    package_name: str
    wait_kind: WaitKind = field(default=WaitKind.APP, init=False)


@dataclass(frozen=True, slots=True)
class IdleWaitPredicate:
    wait_kind: WaitKind = field(default=WaitKind.IDLE, init=False)


WaitPredicate: TypeAlias = (
    TextWaitPredicate
    | ScreenChangeWaitPredicate
    | GoneWaitPredicate
    | AppWaitPredicate
    | IdleWaitPredicate
)

WAIT_PREDICATE_TYPES = (
    TextWaitPredicate,
    ScreenChangeWaitPredicate,
    GoneWaitPredicate,
    AppWaitPredicate,
    IdleWaitPredicate,
)


@dataclass(frozen=True)
class WaitCommand:
    predicate: WaitPredicate
    timeout_ms: int | None = None
    kind: CommandKind = field(default=CommandKind.WAIT, init=False)

    def __post_init__(self) -> None:
        if not is_wait_predicate(self.predicate):
            raise ValueError("wait requires typed predicate")

    @property
    def wait_kind(self) -> WaitKind:
        return self.predicate.wait_kind


TypedActionCommand: TypeAlias = (
    OpenCommand
    | TapCommand
    | LongTapCommand
    | TypeCommand
    | FocusCommand
    | SubmitCommand
    | ScrollCommand
    | GlobalCommand
)

ActionCommand: TypeAlias = TypedActionCommand

REF_BOUND_ACTION_COMMAND_TYPES = (
    TapCommand,
    LongTapCommand,
    TypeCommand,
    FocusCommand,
    SubmitCommand,
    ScrollCommand,
)

RefBoundActionCommand: TypeAlias = (
    TapCommand
    | LongTapCommand
    | TypeCommand
    | FocusCommand
    | SubmitCommand
    | ScrollCommand
)

InternalCommand: TypeAlias = (
    ConnectCommand
    | ObserveCommand
    | ListAppsCommand
    | ActionCommand
    | WaitCommand
    | ScreenshotCommand
)


def wait_timeout_ms(command: WaitCommand) -> int | None:
    return command.timeout_ms


def is_wait_predicate(value: object) -> TypeGuard[WaitPredicate]:
    return isinstance(value, WAIT_PREDICATE_TYPES)


def is_ref_bound_action_command(command: object) -> TypeGuard[RefBoundActionCommand]:
    return isinstance(command, REF_BOUND_ACTION_COMMAND_TYPES)


__all__ = [
    "REF_BOUND_ACTION_COMMAND_TYPES",
    "ActionCommand",
    "AppWaitPredicate",
    "ConnectCommand",
    "FocusCommand",
    "GlobalCommand",
    "GoneWaitPredicate",
    "IdleWaitPredicate",
    "InternalCommand",
    "ListAppsCommand",
    "LongTapCommand",
    "ObserveCommand",
    "OpenAppTarget",
    "OpenCommand",
    "OpenUrlTarget",
    "RefBoundActionCommand",
    "ScreenChangeWaitPredicate",
    "ScreenshotCommand",
    "ScrollCommand",
    "SubmitCommand",
    "TapCommand",
    "TextWaitPredicate",
    "TypeCommand",
    "TypedActionCommand",
    "WaitCommand",
    "WaitKind",
    "WaitPredicate",
    "is_ref_bound_action_command",
    "is_wait_predicate",
    "wait_timeout_ms",
]
