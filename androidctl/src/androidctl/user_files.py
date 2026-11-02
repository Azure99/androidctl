from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Callable
from contextlib import nullcontext
from ctypes import wintypes
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO, cast

from . import paths
from .protocol import Fault

RENAME_NOREPLACE = 1
RENAME_EXCL = 4


@dataclass(frozen=True)
class FileIdentity:
    filesystem: int
    file_id: int | str


DestinationKey = tuple[FileIdentity, str]


def create(directory: Path, name: str, data: bytes) -> None:
    parent = Directory(directory)
    owned = None
    try:
        try:
            stream, owned = parent.create(name)
            with stream as output:
                write_all(output, data)
        except BaseException as error:
            if owned is not None:
                try:
                    if not parent.remove(name, owned):
                        raise Fault(
                            "fileCleanup",
                            "Target identity changed; target left untouched",
                        )
                except (Fault, OSError) as cleanup:
                    raise Fault(
                        "fileCleanup",
                        f"{error}; file cleanup unconfirmed at {directory / name}; "
                        f"cleanup failed: {cleanup}",
                    ) from error
            raise
    finally:
        parent.close()


def write_all(stream: BinaryIO, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        if not written:
            raise OSError("File write made no progress")
        offset += written


def require_new_file(path: Path, exists_message: str, parent_error: Fault) -> None:
    file_path(str(path))
    if paths.exists(path):
        raise Fault("fileExists", exists_message)
    if not paths.is_dir(path.parent):
        raise parent_error


def file_path(value: str) -> Path:
    if not value or "\0" in value:
        raise Fault("filePath", "Supply a path to one file")
    path = Path(os.path.abspath(value))
    if not path.name or (sys.platform == "win32" and ":" in path.name):
        raise Fault(
            "filePath", "Directories and alternate data streams are unsupported"
        )
    return path


class _FileId(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_uint64), ("identifier", ctypes.c_ubyte * 16)]


