from __future__ import annotations

import pytest

import androidctl.daemon.owner as owner


class _FakeWinFunction:
    def __init__(self, implementation):
        self._implementation = implementation

    def __call__(self, *args):
        return self._implementation(*args)


class _FakeKernel32:
    def __init__(
        self,
        *,
        process_entries=None,
        snapshot_handle: int = 100,
        process_handle: int = 200,
        next_error: int = owner._ERROR_NO_MORE_FILES,
        get_process_times_ok: bool = True,
        creation_low: int = 1,
        creation_high: int = 2,
    ) -> None:
        self._process_entries = list(process_entries or [])
        self._snapshot_index = 0
        self.snapshot_handle = snapshot_handle
        self.process_handle = process_handle
        self.next_error = next_error
        self.get_process_times_ok = get_process_times_ok
        self.creation_low = creation_low
        self.creation_high = creation_high
        self.last_error = 0
        self.closed_handles: list[int] = []
        self.open_process_calls: list[tuple[int, bool, int]] = []
        self.CreateToolhelp32Snapshot = _FakeWinFunction(
            self._create_toolhelp32_snapshot
        )
        self.Process32FirstW = _FakeWinFunction(self._process32_first)
        self.Process32NextW = _FakeWinFunction(self._process32_next)
        self.OpenProcess = _FakeWinFunction(self._open_process)
        self.GetProcessTimes = _FakeWinFunction(self._get_process_times)
        self.CloseHandle = _FakeWinFunction(self._close_handle)

    def set_last_error(self, error_code: int) -> None:
        self.last_error = error_code

    def _create_toolhelp32_snapshot(self, _flags: int, _pid: int) -> int:
        return self.snapshot_handle

    def _process32_first(self, _snapshot: int, entry_ptr) -> bool:
        if not self._process_entries:
            return False
        self._snapshot_index = 0
        self._write_process_entry(entry_ptr, self._process_entries[0])
        return True

    def _process32_next(self, _snapshot: int, entry_ptr) -> bool:
        next_index = self._snapshot_index + 1
        if next_index >= len(self._process_entries):
            self.last_error = self.next_error
            return False
        self._snapshot_index = next_index
        self._write_process_entry(entry_ptr, self._process_entries[next_index])
        return True

    def _open_process(self, access: int, inherit: bool, pid: int) -> int:
        self.open_process_calls.append((access, inherit, pid))
        return self.process_handle

    def _get_process_times(
        self,
        _process: int,
        creation_time_ptr,
        _exit_time_ptr,
        _kernel_time_ptr,
        _user_time_ptr,
    ) -> bool:
        if not self.get_process_times_ok:
            return False
        creation_time = creation_time_ptr._obj
        creation_time.dwLowDateTime = self.creation_low
        creation_time.dwHighDateTime = self.creation_high
        return True

    def _close_handle(self, handle: int) -> bool:
        self.closed_handles.append(handle)
        return True

    def _write_process_entry(self, entry_ptr, entry) -> None:
        process_entry = entry_ptr._obj
        pid, parent_pid, process_name = entry
        process_entry.th32ProcessID = pid
        process_entry.th32ParentProcessID = parent_pid
        process_entry.szExeFile = process_name


def _patch_fake_kernel32(
    monkeypatch: pytest.MonkeyPatch,
    fake_kernel32: _FakeKernel32,
) -> None:
    monkeypatch.setattr(owner, "_load_windows_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(owner, "_set_windows_last_error", fake_kernel32.set_last_error)
    monkeypatch.setattr(
        owner, "_get_windows_last_error", lambda: fake_kernel32.last_error
    )


def test_owner_id_prefers_explicit_override() -> None:
    owner_id = owner.derive_owner_id(env={"ANDROIDCTL_OWNER_ID": "agent:codex:1"})

    assert owner_id == "agent:codex:1"


def test_owner_id_prefers_explicit_override_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "win32")
    monkeypatch.setattr(
        owner,
        "_derive_windows_owner_id",
        lambda: pytest.fail("explicit owner should bypass Windows derivation"),
    )

    owner_id = owner.derive_owner_id(env={"ANDROIDCTL_OWNER_ID": " agent:codex:1 "})

    assert owner_id == "agent:codex:1"


def test_owner_id_derives_shell_identity_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "linux")
    monkeypatch.delenv("ANDROIDCTL_OWNER_ID", raising=False)
    monkeypatch.setattr(owner, "_find_interactive_shell_ancestor_pid", lambda env: 3131)
    monkeypatch.setattr(owner, "_read_process_lifetime_discriminator", lambda pid: "42")

    owner_id = owner.derive_owner_id(env={})

    assert owner_id == "shell:3131:42"


def test_owner_id_fails_closed_when_no_safe_identity_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "linux")
    monkeypatch.setattr(owner, "_find_interactive_shell_ancestor_pid", lambda env: None)
    with pytest.raises(ValueError, match="ANDROIDCTL_OWNER_ID"):
        owner.derive_owner_id(env={})


def test_owner_id_derives_windows_shell_identity_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "win32")
    monkeypatch.setattr(owner, "_find_windows_shell_ancestor_pid", lambda: 3131)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_creation_filetime",
        lambda pid: "133485408000000000",
    )

    owner_id = owner.derive_owner_id(env={})

    assert owner_id == "shell:win32:3131:133485408000000000"


def test_owner_id_fails_closed_when_windows_creation_time_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "win32")
    monkeypatch.setattr(owner, "_find_windows_shell_ancestor_pid", lambda: 3131)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_creation_filetime",
        lambda pid: None,
    )

    with pytest.raises(ValueError, match="ANDROIDCTL_OWNER_ID"):
        owner.derive_owner_id(env={})


