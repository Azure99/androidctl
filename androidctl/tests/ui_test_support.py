import io
import json
from pathlib import Path


def load_wire_contract():
    path = Path(__file__).resolve().parents[2] / "contract/wire-contract.json"
    return json.loads(path.read_text(encoding="utf-8"))


class BrokenStream(io.StringIO):
    def write(self, value):
        raise OSError("broken output")


def node(ref, role="button", name="Pay & finish", **fields):
    return {
        "ref": ref,
        "refAvailable": True,
        "parent": None,
        "view": {
            "role": role,
            "name": name,
            "detail": "",
            "nameComplete": True,
            "detailComplete": True,
            "nameConsumed": False,
            "selectionTarget": None,
        },
        "row": {"complete": True},
        "clickable": True,
        "visible": True,
        "enabled": True,
        "actions": ["click"],
        "unavailable": [],
        **fields,
    }


def tree():
    return {
        "sequence": 7,
        "keyboard": "hidden",
        "finishedMs": 100,
        "scope": {"window": 12, "package": "example.shop"},
        "windows": [{"id": 12, "title": "Cart", "active": True}],
        "nodes": [node("e15")],
    }
