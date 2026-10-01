from pathlib import Path

from .protocol import Fault


def require_root(root: Path) -> None:
    try:
        is_directory = root.is_dir()
    except OSError as error:
        raise Fault("workspace", f"{root}: {error}") from error
    if not is_directory:
        raise Fault("workspace", f"Workspace must be an existing directory: {root}")
