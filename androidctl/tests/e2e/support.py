from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from androidctl_contracts.user_state import ActiveDaemonRecord

if TYPE_CHECKING:
    from tests.e2e.conftest import EditableInstallEnv

ANDROIDCTL_COMMAND_TIMEOUT_SECONDS = 30
DAEMON_STOP_TIMEOUT_SECONDS = 5.0


def write_launcher_config(
    *,
    home_dir: Path,
    executable: Path,
    launcher_argv: list[str],
) -> None:
    config_dir = home_dir / ".androidctl"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    payload = {
        "launcher": {
            "executable": executable.as_posix(),
            "argv": launcher_argv,
        }
    }
    config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def workspace_dir(home_dir: Path) -> Path:
    workspace = home_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def workspace_active_record_path(
    home_dir: Path,
    *,
    workspace_root: Path | None = None,
) -> Path:
    workspace = workspace_root or workspace_dir(home_dir)
    return workspace.resolve() / ".androidctl" / "daemon" / "active.json"


def read_active_record(
    home_dir: Path,
    *,
    workspace_root: Path | None = None,
) -> ActiveDaemonRecord:
    return ActiveDaemonRecord.model_validate_json(
        workspace_active_record_path(
            home_dir,
            workspace_root=workspace_root,
        ).read_text(encoding="utf-8")
    )


def run_androidctl(
    argv: list[str],
    *,
    home_dir: Path,
    editable_install_env: EditableInstallEnv,
    owner_id: str | None = None,
    cwd: Path | None = None,
    workspace_env_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    workspace = workspace_dir(home_dir)
    command_cwd = cwd or workspace
    command_cwd.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    if owner_id is None:
        env.pop("ANDROIDCTL_OWNER_ID", None)
    else:
        env["ANDROIDCTL_OWNER_ID"] = owner_id
    if workspace_env_root is None:
        env.pop("ANDROIDCTL_WORKSPACE_ROOT", None)
    else:
        workspace_env_root.mkdir(parents=True, exist_ok=True)
        env["ANDROIDCTL_WORKSPACE_ROOT"] = str(workspace_env_root)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [editable_install_env.androidctl_executable.as_posix(), *argv],
        cwd=command_cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=ANDROIDCTL_COMMAND_TIMEOUT_SECONDS,
    )


def stop_daemon(
    home_dir: Path,
    *,
    workspace_root: Path | None = None,
) -> None:
    path = workspace_active_record_path(home_dir, workspace_root=workspace_root)
    if not path.exists():
        return
    record = read_active_record(home_dir, workspace_root=workspace_root)
    with suppress(ProcessLookupError):
        os.kill(record.pid, signal.SIGTERM)
    deadline = time.monotonic() + DAEMON_STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(
        "daemon active record remained after stop timeout: "
        f"pid={record.pid} active_record={path}"
    )
