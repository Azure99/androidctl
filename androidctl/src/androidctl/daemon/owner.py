from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

OWNER_ENV = "ANDROIDCTL_OWNER_ID"
DEFAULT_OWNER_HINT = "Set ANDROIDCTL_OWNER_ID explicitly."
_SHELL_PROCESS_NAMES = frozenset(
    {"bash", "zsh", "fish", "sh", "ksh", "dash", "tcsh", "csh"}
)


def derive_owner_id(*, env: Mapping[str, str]) -> str:
    configured = env.get(OWNER_ENV)
    if configured is not None:
        candidate = configured.strip()
        if candidate:
            return candidate
    shell_pid = _find_interactive_shell_ancestor_pid(env)
    if shell_pid is None:
        raise ValueError(
            "Unable to derive a safe owner identity automatically. "
            f"{DEFAULT_OWNER_HINT}"
        )
    lifetime = _read_process_lifetime_discriminator(shell_pid)
    if lifetime is None:
        raise ValueError(
            "Unable to derive a safe owner identity automatically. "
            f"{DEFAULT_OWNER_HINT}"
        )
    return f"shell:{shell_pid}:{lifetime}"


def _find_interactive_shell_ancestor_pid(env: Mapping[str, str]) -> int | None:
    del env
    current_pid = os.getpid()
    ancestor_pid = _read_parent_pid(current_pid)
    if ancestor_pid is None:
        return None
    seen: set[int] = {current_pid}
    hops = 0
    while ancestor_pid > 1 and hops < 64:
        if ancestor_pid in seen:
            return None
        seen.add(ancestor_pid)
        process_name = _read_process_name(ancestor_pid)
        if process_name is not None and process_name.casefold() in _SHELL_PROCESS_NAMES:
            interactivity = _read_shell_interactivity(ancestor_pid)
            if interactivity is None:
                return None
            if interactivity:
                return ancestor_pid
        next_pid = _read_parent_pid(ancestor_pid)
        if next_pid is None:
            return None
        ancestor_pid = next_pid
        hops += 1
    return None


def _read_shell_interactivity(pid: int) -> bool | None:
    tty_nr = _read_process_tty_nr(pid)
    if tty_nr is None:
        return None
    return tty_nr != "0"


def _read_process_name(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except (OSError, RuntimeError):
        return None


def _read_parent_pid(pid: int) -> int | None:
    parts = _read_process_stat_fields(pid)
    if parts is None:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def _read_process_lifetime_discriminator(pid: int) -> str | None:
    parts = _read_process_stat_fields(pid)
    if parts is None or len(parts) < 22:
        return None
    start_ticks = parts[21].strip()
    if not start_ticks:
        return None
    return start_ticks


def _read_process_tty_nr(pid: int) -> str | None:
    parts = _read_process_stat_fields(pid)
    if parts is None or len(parts) < 7:
        return None
    tty_nr = parts[6].strip()
    if not tty_nr:
        return None
    return tty_nr


def _read_process_stat_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    stat = raw.strip()
    first_space = stat.find(" ")
    comm_end = stat.rfind(")")
    if first_space <= 0 or comm_end <= first_space:
        return None
    pid_part = stat[:first_space]
    comm_part = stat[first_space + 1 : comm_end + 1]
    remainder = stat[comm_end + 1 :].strip()
    if not comm_part.startswith("("):
        return None
    parts = [pid_part, comm_part, *remainder.split()]
    if len(parts) < 4:
        return None
    return parts
