from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

_DISTRIBUTION_NAME = "androidctl"
_VERSION_FILE_NAME = "VERSION"
_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")


def resolve_release_version() -> str:
    source_version = _read_source_version()
    if source_version is not None:
        return source_version
    try:
        return distribution_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError as error:
        raise RuntimeError("unable to resolve androidctl release version") from error


def _read_source_version() -> str | None:
    for candidate_root in Path(__file__).resolve().parents:
        version_path = candidate_root / _VERSION_FILE_NAME
        if not version_path.is_file() or not _looks_like_repo_root(candidate_root):
            continue
        return _parse_version_text(version_path.read_text(encoding="utf-8"))
    return None


def _looks_like_repo_root(path: Path) -> bool:
    return (
        (path / "pyproject.toml").is_file()
        and (path / "androidctl").is_dir()
        and (path / "androidctld").is_dir()
        and (path / "contracts").is_dir()
    )


def _parse_version_text(raw_text: str) -> str:
    if raw_text.endswith("\r\n"):
        candidate = raw_text.removesuffix("\r\n")
    elif raw_text.endswith("\n"):
        candidate = raw_text.removesuffix("\n")
    else:
        candidate = raw_text
    if (
        "\n" in candidate
        or "\r" in candidate
        or _VERSION_PATTERN.fullmatch(candidate) is None
    ):
        raise RuntimeError(
            "canonical VERSION must contain exactly MAJOR.MINOR.PATCH with "
            "at most one terminating newline"
        )
    return candidate
