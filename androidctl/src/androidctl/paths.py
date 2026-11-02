"""Path probes that keep access errors visible on every supported Python.

Python 3.14 changed Path.exists(), is_dir() and is_file() to return False for
any OSError, so a denied path reads as missing. These probes keep the earlier
rule: only errors that mean the path cannot exist report it as missing, and
other errors propagate to the caller's structured result.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

# Errors that Path.exists() treated as a missing path before Python 3.14.
_MISSING_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})
# Windows ERROR_NOT_READY, ERROR_INVALID_NAME and ERROR_CANT_RESOLVE_FILENAME.
_MISSING_WINERRORS = frozenset({21, 123, 1921})


def stat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError as error:
        if (
            error.errno in _MISSING_ERRNOS
            or getattr(error, "winerror", None) in _MISSING_WINERRORS
        ):
            return None
        raise
    except ValueError:
        # Paths the OS cannot represent, such as those containing NUL.
        return None


def exists(path: Path) -> bool:
    return stat_or_none(path) is not None


def is_dir(path: Path) -> bool:
    info = stat_or_none(path)
    return info is not None and stat.S_ISDIR(info.st_mode)
