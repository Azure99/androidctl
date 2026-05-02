from __future__ import annotations

import json
from pathlib import Path

from androidctld.refs.service import RefRegistryBuilder
from androidctld.semantics.compiler import (
    CompiledScreen,
    SemanticCompiler,
    SemanticNode,
)
from androidctld.semantics.continuity import evaluate_continuity
from androidctld.semantics.models import SemanticMeta
from androidctld.snapshots.models import (
    RawIme,
    RawNode,
    RawSnapshot,
    parse_raw_snapshot,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "golden" / "fixtures"


def _load_snapshot(name: str) -> RawSnapshot:
    return parse_raw_snapshot(json.loads((FIXTURES_DIR / name).read_text("utf-8")))


def _make_candidate(
    *,
    rid: str,
    label: str = "Wi-Fi",
) -> SemanticNode:
    return SemanticNode(
        raw_rid=rid,
        role="button",
        label=label,
        state=[],
        actions=["tap"],
        bounds=(0, 0, 100, 20),
        meta=SemanticMeta(
            resource_id="android:id/button1",
            class_name="android.widget.Button",
        ),
        targetable=True,
        score=100,
        group="targets",
        parent_role="container",
        parent_label="Network",
        sibling_labels=["Bluetooth"],
        relative_bounds=(0, 0, 100, 20),
    )


def _compiled_screen(
    *,
    screen_id: str,
    fingerprint: str,
    targets: list[SemanticNode],
) -> CompiledScreen:
    return CompiledScreen(
        screen_id=screen_id,
        sequence=1,
        source_snapshot_id=1,
        captured_at="2026-04-13T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        keyboard_visible=False,
        action_surface_fingerprint=fingerprint,
        targets=targets,
        context=[],
        dialog=[],
        keyboard=[],
        system=[],
    )


def test_same_authoritative_surface_reuses_screen_id() -> None:
    snapshot = _load_snapshot("settings_snapshot.json")

    first = SemanticCompiler().compile(1, snapshot)
    second = SemanticCompiler().compile(2, snapshot)
    continuity = evaluate_continuity(source_screen=first, candidate_screen=second)

    assert second.screen_id == first.screen_id
    assert continuity.next_screen_id == first.screen_id
    assert continuity.continuity_status == "stable"
    assert continuity.changed is False


def test_blocking_group_moves_to_front() -> None:
    snapshot = RawSnapshot(
        snapshot_id=7,
        captured_at="2026-04-13T00:00:00Z",
        package_name="com.android.permissioncontroller",
        activity_name="GrantPermissionsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(
            RawNode(
                rid="dialog-root",
                window_id="w1",
                parent_rid=None,
                child_rids=("allow",),
                class_name="android.app.Dialog",
                resource_id=None,
                text=None,
                content_desc=None,
                hint_text=None,
                state_description=None,
                pane_title="Allow access?",
                package_name="com.android.permissioncontroller",
                bounds=(0, 0, 500, 300),
                visible_to_user=True,
                important_for_accessibility=True,
                clickable=False,
                enabled=True,
                editable=False,
                focusable=False,
                focused=False,
                checkable=False,
                checked=False,
                selected=False,
                scrollable=False,
                password=False,
                actions=(),
            ),
            RawNode(
                rid="allow",
                window_id="w1",
                parent_rid="dialog-root",
                child_rids=(),
                class_name="android.widget.Button",
                resource_id="android:id/button1",
                text="Allow",
                content_desc=None,
                hint_text=None,
                state_description=None,
                pane_title=None,
                package_name="com.android.permissioncontroller",
                bounds=(100, 200, 240, 260),
                visible_to_user=True,
                important_for_accessibility=True,
                clickable=True,
                enabled=True,
                editable=False,
                focusable=True,
                focused=False,
                checkable=False,
                checked=False,
                selected=False,
                scrollable=False,
                password=False,
                actions=("click",),
            ),
            RawNode(
                rid="wifi",
                window_id="w1",
                parent_rid=None,
                child_rids=(),
                class_name="android.widget.Switch",
                resource_id="android:id/switch_widget",
                text="Wi-Fi",
                content_desc=None,
                hint_text=None,
                state_description="On",
                pane_title=None,
                package_name="com.android.settings",
                bounds=(0, 320, 500, 420),
                visible_to_user=True,
                important_for_accessibility=True,
                clickable=True,
                enabled=True,
                editable=False,
                focusable=True,
                focused=False,
                checkable=True,
                checked=True,
                selected=False,
                scrollable=False,
                password=False,
                actions=("click",),
            ),
        ),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )

    compiled = SemanticCompiler().compile(1, snapshot)
    RefRegistryBuilder().reconcile(
        compiled_screen=compiled,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    screen = compiled.to_public_screen()

    assert screen.surface.blocking_group == "dialog"
    assert [group.name for group in screen.groups] == [
        "dialog",
        "targets",
        "keyboard",
        "system",
        "context",
    ]


def test_non_unique_repair_fails_closed_as_stale() -> None:
    source = _compiled_screen(
        screen_id="screen-source",
        fingerprint="fingerprint-source",
        targets=[_make_candidate(rid="w1:0.1")],
    )
    RefRegistryBuilder().reconcile(
        compiled_screen=source,
        snapshot_id=1,
        previous_registry=None,
    )
    candidate = _compiled_screen(
        screen_id="screen-candidate",
        fingerprint="fingerprint-candidate",
        targets=[
            _make_candidate(rid="w1:0.2"),
            _make_candidate(rid="w1:0.3"),
        ],
    )

    continuity = evaluate_continuity(source_screen=source, candidate_screen=candidate)

    assert continuity.next_screen_id == candidate.screen_id
    assert continuity.continuity_status == "stale"
    assert continuity.code == "REF_STALE"


def test_screen_id_is_stable_and_not_tiny_hash_space() -> None:
    snapshot = _load_snapshot("settings_snapshot.json")

    first = SemanticCompiler().compile(1, snapshot)
    second = SemanticCompiler().compile(99, snapshot)

    assert first.screen_id == second.screen_id
    assert first.screen_id.startswith("screen-")
    assert len(first.screen_id.removeprefix("screen-")) >= 12