class _Attributes(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


if sys.platform == "win32":
    from .private_files import PublishedWriteError, private_security

    FILE_ID_INFO = 18
    FILE_ATTRIBUTE_TAG_INFO = 9
    FILE_DISPOSITION_INFO = 4
    FILE_READ_ATTRIBUTES = 0x00000080
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    DELETE = 0x00010000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_TYPE_DISK = 1
    FILE_NAME_NORMALIZED = 0
    VOLUME_NAME_DOS = 0

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
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        return kernel

    def _info(kernel: Any, handle: int) -> tuple[FileIdentity, int]:
        identifier, attributes = _FileId(), _Attributes()
        for kind, value in (
            (FILE_ID_INFO, identifier),
            (FILE_ATTRIBUTE_TAG_INFO, attributes),
        ):
            if not kernel.GetFileInformationByHandleEx(
                handle, kind, ctypes.byref(value), ctypes.sizeof(value)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        return (
            FileIdentity(identifier.volume, bytes(identifier.identifier).hex()),
            attributes.attributes,
        )

    def _open(
        kernel: Any,
        path: Path,
        *,
        directory: bool = False,
        create: bool = False,
        delete: bool = False,
    ) -> int:
        access = (
            FILE_READ_ATTRIBUTES
            if directory
            else GENERIC_READ | GENERIC_WRITE
            if create
            else GENERIC_READ
        )
        if delete or create:
            access |= DELETE
        with private_security() if create else nullcontext() as security:
            handle = kernel.CreateFileW(
                str(path),
                access,
                FILE_SHARE_READ
                | FILE_SHARE_WRITE
                | (0 if directory else FILE_SHARE_DELETE),
                ctypes.byref(security) if security is not None else None,
                CREATE_NEW if create else OPEN_EXISTING,
                (
                    FILE_FLAG_BACKUP_SEMANTICS
                    if directory
                    else FILE_FLAG_OPEN_REPARSE_POINT
                ),
                None,
            )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    FILE_RENAME_INFO_EX = 22
    FILE_RENAME_REPLACE_IF_EXISTS = 0x1
    FILE_RENAME_POSIX_SEMANTICS = 0x2

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

    def _delete_handle(kernel: Any, handle: int) -> None:
        delete = ctypes.c_ubyte(1)
        if not kernel.SetFileInformationByHandle(
            handle, FILE_DISPOSITION_INFO, ctypes.byref(delete), ctypes.sizeof(delete)
        ):
            raise ctypes.WinError(ctypes.get_last_error())


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


def identity(stream: BinaryIO) -> FileIdentity:
    if sys.platform == "win32":
        import msvcrt

        return _info(_kernel(), msvcrt.get_osfhandle(stream.fileno()))[0]
    else:
        value = os.fstat(stream.fileno())
        return FileIdentity(value.st_dev, value.st_ino)


class Directory:
    """Own the opened receiver directory; never resolve a final file component."""

    def __init__(self, path: Path):
        self.path = path
        self.closed = False
        if sys.platform == "win32":
            self.kernel = _kernel()
            self.handle = _open(self.kernel, path, directory=True)
            try:
                self.identity, attributes = _info(self.kernel, self.handle)
                if not attributes & FILE_ATTRIBUTE_DIRECTORY:
                    raise Fault(
                        "fileParent", "An existing parent directory is required"
                    )
                size = self.kernel.GetFinalPathNameByHandleW(
                    self.handle, None, 0, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
                )
                if not size:
                    raise ctypes.WinError(ctypes.get_last_error())
                buffer = ctypes.create_unicode_buffer(size + 1)
                count = self.kernel.GetFinalPathNameByHandleW(
                    self.handle,
                    buffer,
                    len(buffer),
                    FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
                )
                if not count or count >= len(buffer):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.path = Path(buffer.value)
            except BaseException:
                self.close()
                raise
        else:
            self.kernel = None
            self.handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                value = os.fstat(self.handle)
                self.identity = FileIdentity(value.st_dev, value.st_ino)
            except BaseException:
                self.close()
                raise

    def key(self, name: str) -> DestinationKey:
        return self.identity, name.casefold() if self.kernel else name

    def open(self, name: str) -> BinaryIO:
        return self._open(name, create=False)[0]

    def create(self, name: str) -> tuple[BinaryIO, FileIdentity]:
        return self._open(name, create=True)

    def _open(self, name: str, *, create: bool) -> tuple[BinaryIO, FileIdentity]:
        if sys.platform == "win32":
            import msvcrt

            handle = _open(self.kernel, self.path / name, create=create)
            fd = None
            try:
                owned, attributes = _info(self.kernel, handle)
                if (
                    attributes
                    & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)
                    or self.kernel.GetFileType(handle) != FILE_TYPE_DISK
                ):
                    raise Fault(
                        "fileType",
                        "An ordinary file without a final reparse point is required",
                    )
                fd = msvcrt.open_osfhandle(
                    handle, os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY)
                )
                return (
                    cast(
                        BinaryIO, os.fdopen(fd, "w+b" if create else "rb", buffering=0)
                    ),
                    owned,
                )
            except BaseException as error:
                try:
                    if create:
                        _delete_handle(self.kernel, handle)
                except OSError as cleanup_error:
                    raise Fault(
                        "temporaryCleanup",
                        f"{error}; file cleanup unconfirmed at {self.path / name}; "
                        f"cleanup failed: {cleanup_error}",
                    ) from error
                finally:
                    if fd is None:
                        self.kernel.CloseHandle(handle)
                    else:
                        os.close(fd)
                raise
        else:
            flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            flags |= os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            fd = os.open(name, flags, 0o600, dir_fd=self.handle)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise Fault("fileType", "Only ordinary files are supported")
                return (
                    cast(
                        BinaryIO, os.fdopen(fd, "w+b" if create else "rb", buffering=0)
                    ),
                    FileIdentity(info.st_dev, info.st_ino),
                )
            except BaseException as error:
                try:
                    try:
                        os.close(fd)
                    finally:
                        if create:
                            os.unlink(name, dir_fd=self.handle)
                except OSError as cleanup_error:
                    raise Fault(
                        "temporaryCleanup",
                        f"{error}; file cleanup unconfirmed at {self.path / name}; "
                        f"cleanup failed: {cleanup_error}",
                    ) from error
                raise

    def exists(self, name: str) -> bool:
        try:
            with self.open(name):
                return True
        except FileNotFoundError:
            return False

    def verify(self, name: str, expected: FileIdentity) -> None:
        with self.open(name) as stream:
            if identity(stream) != expected:
                raise Fault("temporaryChanged", "Temporary file identity changed")

    def publish(
        self,
        temporary: str,
        name: str,
        expected: FileIdentity,
        *,
        replace: bool,
        before_publish: Callable[[], None],
    ) -> None:
        self.verify(temporary, expected)
        # Opening an existing target rejects non-files and final links/reparse points.
        try:
            with self.open(name):
                pass
        except FileNotFoundError:
            pass
        if sys.platform == "win32":
            before_publish()
            (os.replace if replace else os.rename)(
                self.path / temporary, self.path / name
            )
        elif replace:
            before_publish()
            os.replace(temporary, name, src_dir_fd=self.handle, dst_dir_fd=self.handle)
        elif sys.platform in ("linux", "darwin"):
            library = ctypes.CDLL(None, use_errno=True)
            darwin = sys.platform == "darwin"
            rename = getattr(library, "renameatx_np" if darwin else "renameat2", None)
            if rename is None:
                raise Fault(
                    "publishUnsupported", "Atomic no-replace publishing is unavailable"
                )
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            before_publish()
            if rename(
                self.handle,
                os.fsencode(temporary),
                self.handle,
                os.fsencode(name),
                RENAME_EXCL if darwin else RENAME_NOREPLACE,
            ):
                code = ctypes.get_errno()
                if code in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):
                    raise Fault(
                        "publishUnsupported",
                        "Atomic no-replace is unsupported here; "
                        "choose --replace or another path",
                    )
                raise OSError(code, os.strerror(code))
        else:
            raise Fault(
                "publishUnsupported", "Atomic no-replace publishing is unavailable"
            )

    def remove(self, name: str, expected: FileIdentity) -> bool:
        if sys.platform == "win32":
            try:
                handle = _open(self.kernel, self.path / name, delete=True)
            except FileNotFoundError:
                return True
            try:
                actual, attributes = _info(self.kernel, handle)
                if actual != expected or attributes & (
                    FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY
                ):
                    return False
                _delete_handle(self.kernel, handle)
                return True
            finally:
                if not self.kernel.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
        else:
            try:
                self.verify(name, expected)
                os.unlink(name, dir_fd=self.handle)
                return True
            except FileNotFoundError:
                return True
            except Fault:
                return False

    def close(self) -> None:
        if self.closed:
            return
        if sys.platform == "win32":
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.close(self.handle)
        self.closed = True
