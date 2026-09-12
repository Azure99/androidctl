import errno
import os
import sys
from types import SimpleNamespace

import pytest

from androidctl import private_files
from androidctl.private_files import LifetimeLock, PublishedWriteError, atomic_write


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
