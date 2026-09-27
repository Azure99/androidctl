import ctypes
import errno
import os
import sys
from types import SimpleNamespace

import pytest

from androidctl import private_files, user_files
from androidctl.private_files import (
    LifetimeLock,
    PublishedWriteError,
    SecurityAttributes,
    atomic_write,
    check_private,
)
from androidctl.protocol import Fault


def test_lock_error_is_propagated_after_closing_file(tmp_path, monkeypatch):
    descriptors = []
    failure = OSError(errno.EIO, "lock I/O failure")

    def fail(descriptor, *args):
        descriptors.append(descriptor)
        raise failure

    if sys.platform == "win32":
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", fail)
    else:
        import fcntl

        monkeypatch.setattr(fcntl, "flock", fail)

    lock = LifetimeLock(tmp_path / "lifetime.lock")
    with pytest.raises(OSError, match="lock I/O failure") as caught:
        lock.acquire()
    assert caught.value is failure
    assert len(descriptors) == 1
    with pytest.raises(OSError, match=r"\[(?:Errno 9|WinError 6)\]"):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("size", [0, 256, 65553])
def test_atomic_publication_preserves_no_replace(tmp_path, size):
    parent = user_files.Directory(tmp_path)
    data = bytes(index % 256 for index in range(size))
    try:
        stream, owned = parent.create("temporary")
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        parent.publish(
            "temporary", "target", owned, replace=False, before_publish=lambda: None
        )
        assert (tmp_path / "target").read_bytes() == data
        stream, next_id = parent.create("next")
        with stream:
            stream.write(b"replacement")
        with pytest.raises(FileExistsError):
            parent.publish(
                "next", "target", next_id, replace=False, before_publish=lambda: None
            )
        assert (tmp_path / "target").read_bytes() == data
        parent.publish(
            "next", "target", next_id, replace=True, before_publish=lambda: None
        )
        assert (tmp_path / "target").read_bytes() == b"replacement"
    finally:
        parent.close()


def test_cleanup_refuses_replaced_temporary(tmp_path):
    parent = user_files.Directory(tmp_path)
    try:
        stream, owned = parent.create("temp")
        stream.close()
        os.rename(tmp_path / "temp", tmp_path / "moved")
        stream, _ = parent.create("temp")
        with stream:
            stream.write(b"not ours")
        with pytest.raises(Fault, match="identity"):
            parent.publish(
                "temp", "target", owned, replace=True, before_publish=lambda: None
            )
        assert not parent.remove("temp", owned)
        assert (tmp_path / "temp").read_bytes() == b"not ours"
        assert parent.remove("moved", owned)
    finally:
        parent.close()


@pytest.mark.parametrize("replaced", [False, True])
def test_failed_create_cleans_owned_file_and_preserves_replacement(
    tmp_path, monkeypatch, replaced
):
    def write_then_fail(stream, data):
        stream.write(data)
        if replaced:
            (tmp_path / "target").rename(tmp_path / "moved")
            (tmp_path / "target").write_bytes(b"foreign")
        raise OSError("original write failed")

    monkeypatch.setattr(user_files, "write_all", write_then_fail)
    with pytest.raises(
        Fault if replaced else OSError, match="original write failed"
    ) as caught:
        user_files.create(tmp_path, "target", b"ours")
    if replaced:
        assert caught.value.problem.code == "fileCleanup"
        assert str(tmp_path / "target") in str(caught.value)
        assert "identity changed; target left untouched" in str(caught.value)
        assert (tmp_path / "target").read_bytes() == b"foreign"
        assert (tmp_path / "moved").read_bytes() == b"ours"
    else:
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "code", [errno.EINVAL, errno.ENOTSUP, errno.ENOSYS, errno.EACCES, errno.EIO]
)
def test_posix_no_replace_error_mapping(monkeypatch, code):
    calls = []

    def rename(*args):
        calls.append(args)
        ctypes.set_errno(code)
        return -1

    parent = object.__new__(user_files.Directory)
    parent.kernel, parent.handle = None, 42
    monkeypatch.setattr(parent, "verify", lambda *_: None)

    def target_missing(*_):
        raise FileNotFoundError("Target is absent")

    monkeypatch.setattr(parent, "open", target_missing)
    monkeypatch.setattr(user_files, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        ctypes, "CDLL", lambda *_, **__: SimpleNamespace(renameat2=rename)
    )
    with pytest.raises((Fault, OSError)) as error:
        parent.publish(
            "temporary",
            "target",
            user_files.FileIdentity(0, 0),
            replace=False,
            before_publish=lambda: None,
        )
    if code in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):
        assert error.value.problem.code == "publishUnsupported"
    else:
        assert error.value.errno == code
    assert len(calls) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation descriptor")
