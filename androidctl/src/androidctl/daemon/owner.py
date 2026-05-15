from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OWNER_ENV = "ANDROIDCTL_OWNER_ID"
WINDOWS_OWNER_ANCHOR_ENV = "ANDROIDCTL_OWNER_ANCHOR_PROCESSES"
DEFAULT_OWNER_HINT = "Set ANDROIDCTL_OWNER_ID explicitly."
_MAX_OWNER_PROCESS_HOPS = 64
_SHELL_PROCESS_NAMES = frozenset(
    {"bash", "zsh", "fish", "sh", "ksh", "dash", "tcsh", "csh"}
)
_WINDOWS_SHELL_PROCESS_NAMES = frozenset(
    {"bash.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "sh.exe"}
)
_DEFAULT_WINDOWS_OWNER_ANCHOR_PROCESS_NAMES = frozenset({"claude.exe", "codex.exe"})
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_NO_MORE_FILES = 18
_WINDOWS_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class _WindowsProcessInfo:
    parent_pid: int
    process_name: str


@dataclass(frozen=True)
class _WindowsAncestorProcess:
    pid: int
    process_name: str


@dataclass(frozen=True)
class _WindowsOwnerTarget:
    kind: str
    pid: int
    process_name: str


class _WindowsFileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _WindowsProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH),
    ]


def derive_owner_id(*, env: Mapping[str, str]) -> str:
    configured = env.get(OWNER_ENV)
    if configured is not None:
        candidate = configured.strip()
        if candidate:
            return candidate
    if _is_windows_platform():
        owner_id = _derive_windows_owner_id(env=env)
        if owner_id is None:
            raise ValueError(
                "Unable to derive a safe owner identity automatically. "
                f"{DEFAULT_OWNER_HINT}"
            )
        return owner_id
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


def _is_windows_platform() -> bool:
    return sys.platform == "win32"


def _find_interactive_shell_ancestor_pid(env: Mapping[str, str]) -> int | None:
    del env
    current_pid = os.getpid()
    ancestor_pid = _read_parent_pid(current_pid)
    if ancestor_pid is None:
        return None
    seen: set[int] = {current_pid}
    hops = 0
    while ancestor_pid > 1 and hops < _MAX_OWNER_PROCESS_HOPS:
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


def _derive_windows_owner_id(*, env: Mapping[str, str]) -> str | None:
    process_table = _read_windows_process_table()
    if process_table is None:
        return None
    for target in _find_windows_owner_targets(process_table, env=env):
        lifetime = _read_windows_process_creation_filetime(target.pid)
        if lifetime is None:
            continue
        if target.kind == "agent":
            return f"agent:win32:{target.process_name}:{target.pid}:{lifetime}"
        return f"shell:win32:{target.pid}:{lifetime}"
    return None


def _find_windows_owner_targets(
    process_table: dict[int, _WindowsProcessInfo],
    *,
    env: Mapping[str, str],
) -> list[_WindowsOwnerTarget]:
    anchor_names = _windows_owner_anchor_process_names(env)
    agent_targets: list[_WindowsOwnerTarget] = []
    shell_target: _WindowsOwnerTarget | None = None
    for ancestor in _windows_ancestor_chain(process_table):
        if ancestor.process_name in anchor_names:
            agent_targets.append(
                _WindowsOwnerTarget(
                    kind="agent",
                    pid=ancestor.pid,
                    process_name=ancestor.process_name,
                )
            )
        if (
            shell_target is None
            and ancestor.process_name in _WINDOWS_SHELL_PROCESS_NAMES
        ):
            shell_target = _WindowsOwnerTarget(
                kind="shell",
                pid=ancestor.pid,
                process_name=ancestor.process_name,
            )
    if shell_target is not None:
        return [*agent_targets, shell_target]
    return agent_targets


def _windows_ancestor_chain(
    process_table: dict[int, _WindowsProcessInfo],
) -> list[_WindowsAncestorProcess]:
    current_pid = os.getpid()
    current = process_table.get(current_pid)
    if current is None:
        return []
    ancestor_pid = current.parent_pid
    seen: set[int] = {current_pid}
    ancestors: list[_WindowsAncestorProcess] = []
    hops = 0
    while ancestor_pid > 0 and hops < _MAX_OWNER_PROCESS_HOPS:
        if ancestor_pid in seen:
            return []
        seen.add(ancestor_pid)
        ancestor = process_table.get(ancestor_pid)
        if ancestor is None:
            break
        ancestors.append(
            _WindowsAncestorProcess(
                pid=ancestor_pid,
                process_name=_normalize_windows_process_name(ancestor.process_name),
            )
        )
        ancestor_pid = ancestor.parent_pid
        hops += 1
    return ancestors


