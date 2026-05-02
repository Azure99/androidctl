from pathlib import Path


def daemon_state_root(workspace_root: Path) -> Path:
    return workspace_root / ".androidctl" / "daemon"
