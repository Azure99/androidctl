import json
from pathlib import Path
from typing import cast

from . import paths
from .private_files import PublishedWriteError, private_directory, write_json
from .protocol import UNCERTAIN_EXIT, Fault, Result, completed


def config_path(root: Path) -> Path:
    return root / ".androidctl" / "config.json"


def require_root(root: Path) -> None:
    try:
        is_directory = paths.is_dir(root)
    except OSError as error:
        raise Fault("workspace", f"{root}: {error}") from error
    if not is_directory:
        raise Fault("workspace", f"Workspace must be an existing directory: {root}")


def read(root: Path) -> dict[str, str]:
    path = config_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise Fault("workspace", f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict) or any(
        key not in {"device", "session"} or not isinstance(item, str) or not item
        for key, item in value.items()
    ):
        raise Fault("workspace", f"Expected nonempty device/session strings in {path}")
    return cast(dict[str, str], value)


def save(root: Path, value: dict[str, str]) -> Result:
    path = config_path(root)
    result = completed("use", {**value, "path": str(path), "saved": True})
    try:
        private_directory(path.parent)
        write_json(path, value)
    except PublishedWriteError as error:
        result.reason = (
            f"Configuration saved; final file synchronization or cleanup failed: {path}"
        )
        result.error = Fault("workspace", f"{path}: {error}").problem
        result.exit = UNCERTAIN_EXIT
    except OSError as error:
        raise Fault("workspace", f"Cannot save {path}: {error}") from error
    return result


def clear(root: Path) -> Result:
    path = config_path(root)
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise Fault("workspace", f"Cannot remove {path}: {error}") from error
    return completed("use", {"path": str(path), "cleared": True})
