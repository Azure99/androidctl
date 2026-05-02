from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def _resolve_user_path(value: Path | str, *, cwd: Path, field_name: str) -> Path:
    raw_value = str(value)
    if os.name != "nt" and PureWindowsPath(raw_value).is_absolute():
        raise ValueError(f"{field_name} must use a host path")
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def resolve_workspace_root(
    *,
    flag_value: Path | None,
    env_value: str | None,
    cwd: Path,
) -> Path:
    if flag_value is not None:
        return _resolve_user_path(flag_value, cwd=cwd, field_name="workspace root")
    if env_value:
        return _resolve_user_path(env_value, cwd=cwd, field_name="workspace root")
    return cwd.resolve()
