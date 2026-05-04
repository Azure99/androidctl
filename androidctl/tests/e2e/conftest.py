from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.e2e.support import remove_editable_metadata_dirs

REPO_ROOT = Path(__file__).resolve().parents[3]
VENV_CREATE_TIMEOUT_SECONDS = 120
PIP_INSTALL_TIMEOUT_SECONDS = 240


@dataclass(frozen=True)
class EditableInstallEnv:
    python_executable: Path
    androidctl_executable: Path


def _python_path(venv_dir: Path) -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    return venv_dir / scripts_dir / "python"


def _script_path(venv_dir: Path, name: str) -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_dir / scripts_dir / f"{name}{suffix}"


def _minimal_install_env() -> dict[str, str]:
    allowlist = (
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    )
    env = {key: value for key in allowlist if (value := os.environ.get(key))}
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _install_editable_packages(root: Path) -> EditableInstallEnv:
    remove_editable_metadata_dirs()
    venv_dir = root / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", venv_dir.as_posix()],
        check=True,
        capture_output=True,
        text=True,
        timeout=VENV_CREATE_TIMEOUT_SECONDS,
    )
    python_executable = _python_path(venv_dir)
    androidctl_executable = _script_path(venv_dir, "androidctl")
    subprocess.run(
        [
            python_executable.as_posix(),
            "-m",
            "pip",
            "install",
            "-e",
            f"{REPO_ROOT.as_posix()}[dev]",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=_minimal_install_env(),
        timeout=PIP_INSTALL_TIMEOUT_SECONDS,
    )
    return EditableInstallEnv(
        python_executable=python_executable,
        androidctl_executable=androidctl_executable,
    )


@pytest.fixture(scope="module")
def editable_install_env(
    tmp_path_factory: pytest.TempPathFactory,
) -> EditableInstallEnv:
    env = _install_editable_packages(tmp_path_factory.mktemp("editable-install"))
    try:
        yield env
    finally:
        remove_editable_metadata_dirs()