def test_owner_id_fails_closed_when_windows_shell_ancestor_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.sys, "platform", "win32")
    monkeypatch.setattr(owner, "_find_windows_shell_ancestor_pid", lambda: None)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_creation_filetime",
        lambda pid: pytest.fail("creation time should not be read without shell"),
    )

    with pytest.raises(ValueError, match="ANDROIDCTL_OWNER_ID"):
        owner.derive_owner_id(env={})


def test_windows_shell_ancestor_uses_nearest_allowed_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_table",
        lambda: {
            100: owner._WindowsProcessInfo(
                parent_pid=200,
                process_name="python.exe",
            ),
            200: owner._WindowsProcessInfo(
                parent_pid=300,
                process_name="conhost.exe",
            ),
            300: owner._WindowsProcessInfo(
                parent_pid=400,
                process_name=r"C:\Program Files\PowerShell\7\PwSh.ExE",
            ),
            400: owner._WindowsProcessInfo(
                parent_pid=0,
                process_name="explorer.exe",
            ),
        },
    )

    shell_pid = owner._find_windows_shell_ancestor_pid()

    assert shell_pid == 300


def test_windows_shell_ancestor_fails_closed_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_table",
        lambda: {
            100: owner._WindowsProcessInfo(
                parent_pid=200,
                process_name="python.exe",
            ),
            200: owner._WindowsProcessInfo(
                parent_pid=300,
                process_name="conhost.exe",
            ),
            300: owner._WindowsProcessInfo(
                parent_pid=0,
                process_name="explorer.exe",
            ),
        },
    )

    shell_pid = owner._find_windows_shell_ancestor_pid()

    assert shell_pid is None


def test_read_windows_process_table_reads_snapshot_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_entries=[
            (100, 50, "python.exe"),
            (50, 1, "pwsh.exe"),
        ],
        snapshot_handle=321,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    process_table = owner._read_windows_process_table()

    assert process_table == {
        100: owner._WindowsProcessInfo(
            parent_pid=50,
            process_name="python.exe",
        ),
        50: owner._WindowsProcessInfo(
            parent_pid=1,
            process_name="pwsh.exe",
        ),
    }
    assert fake_kernel32.closed_handles == [321]


def test_read_windows_process_table_fails_closed_on_invalid_snapshot_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_entries=[(100, 50, "python.exe")],
        snapshot_handle=owner._WINDOWS_INVALID_HANDLE_VALUE,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    process_table = owner._read_windows_process_table()

    assert process_table is None
    assert fake_kernel32.closed_handles == []


def test_read_windows_process_table_fails_closed_on_process32next_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_entries=[(100, 50, "python.exe")],
        snapshot_handle=321,
        next_error=5,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    process_table = owner._read_windows_process_table()

    assert process_table is None
    assert fake_kernel32.closed_handles == [321]


def test_read_windows_process_creation_filetime_reads_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_handle=654,
        creation_low=1,
        creation_high=2,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    creation_filetime = owner._read_windows_process_creation_filetime(3131)

    assert creation_filetime == str((2 << 32) | 1)
    assert fake_kernel32.open_process_calls == [
        (owner._PROCESS_QUERY_LIMITED_INFORMATION, False, 3131)
    ]
    assert fake_kernel32.closed_handles == [654]


def test_read_windows_process_creation_filetime_fails_closed_when_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(process_handle=0)
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    creation_filetime = owner._read_windows_process_creation_filetime(3131)

    assert creation_filetime is None
    assert fake_kernel32.closed_handles == []


def test_read_windows_process_creation_filetime_fails_closed_when_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_handle=654,
        get_process_times_ok=False,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    creation_filetime = owner._read_windows_process_creation_filetime(3131)

    assert creation_filetime is None
    assert fake_kernel32.closed_handles == [654]


def test_read_windows_process_creation_filetime_fails_closed_when_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = _FakeKernel32(
        process_handle=654,
        creation_low=0,
        creation_high=0,
    )
    _patch_fake_kernel32(monkeypatch, fake_kernel32)

    creation_filetime = owner._read_windows_process_creation_filetime(3131)

    assert creation_filetime is None
    assert fake_kernel32.closed_handles == [654]


def test_windows_shell_ancestor_fails_closed_on_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        owner,
        "_read_windows_process_table",
        lambda: {
            100: owner._WindowsProcessInfo(
                parent_pid=200,
                process_name="python.exe",
            ),
            200: owner._WindowsProcessInfo(
                parent_pid=300,
                process_name="conhost.exe",
            ),
            300: owner._WindowsProcessInfo(
                parent_pid=200,
                process_name="python.exe",
            ),
        },
    )

    shell_pid = owner._find_windows_shell_ancestor_pid()

    assert shell_pid is None


def test_windows_shell_ancestor_fails_closed_after_hop_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_pid = 1000
    first_parent_pid = current_pid + 1
    process_table = {
        current_pid: owner._WindowsProcessInfo(
            parent_pid=first_parent_pid,
            process_name="python.exe",
        )
    }
    for pid in range(
        first_parent_pid,
        first_parent_pid + owner._MAX_OWNER_PROCESS_HOPS,
    ):
        process_table[pid] = owner._WindowsProcessInfo(
            parent_pid=pid + 1,
            process_name="python.exe",
        )
    process_table[first_parent_pid + owner._MAX_OWNER_PROCESS_HOPS] = (
        owner._WindowsProcessInfo(
            parent_pid=0,
            process_name="pwsh.exe",
        )
    )
    monkeypatch.setattr(owner.os, "getpid", lambda: current_pid)
    monkeypatch.setattr(owner, "_read_windows_process_table", lambda: process_table)

    shell_pid = owner._find_windows_shell_ancestor_pid()

    assert shell_pid is None
