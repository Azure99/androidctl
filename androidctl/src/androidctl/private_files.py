from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO

from .protocol import Fault


def state_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "AndroidCtl"
    return Path.home() / ".local" / "state" / "androidctl"


class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", ctypes.c_void_p),
        ("inherit", wintypes.BOOL),
    ]


if sys.platform == "win32":
    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    SDDL_REVISION_1 = 1
    SE_FILE_OBJECT = 1
    ACL_SIZE_INFORMATION = 2
    ACCESS_ALLOWED_ACE_TYPE = 0
    INHERIT_ONLY_ACE = 0x08
    FILE_ALL_ACCESS = 0x1F01FF
    OWNER_SECURITY_INFORMATION = 0x00000001
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    ERROR_ALREADY_EXISTS = 183

    @cache
    def _windows_libraries() -> tuple[Any, Any]:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        advapi.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            pointer,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.ConvertSidToStringSidW.argtypes = [
            pointer,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(pointer),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(pointer),
            pointer,
            ctypes.POINTER(pointer),
            pointer,
            ctypes.POINTER(pointer),
        ]
        advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi.GetAclInformation.argtypes = [
            pointer,
            pointer,
            wintypes.DWORD,
            ctypes.c_int,
        ]
        advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
        advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.LocalFree.argtypes = [pointer]
        kernel.LocalFree.restype = pointer
        kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, pointer]
        return advapi, kernel

    def _sid_text(advapi: Any, kernel: Any, sid: Any) -> str:
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return str(value.value)
        finally:
            kernel.LocalFree(value)

    def _current_sid(advapi: Any, kernel: Any) -> str:
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(
            kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
            data = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(
                token, TOKEN_USER, data, size, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            return _sid_text(advapi, kernel, sid)
        finally:
            kernel.CloseHandle(token)

    @contextmanager
    def private_security(*, directory: bool = False) -> Iterator[SecurityAttributes]:
        advapi, kernel = _windows_libraries()
        sid = _current_sid(advapi, kernel)
        descriptor = ctypes.c_void_p()
        inheritance = "OICI" if directory else ""
        sddl = f"O:{sid}D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield SecurityAttributes(
                ctypes.sizeof(SecurityAttributes), descriptor, False
            )
        finally:
            kernel.LocalFree(descriptor)


def check_private(path: Path) -> None:
    if sys.platform != "win32":
        info = path.stat()
        uid = os.getuid()
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o077:
            raise Fault(
                "privatePermissions",
                f"Private POSIX owner/mode required for {path}: "
                f"owner UID={info.st_uid}, expected UID={uid}, "
                f"mode={oct(stat.S_IMODE(info.st_mode))}; "
                "group/other permission bits must be zero",
            )
    else:
        advapi, kernel = _windows_libraries()
        sid = _current_sid(advapi, kernel)
        owner, acl, descriptor = (ctypes.c_void_p() for _ in range(3))
        error = advapi.GetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(acl),
            None,
            ctypes.byref(descriptor),
        )
        if error:
            raise ctypes.WinError(error)
        try:
            if not acl.value or _sid_text(advapi, kernel, owner) != sid:
                raise Fault(
                    "privatePermissions",
                    f"Windows private DACL and current owner required: {path}",
                )
            information = (wintypes.DWORD * 3)()
            if not advapi.GetAclInformation(
                acl, information, ctypes.sizeof(information), ACL_SIZE_INFORMATION
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            own_access = False
            for index in range(information[0]):
                ace = ctypes.c_void_p()
                if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                address = int(ace.value or 0)
                header = ctypes.string_at(address, 4)
                if header[0] != ACCESS_ALLOWED_ACE_TYPE:
                    raise Fault(
                        "privatePermissions",
                        f"Unexpected Windows DACL access entry: {path}",
                    )
                mask = ctypes.c_uint32.from_address(address + 4).value
                principal = _sid_text(advapi, kernel, ctypes.c_void_p(address + 8))
                if principal not in (sid, "S-1-5-18"):
                    raise Fault(
                        "privatePermissions",
                        f"Unexpected Windows DACL access entry: {path}",
                    )
                own_access |= (
                    principal == sid
                    and mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS
                    and not header[1] & INHERIT_ONLY_ACE
                )
            if not own_access:
                raise Fault(
                    "privatePermissions",
                    f"Windows DACL must grant current-user full access: {path}",
                )
        finally:
            kernel.LocalFree(descriptor)


def private_directory(path: Path) -> None:
    if path.exists():
        check_private(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        path.mkdir(mode=0o700, exist_ok=True)
    else:
        _, kernel = _windows_libraries()
        with private_security(directory=True) as attributes:
            if (
                not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
                and ctypes.get_last_error() != ERROR_ALREADY_EXISTS
            ):
                raise ctypes.WinError(ctypes.get_last_error())
    check_private(path)


class PublishedWriteError(OSError):
    """Publication succeeded; subsequent synchronization or resource release failed."""


def atomic_write(
    path: Path,
    data: bytes,
    *,
    publish: Callable[[Path, Path], None] | None = None,
) -> None:
    # A publisher that fails after replacement must raise PublishedWriteError.
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            protect_created_file(temporary)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        (publish or os.replace)(temporary, path)
        published = True
        if sys.platform != "win32":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        if published:
            raise PublishedWriteError(str(error)) from error
        raise
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


def protect_created_file(path: Path) -> None:
    """Apply the current-user descriptor to a new, empty, tool-owned file."""
    if sys.platform != "win32":
        path.chmod(0o600)
    else:
        advapi, _ = _windows_libraries()
        with private_security() as attributes:
            if not advapi.SetFileSecurityW(
                str(path),
                OWNER_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION
                | PROTECTED_DACL_SECURITY_INFORMATION,
                attributes.descriptor,
            ):
                raise ctypes.WinError(ctypes.get_last_error())


def write_json(
    path: Path,
    value: dict[str, Any],
    *,
    publish: Callable[[Path, Path], None] | None = None,
) -> None:
    atomic_write(
        path, json.dumps(value, ensure_ascii=False).encode("utf-8"), publish=publish
    )


class LifetimeLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream: BinaryIO | None = None

    def acquire(self) -> bool:
        stream = self.path.open("a+b")
        try:
            stream.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                conflict = (
                    errno.EACCES if sys.platform == "win32" else errno.EWOULDBLOCK
                )
                if error.errno != conflict:
                    raise
                stream.close()
                return False
        except OSError:
            stream.close()
            raise
        self.stream = stream
        return True

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
