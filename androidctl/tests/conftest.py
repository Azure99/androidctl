import asyncio
import inspect
import os

import pytest

from androidctl.private_files import state_directory


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
