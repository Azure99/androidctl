from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

import pytest

from androidctl.private_files import state_directory


def _prepend_repo_source_path(path: Path) -> None:
    candidate = str(path.resolve())
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


_TESTS_DIR = Path(__file__).resolve().parent
_ANDROIDCTL_ROOT = _TESTS_DIR.parent
_REPO_ROOT = _ANDROIDCTL_ROOT.parent

_prepend_repo_source_path(_ANDROIDCTL_ROOT / "src")
_prepend_repo_source_path(_REPO_ROOT / "contracts" / "src")


def pytest_pyfunc_call(pyfuncitem):
    function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(function):
        return None
    arguments = {
        name: pyfuncitem.funcargs[name]
        for name in inspect.signature(function).parameters
    }
    asyncio.run(function(**arguments))
    return True


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA" if os.name == "nt" else "HOME", str(tmp_path))
    return state_directory()