def test_windows_file_is_private_at_createfile_return(tmp_path, monkeypatch):
    kernel = user_files._kernel()
    checked = []

    class ObservedKernel:
        def __getattr__(self, name):
            return getattr(kernel, name)

        def CreateFileW(
            self, path, access, sharing, security, disposition, flags, template
        ):
            handle = kernel.CreateFileW(
                path, access, sharing, security, disposition, flags, template
            )
            if disposition == 1:
                assert security is not None
                attributes = ctypes.cast(
                    security, ctypes.POINTER(SecurityAttributes)
                ).contents
                assert attributes.descriptor
                assert not attributes.inherit
                checked.append(check_private(type(tmp_path)(path)))
            return handle

    monkeypatch.setattr(user_files, "_kernel", ObservedKernel)
    parent = user_files.Directory(tmp_path)
    try:
        stream, _ = parent.create("private")
        with stream:
            stream.write(b"already private")
        assert len(checked) == 1
    finally:
        parent.close()


@pytest.mark.parametrize("failure", ["fstat", "fdopen"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_posix_created_file_initialization_failure_closes_and_cleans(
    tmp_path, monkeypatch, failure, cleanup_fails
):
    parent = object.__new__(user_files.Directory)
    parent.kernel, parent.handle = None, 42
    parent.path = tmp_path
    path = tmp_path / "owned"
    original_open, original_unlink, original_stat = os.open, os.unlink, os.fstat
    descriptors = []

    def create(name, flags, mode, *, dir_fd):
        assert name == "owned"
        assert dir_fd == 42
        fd = original_open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, mode)
        descriptors.append(fd)
        return fd

    def unlink(name, *, dir_fd):
        assert name == "owned"
        assert dir_fd == 42
        if cleanup_fails:
            raise OSError("owned cleanup denied")
        original_unlink(path)

    def fail(*_, **__):
        raise OSError("injected initialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(user_files, "sys", SimpleNamespace(platform="linux"))
        for flag in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            patch.setattr(os, flag, getattr(os, flag, 0), raising=False)
        patch.setattr(os, "open", create)
        patch.setattr(os, "unlink", unlink)
        patch.setattr(os, failure, fail)
        with pytest.raises((Fault, OSError)) as error:
            parent.create("owned")
    if cleanup_fails:
        assert error.value.problem.code == "temporaryCleanup"
        assert str(path) in str(error.value)
        assert "injected initialization failure" in str(error.value)
        assert "owned cleanup denied" in str(error.value)
    else:
        assert "initialization" in str(error.value)
    assert path.exists() == cleanup_fails
    assert len(descriptors) == 1
    with pytest.raises(OSError, match=r"(?:Errno 9|WinError 6)"):
        original_stat(descriptors[0])


def test_post_replace_io_error_carries_publication_fact(tmp_path, monkeypatch):
    path = tmp_path / "value"
    atomic_write(path, b"old")
    replace = private_files.os.replace
    open_fd = private_files.os.open

    def open_directory(name, flags, *args, **kwargs):
        if name == path.parent:
            raise OSError("injected directory synchronization failure")
        return open_fd(name, flags, *args, **kwargs)

    def publish(source, target):
        replace(source, target)
        # Exercise the POSIX directory synchronization branch on this host.
        monkeypatch.setattr(
            private_files, "sys", SimpleNamespace(platform="posix-test")
        )

    monkeypatch.setattr(private_files.os, "replace", publish)
    monkeypatch.setattr(private_files.os, "open", open_directory)
    with pytest.raises(PublishedWriteError):
        atomic_write(path, b"new")
    assert path.read_bytes() == b"new"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shared replacement")
def test_shared_replacement_failure_preserves_target_and_cleans_temporary(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    with pytest.raises(PermissionError):
        atomic_write(path, b"new", publish=user_files.replace_shared)
    assert path.is_dir()
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows publisher handle")
def test_shared_replacement_release_error_carries_publication_fact(
    tmp_path, monkeypatch
):
    path = tmp_path / "target"
    atomic_write(path, b"old")
    kernel = user_files._kernel()

    class ReleaseError:
        def __getattr__(self, name):
            return getattr(kernel, name)

        def CloseHandle(self, handle):
            assert kernel.CloseHandle(handle)
            ctypes.set_last_error(6)
            return 0

    monkeypatch.setattr(user_files, "_kernel", ReleaseError)
    with pytest.raises(PublishedWriteError):
        atomic_write(path, b"new", publish=user_files.replace_shared)
    assert path.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [path]
