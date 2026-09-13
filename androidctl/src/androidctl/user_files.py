from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO, cast

if sys.platform == "win32":
    from .private_files import PublishedWriteError

    GENERIC_READ = 0x80000000
    DELETE = 0x00010000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_RENAME_INFO_EX = 22
    FILE_RENAME_REPLACE_IF_EXISTS = 0x1
    FILE_RENAME_POSIX_SEMANTICS = 0x2

    @cache
    def _kernel() -> Any:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        return kernel

    def _open(
        kernel: Any,
        path: Path,
        *,
        delete: bool = False,
    ) -> int:
        access = GENERIC_READ
        if delete:
            access |= DELETE
        handle = kernel.CreateFileW(
            str(path),
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    class _RenameInfo(ctypes.Structure):
        _fields_ = [
            ("flags", wintypes.DWORD),
            ("root", wintypes.HANDLE),
            ("length", wintypes.DWORD),
            ("name", wintypes.WCHAR * 1),
        ]

    def replace_shared(source: Path, target: Path) -> None:
        encoded = str(target.absolute()).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(ctypes.sizeof(_RenameInfo) + len(encoded))
        info = _RenameInfo.from_buffer(buffer)
        info.flags = FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS
        info.length = len(encoded)
        ctypes.memmove(
            ctypes.addressof(buffer) + _RenameInfo.name.offset, encoded, len(encoded)
        )
        kernel = _kernel()
        handle = _open(kernel, source, delete=True)
        try:
            if not kernel.SetFileInformationByHandle(
                handle, FILE_RENAME_INFO_EX, buffer, len(buffer)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as error:
            if not kernel.CloseHandle(handle):
                raise OSError(
                    f"{error}; publisher handle release failed: "
                    f"{ctypes.WinError(ctypes.get_last_error())}"
                ) from error
            raise
        if not kernel.CloseHandle(handle):
            raise PublishedWriteError(str(ctypes.WinError(ctypes.get_last_error())))


def open_read(path: Path) -> BinaryIO:
    if sys.platform == "win32":
        import msvcrt

        def opener(name: str, _flags: int) -> int:
            kernel = _kernel()
            handle = _open(kernel, Path(name))
            try:
                return msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
            except BaseException as error:
                if not kernel.CloseHandle(handle):
                    raise OSError(
                        f"{error}; reader handle release failed: "
                        f"{ctypes.WinError(ctypes.get_last_error())}"
                    ) from error
                raise

        return cast(BinaryIO, open(path, "rb", opener=opener))
    else:
        return path.open("rb")
