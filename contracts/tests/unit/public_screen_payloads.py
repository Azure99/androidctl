from __future__ import annotations


def public_screen_payload_base(screen_id: str) -> dict[str, object]:
    return {
        "screenId": screen_id,
        "app": {"packageName": "com.android.settings"},
        "surface": {
            "keyboardVisible": False,
            "focus": {},
        },
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {"name": "context", "nodes": []},
            {"name": "dialog", "nodes": []},
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [],
    }
