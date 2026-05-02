"""Local shared command catalog used to sync routing and result shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .vocabulary import (
    PublicResultCategory,
    PublicResultFamily,
    RetainedEnvelopeKind,
)

CommandRoute = Literal["commands_run", "runtime_close"]


@dataclass(frozen=True)
class CommandCatalogEntry:
    public_name: str
    route: CommandRoute
    daemon_kind: str | None
    result_command: str
    result_family: PublicResultFamily
    result_category: PublicResultCategory | None
    retained_envelope_kind: RetainedEnvelopeKind | None = None

    def __post_init__(self) -> None:
        if self.result_family is PublicResultFamily.SEMANTIC:
            if self.result_category is None:
                raise ValueError("semantic result family requires result_category")
            if self.retained_envelope_kind is not None:
                raise ValueError(
                    "semantic result family forbids retained_envelope_kind"
                )
        elif self.result_family is PublicResultFamily.RETAINED:
            if self.retained_envelope_kind is None:
                raise ValueError(
                    "retained result family requires retained_envelope_kind"
                )
            if self.result_category is not None:
                raise ValueError("retained result family forbids result_category")
        elif self.result_family is PublicResultFamily.LIST_APPS:
            if self.result_category is not None:
                raise ValueError("listApps result family forbids result_category")
            if self.retained_envelope_kind is not None:
                raise ValueError(
                    "listApps result family forbids retained_envelope_kind"
                )
        else:
            raise ValueError(f"unsupported result family: {self.result_family!r}")


_COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    CommandCatalogEntry(
        public_name="connect",
        route="commands_run",
        daemon_kind="connect",
        result_command="connect",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.BOOTSTRAP,
    ),
    CommandCatalogEntry(
        public_name="observe",
        route="commands_run",
        daemon_kind="observe",
        result_command="observe",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OBSERVE,
    ),
    CommandCatalogEntry(
        public_name="open",
        route="commands_run",
        daemon_kind="open",
        result_command="open",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OPEN,
    ),
    CommandCatalogEntry(
        public_name="tap",
        route="commands_run",
        daemon_kind="tap",
        result_command="tap",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="long-tap",
        route="commands_run",
        daemon_kind="longTap",
        result_command="long-tap",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="focus",
        route="commands_run",
        daemon_kind="focus",
        result_command="focus",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="type",
        route="commands_run",
        daemon_kind="type",
        result_command="type",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="submit",
        route="commands_run",
        daemon_kind="submit",
        result_command="submit",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="scroll",
        route="commands_run",
        daemon_kind="scroll",
        result_command="scroll",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="back",
        route="commands_run",
        daemon_kind="back",
        result_command="back",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="home",
        route="commands_run",
        daemon_kind="home",
        result_command="home",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="recents",
        route="commands_run",
        daemon_kind="recents",
        result_command="recents",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="notifications",
        route="commands_run",
        daemon_kind="notifications",
        result_command="notifications",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="wait",
        route="commands_run",
        daemon_kind="wait",
        result_command="wait",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.WAIT,
    ),
    CommandCatalogEntry(
        public_name="list-apps",
        route="commands_run",
        daemon_kind="listApps",
        result_command="list-apps",
        result_family=PublicResultFamily.LIST_APPS,
        result_category=None,
    ),
    CommandCatalogEntry(
        public_name="screenshot",
        route="commands_run",
        daemon_kind="screenshot",
        result_command="screenshot",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.ARTIFACT,
    ),
    CommandCatalogEntry(
        public_name="close",
        route="runtime_close",
        daemon_kind=None,
        result_command="close",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.LIFECYCLE,
    ),
)


def _build_unique_entry_index(
    entries: tuple[CommandCatalogEntry, ...],
    *,
    field_name: str,
) -> dict[str, CommandCatalogEntry]:
    index: dict[str, CommandCatalogEntry] = {}
    for entry in entries:
        key = getattr(entry, field_name)
        if key is None:
            continue
        if key in index:
            raise RuntimeError(f"duplicate command catalog {field_name}: {key!r}")
        index[key] = entry
    return index


_ENTRY_BY_PUBLIC_NAME = _build_unique_entry_index(
    _COMMAND_CATALOG,
    field_name="public_name",
)
_ENTRY_BY_DAEMON_KIND = _build_unique_entry_index(
    _COMMAND_CATALOG,
    field_name="daemon_kind",
)
_ENTRY_BY_RESULT_COMMAND = _build_unique_entry_index(
    _COMMAND_CATALOG,
    field_name="result_command",
)
_ENTRY_BY_RETAINED_RESULT_COMMAND = {
    command: entry
    for command, entry in _ENTRY_BY_RESULT_COMMAND.items()
    if entry.result_family is PublicResultFamily.RETAINED
}
_ENTRY_BY_SEMANTIC_RESULT_COMMAND = {
    command: entry
    for command, entry in _ENTRY_BY_RESULT_COMMAND.items()
    if entry.result_family is PublicResultFamily.SEMANTIC
}
_ENTRY_BY_LIST_APPS_RESULT_COMMAND = {
    command: entry
    for command, entry in _ENTRY_BY_RESULT_COMMAND.items()
    if entry.result_family is PublicResultFamily.LIST_APPS
}

PUBLIC_COMMAND_NAMES = set(_ENTRY_BY_PUBLIC_NAME)
DAEMON_COMMAND_KINDS = set(_ENTRY_BY_DAEMON_KIND)
SEMANTIC_RESULT_COMMAND_NAMES = set(_ENTRY_BY_SEMANTIC_RESULT_COMMAND)
RETAINED_RESULT_COMMAND_NAMES = set(_ENTRY_BY_RETAINED_RESULT_COMMAND)
LIST_APPS_RESULT_COMMAND_NAMES = set(_ENTRY_BY_LIST_APPS_RESULT_COMMAND)
RESULT_COMMAND_NAMES = set(_ENTRY_BY_RESULT_COMMAND)


def entries_for_route(route: CommandRoute) -> tuple[CommandCatalogEntry, ...]:
    return tuple(entry for entry in _COMMAND_CATALOG if entry.route == route)


def daemon_command_kinds_for_route(route: CommandRoute) -> frozenset[str]:
    return frozenset(
        entry.daemon_kind
        for entry in entries_for_route(route)
        if entry.daemon_kind is not None
    )


def runtime_close_entry() -> CommandCatalogEntry:
    entries = entries_for_route("runtime_close")
    if len(entries) != 1:
        raise RuntimeError(
            f"expected exactly one runtime_close command, found {len(entries)}"
        )
    entry = entries[0]
    if entry.daemon_kind is not None:
        raise RuntimeError("runtime_close command must not have a daemon kind")
    return entry


def entry_for_public_command(name: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_PUBLIC_NAME.get(name)


def entry_for_daemon_kind(kind: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_DAEMON_KIND.get(kind)


def entry_for_result_command(command: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_RESULT_COMMAND.get(command)


def entry_for_retained_result_command(command: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_RETAINED_RESULT_COMMAND.get(command)


def entry_for_semantic_result_command(command: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_SEMANTIC_RESULT_COMMAND.get(command)


def entry_for_list_apps_result_command(command: str) -> CommandCatalogEntry | None:
    return _ENTRY_BY_LIST_APPS_RESULT_COMMAND.get(command)


def daemon_kind_for_public_command(name: str) -> str | None:
    entry = entry_for_public_command(name)
    if entry is None:
        return None
    return entry.daemon_kind


def public_command_for_daemon_kind(kind: str) -> str | None:
    entry = entry_for_daemon_kind(kind)
    if entry is None:
        return None
    return entry.public_name


def result_category_for_public_command(
    name: str,
) -> PublicResultCategory | None:
    entry = entry_for_public_command(name)
    if entry is None:
        return None
    return entry.result_category


def result_family_for_public_command(
    name: str,
) -> PublicResultFamily | None:
    entry = entry_for_public_command(name)
    if entry is None:
        return None
    return entry.result_family


def result_family_for_daemon_kind(kind: str) -> PublicResultFamily | None:
    entry = entry_for_daemon_kind(kind)
    if entry is None:
        return None
    return entry.result_family


def result_family_for_command(command: str) -> PublicResultFamily | None:
    entry = entry_for_result_command(command)
    if entry is None:
        return None
    return entry.result_family


def result_category_for_command(command: str) -> PublicResultCategory | None:
    entry = entry_for_semantic_result_command(command)
    if entry is None:
        return None
    return entry.result_category


def retained_envelope_kind_for_public_command(
    name: str,
) -> RetainedEnvelopeKind | None:
    entry = entry_for_public_command(name)
    if entry is None:
        return None
    return entry.retained_envelope_kind


def retained_envelope_kind_for_command(command: str) -> RetainedEnvelopeKind | None:
    entry = entry_for_retained_result_command(command)
    if entry is None:
        return None
    return entry.retained_envelope_kind


def is_public_command(name: str) -> bool:
    return entry_for_public_command(name) is not None


def is_daemon_command_kind(kind: str) -> bool:
    return entry_for_daemon_kind(kind) is not None


def is_semantic_result_command(command: str) -> bool:
    return entry_for_semantic_result_command(command) is not None


def is_retained_result_command(command: str) -> bool:
    return entry_for_retained_result_command(command) is not None


def is_list_apps_result_command(command: str) -> bool:
    return entry_for_list_apps_result_command(command) is not None


__all__ = [
    "DAEMON_COMMAND_KINDS",
    "LIST_APPS_RESULT_COMMAND_NAMES",
    "PUBLIC_COMMAND_NAMES",
    "RETAINED_RESULT_COMMAND_NAMES",
    "RESULT_COMMAND_NAMES",
    "SEMANTIC_RESULT_COMMAND_NAMES",
    "CommandCatalogEntry",
    "CommandRoute",
    "daemon_kind_for_public_command",
    "entry_for_daemon_kind",
    "entry_for_list_apps_result_command",
    "entry_for_public_command",
    "entry_for_retained_result_command",
    "entry_for_result_command",
    "entry_for_semantic_result_command",
    "is_daemon_command_kind",
    "is_list_apps_result_command",
    "is_public_command",
    "is_retained_result_command",
    "is_semantic_result_command",
    "public_command_for_daemon_kind",
    "retained_envelope_kind_for_command",
    "retained_envelope_kind_for_public_command",
    "result_category_for_command",
    "result_category_for_public_command",
    "result_family_for_command",
    "result_family_for_daemon_kind",
    "result_family_for_public_command",
]
