from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class LauncherConfig(BaseModel):
    executable: str | None = None
    argv: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class UserConfig(BaseModel):
    launcher: LauncherConfig = Field(default_factory=LauncherConfig)


def read_user_config(path: Path) -> UserConfig:
    if not path.exists():
        return UserConfig()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return UserConfig.model_validate(payload)
