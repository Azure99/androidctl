from __future__ import annotations

import re
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from . import paths
from .private_files import LifetimeLock, private_directory
from .protocol import Fault
from .user_files import create

MAX_FILES = 150
MAX_BYTES = 20 * 1024 * 1024
FILE_STEM = re.compile(r"([0-9]{6,})-[0-9]{8}T[0-9]{12}Z-[\w.-]{1,50}-[\w.-]{1,50}")


class SavedHistory(NamedTuple):
    snapshot_path: Path | None
    image_path: Path | None
    image_error: str | None
    warning: str | None


def scan(root: Path) -> dict[tuple[int, str], dict[Path, int]]:
    groups: dict[tuple[int, str], dict[Path, int]] = {}
    for folder, suffixes in (
        ("snapshots", {".xml", ".json", ".source"}),
        ("shots", {".png"}),
    ):
        for path in (root / folder).iterdir():
            match = FILE_STEM.fullmatch(path.stem)
            if match is None or path.suffix not in suffixes:
                continue
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                groups.setdefault((int(match[1]), path.stem), {})[path] = info.st_size
    return groups


def within_quota(
    groups: dict[tuple[int, str], dict[Path, int]], files: int, size: int
) -> bool:
    return (
        sum(len(group) for group in groups.values()) + files <= MAX_FILES
        and sum(sum(group.values()) for group in groups.values()) + size <= MAX_BYTES
    )


def safe(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "_.-" else "_"
        for char in (value or "untitled")
    )[:50]


def save(
    workspace_root: Path,
    body: str | None,
    png: bytes | None,
    *,
    app: str,
    title: str,
    deadline: float,
    suffix: str = ".xml",
    source_body: str | None = None,
) -> SavedHistory:
    content = body.encode("utf-8") if body is not None else b""
    source_content = source_body.encode("utf-8") if source_body is not None else b""
    total = len(content) + len(png or b"") + len(source_content)
    if total > MAX_BYTES:
        raise Fault("history", "Capture exceeds the history size limit")
    root = workspace_root / ".androidctl"
    hint = "Choose a writable --workspace-root for automatic artifacts."
    for directory in (root, root / "snapshots", root / "shots"):
        try:
            if paths.exists(directory) and not paths.is_dir(directory):
                raise Fault("history", f"Cache path is not a directory: {directory}")
            private_directory(directory)
        except Fault as error:
            raise Fault.from_problem(
                error.problem.model_copy(update={"message": f"{error}. {hint}"})
            ) from error
        except OSError as error:
            raise Fault(
                "history",
                f"Cannot prepare cache directory {directory}: {error}. {hint}",
            ) from error
    lock = LifetimeLock(root / "history.lock")
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise Fault("history", "History is busy within this request budget")
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    try:
        ignore = root / ".gitignore"
        if not paths.exists(ignore):
            create(root, ignore.name, b"*\n")
        groups = scan(root)
        sequence = max((key[0] for key in groups), default=0) + 1
        needed = (
            int(body is not None) + int(png is not None) + int(source_body is not None)
        )
        failures: list[str] = []
        for key in sorted(groups):
            if within_quota(groups, needed, total):
                break
            group = groups[key]
            for path in list(group):
                try:
                    path.unlink(missing_ok=True)
                    del group[path]
                except OSError as error:
                    failures.append(f"{path}: {error}")
            if not group:
                del groups[key]
        if not within_quota(groups, needed, total):
            raise Fault(
                "history",
                "History quota remains occupied. "
                "Close handles to retained files: " + "; ".join(failures),
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{sequence:06d}-{stamp}-{safe(app)}-{safe(title)}"
        snapshot_path = None
        if body is not None:
            snapshot_path = root / "snapshots" / (name + suffix)
            create(snapshot_path.parent, snapshot_path.name, content)
        image_path, image_error = None, None
        if png is not None:
            try:
                path = root / "shots" / (name + ".png")
                create(path.parent, path.name, png)
                image_path = path
            except (Fault, OSError) as error:
                image_error = str(error)
        warning = (
            "History cleanup incomplete; retained: " + "; ".join(failures)
            if failures
            else None
        )
        if source_body is not None:
            try:
                create(root / "snapshots", name + ".source", source_content)
            except (Fault, OSError) as error:
                warning = "; ".join(
                    filter(None, (warning, f"Ref source was not saved: {error}"))
                )
        return SavedHistory(snapshot_path, image_path, image_error, warning)
    finally:
        lock.close()
