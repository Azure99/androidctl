"""Restricted atomic writers for daemon-local sensitive state."""

from __future__ import annotations

import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from androidctld.schema.persistence_io import dump_formatted_json

SECRET_FILE_MODE = 0o600
SECRET_DIR_MODE = 0o700


def write_secret_json_file_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Write sensitive daemon JSON state via a restricted atomic sidecar."""
    _ensure_secret_parent_dir(path.parent)
    temp_path = _unique_temp_path(path)
    fd: int | None = None
    try:
        fd = os.open(
            temp_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            SECRET_FILE_MODE,
        )
        if os.name == "posix":
            os.fchmod(fd, SECRET_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            dump_formatted_json(handle, payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def _unique_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _ensure_secret_parent_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, SECRET_DIR_MODE)
