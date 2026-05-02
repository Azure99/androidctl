from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from androidctl.user_state.config import LauncherConfig


@dataclass(frozen=True)
class LaunchSpec:
    executable: str
    argv: tuple[str, ...] = ()
    env_overlay: dict[str, str] | None = None
    cwd: Path | None = None


def resolve_launch_spec(
    *,
    launcher: LauncherConfig | None,
    env: dict[str, str] | None = None,
) -> LaunchSpec:
    env_is_explicit = env is not None
    merged_env = os.environ if env is None else env
    if launcher and launcher.executable:
        executable = launcher.executable
        return LaunchSpec(
            executable=executable,
            argv=tuple(launcher.argv),
            env_overlay=dict(launcher.env),
            cwd=Path(launcher.cwd).resolve() if launcher.cwd else None,
        )

    env_bin = merged_env.get("ANDROIDCTLD_BIN")
    if env_bin:
        return LaunchSpec(executable=env_bin)

    path_value = merged_env.get("PATH")
    if env_is_explicit and path_value is None:
        path_value = ""
    path_bin = shutil.which("androidctld", path=path_value)
    if path_bin:
        return LaunchSpec(executable=path_bin)
    return LaunchSpec(executable=sys.executable, argv=("-m", "androidctld"))