def _windows_owner_anchor_process_names(env: Mapping[str, str]) -> set[str]:
    process_names = set(_DEFAULT_WINDOWS_OWNER_ANCHOR_PROCESS_NAMES)
    configured = env.get(WINDOWS_OWNER_ANCHOR_ENV)
    if configured is None:
        return process_names
    for raw_name in configured.replace(";", ",").split(","):
        normalized = _normalize_windows_process_name(raw_name)
        if normalized:
            process_names.add(normalized)
    return process_names


def _find_windows_shell_ancestor_pid() -> int | None:
    process_table = _read_windows_process_table()
    if process_table is None:
        return None
    target = _find_windows_shell_ancestor(process_table)
    if target is None:
        return None
    return target.pid


def _find_windows_shell_ancestor(
    process_table: dict[int, _WindowsProcessInfo],
) -> _WindowsOwnerTarget | None:
    for ancestor in _windows_ancestor_chain(process_table):
        if ancestor.process_name in _WINDOWS_SHELL_PROCESS_NAMES:
            return _WindowsOwnerTarget(
                kind="shell",
                pid=ancestor.pid,
                process_name=ancestor.process_name,
            )
    return None


def _normalize_windows_process_name(process_name: str) -> str:
    normalized = process_name.strip().replace("/", "\\")
    return normalized.rsplit("\\", maxsplit=1)[-1].casefold()


def _read_windows_process_table() -> dict[int, _WindowsProcessInfo] | None:
    kernel32 = _load_windows_kernel32()
    if kernel32 is None:
        return None
    _configure_windows_process_snapshot_functions(kernel32)
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    snapshot_value = _windows_handle_value(snapshot)
    if snapshot_value is None or snapshot_value == _WINDOWS_INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _WindowsProcessEntry32()
        entry.dwSize = ctypes.sizeof(_WindowsProcessEntry32)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        process_table: dict[int, _WindowsProcessInfo] = {}
        while True:
            pid = int(entry.th32ProcessID)
            if pid > 0:
                process_table[pid] = _WindowsProcessInfo(
                    parent_pid=int(entry.th32ParentProcessID),
                    process_name=str(entry.szExeFile),
                )
            _set_windows_last_error(0)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                if _get_windows_last_error() != _ERROR_NO_MORE_FILES:
                    return None
                break
        return process_table
    finally:
        kernel32.CloseHandle(snapshot)


def _read_windows_process_creation_filetime(pid: int) -> str | None:
    kernel32 = _load_windows_kernel32()
    if kernel32 is None:
        return None
    _configure_windows_process_time_functions(kernel32)
    process = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    process_value = _windows_handle_value(process)
    if process_value is None or process_value == 0:
        return None
    try:
        creation_time = _WindowsFileTime()
        exit_time = _WindowsFileTime()
        kernel_time = _WindowsFileTime()
        user_time = _WindowsFileTime()
        ok = kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation_time),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        filetime = (int(creation_time.dwHighDateTime) << 32) | int(
            creation_time.dwLowDateTime
        )
        if filetime <= 0:
            return None
        return str(filetime)
    finally:
        kernel32.CloseHandle(process)


def _load_windows_kernel32() -> Any | None:
    windll = getattr(ctypes, "WinDLL", None)
    if windll is None:
        return None
    try:
        return windll("kernel32", use_last_error=True)
    except OSError:
        return None


def _set_windows_last_error(error_code: int) -> None:
    setter = getattr(ctypes, "set_last_error", None)
    if setter is not None:
        setter(error_code)


def _get_windows_last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    return int(getter())


def _configure_windows_process_snapshot_functions(kernel32: Any) -> None:
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsProcessEntry32),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsProcessEntry32),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _configure_windows_process_time_functions(kernel32: Any) -> None:
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsFileTime),
        ctypes.POINTER(_WindowsFileTime),
        ctypes.POINTER(_WindowsFileTime),
        ctypes.POINTER(_WindowsFileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _windows_handle_value(handle: object) -> int | None:
    if handle is None:
        return None
    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", None)
    if isinstance(value, int):
        return value
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
