from __future__ import annotations

from copy import deepcopy

import pytest

from androidctld.device.errors import DeviceBootstrapError
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.models import parse_raw_snapshot

from .support.semantic_screen import make_contract_snapshot, make_raw_node


def _snapshot_payload() -> dict[str, object]:
    return {
        "snapshotId": 1,
        "capturedAt": "2026-04-13T00:00:00Z",
        "packageName": "com.android.settings",
        "activityName": "SettingsActivity",
        "display": {
            "widthPx": 1080,
            "heightPx": 2400,
            "densityDpi": 420,
            "rotation": 0,
        },
        "ime": {"visible": False, "windowId": None},
        "windows": [
            {
                "windowId": "w1",
                "type": "application",
                "layer": 0,
                "packageName": "com.android.settings",
                "bounds": [0, 0, 1080, 2400],
                "rootRid": "w1:0",
            }
        ],
        "nodes": [
            {
                "rid": "w1:0",
                "windowId": "w1",
                "parentRid": None,
                "childRids": [],
                "className": "android.widget.Button",
                "resourceId": None,
                "text": "Wi-Fi",
                "contentDesc": None,
                "hintText": None,
                "stateDescription": None,
                "paneTitle": None,
                "packageName": "com.android.settings",
                "bounds": [0, 0, 200, 80],
                "visibleToUser": True,
                "importantForAccessibility": True,
                "clickable": True,
                "enabled": True,
                "editable": False,
                "focusable": True,
                "focused": False,
                "checkable": False,
                "checked": False,
                "selected": False,
                "scrollable": False,
                "password": False,
                "actions": ["click"],
            }
        ],
    }


def test_parse_raw_snapshot_accepts_null_nested_packages_and_display() -> None:
    payload = _snapshot_payload()
    windows = payload["windows"]
    nodes = payload["nodes"]
    assert isinstance(windows, list)
    assert isinstance(nodes, list)
    windows[0]["packageName"] = None
    nodes[0]["packageName"] = None

    snapshot = parse_raw_snapshot(payload)

    assert snapshot.windows[0].package_name is None
    assert snapshot.nodes[0].package_name is None
    assert snapshot.display == {
        "widthPx": 1080,
        "heightPx": 2400,
        "densityDpi": 420,
        "rotation": 0,
    }


@pytest.mark.parametrize("display_value", ["missing", None])
def test_parse_raw_snapshot_rejects_missing_or_null_display(
    display_value: object,
) -> None:
    payload = _snapshot_payload()
    if display_value == "missing":
        del payload["display"]
    else:
        payload["display"] = display_value

    with pytest.raises(DeviceBootstrapError) as excinfo:
        parse_raw_snapshot(payload)

    assert excinfo.value.details == {
        "field": "result.display",
        "reason": "invalid_snapshot",
    }
    assert excinfo.value.retryable is False


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("windows", "result.windows[0].packageName"),
        ("nodes", "result.nodes[0].packageName"),
    ],
)
def test_parse_raw_snapshot_rejects_blank_nested_package_names(
    section: str,
    field: str,
) -> None:
    payload = deepcopy(_snapshot_payload())
    entries = payload[section]
    assert isinstance(entries, list)
    entries[0]["packageName"] = "   "

    with pytest.raises(DeviceBootstrapError) as excinfo:
        parse_raw_snapshot(payload)

    assert excinfo.value.details == {
        "field": field,
        "reason": "invalid_snapshot",
    }
    assert excinfo.value.retryable is False


def test_semantic_grouping_treats_null_node_package_as_not_system() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:button",
            class_name="android.widget.Button",
            text="Continue",
            package_name=None,
            editable=False,
            actions=("click",),
        ),
        package_name="com.android.settings",
        windowless=True,
    )

    compiled = SemanticCompiler().compile(1, snapshot)

    assert [node.raw_rid for node in compiled.targets] == ["w1:button"]
    assert compiled.system == []


def test_semantic_grouping_keeps_systemui_package_in_system() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="sys:button",
            class_name="android.widget.Button",
            text="Quick settings",
            package_name="com.android.systemui",
            editable=False,
            actions=("click",),
        ),
        package_name="com.android.settings",
        windowless=True,
    )

    compiled = SemanticCompiler().compile(1, snapshot)

    assert [node.raw_rid for node in compiled.system] == ["sys:button"]
    assert compiled.targets == []
