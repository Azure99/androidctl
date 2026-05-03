from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchSpec:
    executable: str
    argv: tuple[str, ...] = ()
    env_overlay: dict[str, str] | None = None
    cwd: Path | None = None


def resolve_launch_spec(
    *,
    env: Mapping[str, str] | None = None,
) -> LaunchSpec:
    merged_env = os.environ if env is None else env
    env_bin = merged_env.get("ANDROIDCTLD_BIN")
    if env_bin:
        return LaunchSpec(executable=env_bin)
    return LaunchSpec(executable=sys.executable, argv=("-m", "androidctld"))
