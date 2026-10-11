from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .history import FILE_STEM


def build(
    tree: dict[str, Any],
    refs: set[str],
    *,
    device_id: str,
    session: str,
    capture: str,
) -> dict[str, Any]:
    return {
        "refSource": {
            "deviceId": device_id,
            "session": session,
            "capture": capture,
            "context": tree["refContext"],
        },
        "refSignatures": signatures(tree, refs),
    }


def signatures(tree: dict[str, Any], refs: set[str]) -> dict[str, Any]:
    fields = (
        "ref",
        "packageName",
        "windowId",
        "sourceId",
        "resourceId",
        "className",
        "row",
        "bounds",
        "actions",
        "unavailable",
    )
    return {
        node["ref"]: {
            **{field: node.get(field) for field in fields},
            "view": {
                field: node["view"].get(field)
                for field in ("role", "name", "nameComplete")
            },
        }
        for node in tree["nodes"]
        if node.get("refAvailable") and node["ref"] in refs
    }


def load(ref: str, device: str, session: str, workspace: Path) -> dict[str, Any] | None:
    sources = []
    try:
        for path in (workspace / ".androidctl" / "snapshots").glob("*.source"):
            match = FILE_STEM.fullmatch(path.stem)
            if match is not None:
                sources.append((int(match[1]), path))
    except OSError:
        return None
    for _, path in sorted(sources, reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            source = value["refSource"]
            if (source["deviceId"], source["session"]) != (device, session):
                continue
            node = value["refSignatures"][ref]
            if not isinstance(node, dict):
                continue
            return {
                **node,
                "sourceContext": source["context"],
                "sourceCapture": source["capture"],
            }
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
