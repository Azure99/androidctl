from __future__ import annotations

import re

_WINDOWS_ABS_RE = re.compile(r"^[a-zA-Z]:/")


def normalize_public_path(
    path: str | None,
    *,
    workspace_root: str | None,
    artifact_root: str | None,
) -> str | None:
    if path is None:
        return None
    normalized_path = path.replace("\\", "/")
    normalized_workspace = _normalize_slashes(workspace_root)
    normalized_artifact = _normalize_slashes(artifact_root)
    if (
        normalized_artifact is not None
        and normalized_artifact != ""
        and _is_within(normalized_path, normalized_artifact)
    ):
        artifact_relative = _relative_to(normalized_path, normalized_artifact)
        if artifact_relative is not None and _is_internal_screen_artifact(
            artifact_relative
        ):
            return normalized_path
    if (
        normalized_workspace is not None
        and normalized_workspace != ""
        and _is_within(normalized_path, normalized_workspace)
    ):
        relative = _relative_to(normalized_path, normalized_workspace)
        if relative is not None:
            return relative
    return normalized_path


def _normalize_slashes(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\\", "/")


def _is_internal_screen_artifact(relative_path: str) -> bool:
    relative_parts = _parts(relative_path)
    if not relative_parts:
        return False
    return relative_parts[0].lower() == "screens"


def _is_within(path: str, base: str) -> bool:
    path_parts = _parts(path)
    base_parts = _parts(base)
    if len(base_parts) > len(path_parts):
        return False
    if _drive(path_parts) != _drive(base_parts):
        return False
    path_slice = path_parts[: len(base_parts)]
    if _is_windows_path(path_parts) and _is_windows_path(base_parts):
        return _lowered(path_slice) == _lowered(base_parts)
    return path_slice == base_parts


def _relative_to(path: str, base: str) -> str | None:
    path_parts = _parts(path)
    base_parts = _parts(base)
    if not _is_within(path, base):
        return None
    relative_parts = path_parts[len(base_parts) :]
    if not relative_parts:
        return "."
    return "/".join(relative_parts)


def _parts(value: str) -> tuple[str, ...]:
    trimmed = value.rstrip("/")
    if trimmed == "":
        return ()
    if _WINDOWS_ABS_RE.match(trimmed):
        drive, tail = trimmed.split(":/", maxsplit=1)
        head = f"{drive.upper()}:"
        tail_parts = tuple(part for part in tail.split("/") if part)
        return (head, *tail_parts)
    if trimmed.startswith("/"):
        return ("/", *tuple(part for part in trimmed[1:].split("/") if part))
    return tuple(part for part in trimmed.split("/") if part)


def _drive(parts: tuple[str, ...]) -> str | None:
    if not parts:
        return None
    head = parts[0]
    if head == "/":
        return "/"
    if head.endswith(":"):
        return head.lower()
    return None


def _lowered(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.lower() for part in parts)


def _is_windows_path(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    return parts[0].endswith(":")
