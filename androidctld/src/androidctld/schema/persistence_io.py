"""File I/O helpers for JSON persistence boundaries."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import IO, Any


def dump_formatted_json(handle: IO[str], payload: object) -> None:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            dump_formatted_json(handle, payload)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(str(error)) from error
    if not isinstance(payload, dict):
        raise ValueError("root JSON value must be an object")
    return dict(payload)
