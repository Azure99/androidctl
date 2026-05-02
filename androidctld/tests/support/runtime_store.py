"""RuntimeStore test factories."""

from __future__ import annotations

from pathlib import Path

from androidctld.config import DaemonConfig
from androidctld.runtime.store import RuntimeStore


def runtime_store_for_workspace(workspace_root: Path) -> RuntimeStore:
    return RuntimeStore(
        DaemonConfig(workspace_root=workspace_root, owner_id="test-owner")
    )
