from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from androidctld.refs.service import RefRegistryBuilder
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.semantics.public_models import public_group_nodes
from androidctld.snapshots.models import (
    RawIme,
    RawSnapshot,
    RawWindow,
    parse_raw_snapshot,
)

from .support.semantic_screen import make_contract_snapshot, make_raw_node

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "golden" / "fixtures"


def _load_snapshot(name: str) -> RawSnapshot:
    return parse_raw_snapshot(json.loads((FIXTURES_DIR / name).read_text("utf-8")))


def _compile_public_screen(snapshot: RawSnapshot):
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    return finalized.compiled_screen.to_public_screen()


def _target_by_label(screen, *, label: str):
    return next(
        node for node in public_group_nodes(screen, "targets") if node.label == label
    )


def test_submit_exposed_for_focused_input_with_raw_submit_action() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:input",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText", "submit", "click"),
        ),
    )

    screen = _compile_public_screen(snapshot)
    target = _target_by_label(screen, label="Search settings")

    assert screen.surface.focus.input_ref == "n1"
    assert "focused" in target.state
    assert "submit" in target.actions


@pytest.mark.parametrize(
    "raw_action",
    [("action_321",), ("action_999999",), ("action_888888",)],
)
def test_submit_omitted_for_focused_input_with_unknown_raw_action(
    raw_action: str,
) -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:input",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText", raw_action, "click"),
        ),
    )

    screen = _compile_public_screen(snapshot)
    target = _target_by_label(screen, label="Search settings")

    assert screen.surface.focus.input_ref == "n1"
    assert "focused" in target.state
    assert "submit" not in target.actions


def test_compiler_omits_submit_for_settings_search_unknown_raw_action() -> None:
    screen = _compile_public_screen(_load_snapshot("settings_search_snapshot.json"))
    target = _target_by_label(screen, label="Search settings")

    assert screen.surface.focus.input_ref == "n1"
    assert "focused" in target.state
    assert "submit" not in target.actions


def test_submit_omitted_for_focused_input_without_submit_capability() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:input",
            text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText", "click"),
        ),
    )

    screen = _compile_public_screen(snapshot)
    target = _target_by_label(screen, label="Search settings")

    assert screen.surface.focus.input_ref == "n1"
    assert "focused" in target.state
    assert "submit" not in target.actions


@pytest.mark.parametrize(
    ("fixture_name", "label"),
    [
        ("messages_compose_snapshot.json", "phase-d"),
        ("chrome_scroll_snapshot.json", "baidu.com/s?wd=openai"),
    ],
)
def test_compiler_omits_submit_for_unfocused_inputs(
    fixture_name: str,
    label: str,
) -> None:
    screen = _compile_public_screen(_load_snapshot(fixture_name))
    target = _target_by_label(screen, label=label)

    assert screen.surface.focus.input_ref is None
    assert "focused" not in target.state
    assert "type" not in target.actions
    assert set(target.actions).issubset({"focus"})
    assert "submit" not in target.actions


def test_compose_surface_keeps_focused_input_actionable_with_visible_keyboard() -> None:
    snapshot = replace(
        make_contract_snapshot(
            make_raw_node(
                rid="w1:input",
                text="Compose message",
                hint_text="Text message",
                editable=True,
                focused=True,
                actions=("focus", "setText", "click"),
            ),
            make_raw_node(
                rid="ime:key",
                window_id="ime",
                class_name="android.widget.Button",
                package_name="com.example.keyboard",
                text="Send",
                editable=False,
                focusable=False,
                actions=("click",),
            ),
            package_name="com.google.android.apps.messaging",
            activity_name="ComposeActivity",
            windows=(
                RawWindow(
                    window_id="w1",
                    type="application",
                    layer=1,
                    package_name="com.google.android.apps.messaging",
                    bounds=(0, 0, 1080, 2400),
                    root_rid="w1:input",
                ),
                RawWindow(
                    window_id="ime",
                    type="input_method",
                    layer=2,
                    package_name="com.example.keyboard",
                    bounds=(0, 1400, 1080, 2400),
                    root_rid="ime:key",
                ),
            ),
        ),
        ime=RawIme(visible=True, window_id="ime"),
    )

    screen = _compile_public_screen(snapshot)
    target = _target_by_label(screen, label="Compose message")

    assert screen.surface.blocking_group is None
    assert target.ref is not None
    assert screen.surface.focus.input_ref == target.ref
    assert "focused" in target.state
    assert "type" in target.actions


def test_keyboard_blocker_exposes_focused_ime_input_as_authoritative_surface() -> None:
    snapshot = replace(
        make_contract_snapshot(
            make_raw_node(
                rid="w1:input",
                text="Compose message",
                editable=True,
                focused=False,
                actions=("focus", "setText", "click"),
            ),
            make_raw_node(
                rid="ime:input",
                window_id="ime",
                class_name="android.widget.EditText",
                package_name="com.example.keyboard",
                text="Search emojis",
                editable=True,
                focused=True,
                actions=("focus", "setText", "submit", "click"),
            ),
            make_raw_node(
                rid="ime:key",
                window_id="ime",
                class_name="android.widget.Button",
                package_name="com.example.keyboard",
                text="Search",
                editable=False,
                focusable=False,
                actions=("click",),
            ),
            package_name="com.google.android.apps.messaging",
            activity_name="ComposeActivity",
            windows=(
                RawWindow(
                    window_id="w1",
                    type="application",
                    layer=1,
                    package_name="com.google.android.apps.messaging",
                    bounds=(0, 0, 1080, 2400),
                    root_rid="w1:input",
                ),
                RawWindow(
                    window_id="ime",
                    type="input_method",
                    layer=2,
                    package_name="com.example.keyboard",
                    bounds=(0, 1400, 1080, 2400),
                    root_rid="ime:key",
                ),
            ),
        ),
        ime=RawIme(visible=True, window_id="ime"),
    )

    screen = _compile_public_screen(snapshot)
    keyboard_input = next(
        node
        for node in public_group_nodes(screen, "keyboard")
        if node.label == "Search emojis"
    )

    assert screen.surface.blocking_group == "keyboard"
    assert keyboard_input.ref is not None
    assert screen.surface.focus.input_ref == keyboard_input.ref
    assert "focused" in keyboard_input.state
    assert "type" in keyboard_input.actions
    assert "submit" in keyboard_input.actions
