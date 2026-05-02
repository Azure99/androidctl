"""Helpers for daemon/public semantic command naming boundaries."""

from __future__ import annotations

from androidctl_contracts.command_catalog import entry_for_daemon_kind
from androidctld.protocol import CommandKind


def semantic_result_command_for_daemon_kind(kind: CommandKind | str) -> str:
    normalized_kind = kind.value if isinstance(kind, CommandKind) else str(kind)
    entry = entry_for_daemon_kind(normalized_kind)
    if entry is None:
        return normalized_kind
    return entry.result_command


__all__ = ["semantic_result_command_for_daemon_kind"]
