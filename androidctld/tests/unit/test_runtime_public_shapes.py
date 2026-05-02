from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import get_args
from xml.etree.ElementTree import fromstring

import pytest
from pydantic import ValidationError

from androidctl_contracts.command_results import ActionTargetPayload, CommandResultCore
from androidctl_contracts.public_screen import (
    BLOCKING_GROUP_NAMES as CONTRACT_BLOCKING_GROUP_NAMES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_GROUP_NAMES as CONTRACT_PUBLIC_GROUP_NAMES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_NODE_ACTION_VALUES as CONTRACT_PUBLIC_NODE_ACTION_VALUES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_NODE_AMBIGUITY_VALUES as CONTRACT_PUBLIC_NODE_AMBIGUITY_VALUES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_NODE_ORIGIN_VALUES as CONTRACT_PUBLIC_NODE_ORIGIN_VALUES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_NODE_ROLE_VALUES as CONTRACT_PUBLIC_NODE_ROLE_VALUES,
)
from androidctl_contracts.public_screen import (
    PUBLIC_NODE_STATE_VALUES as CONTRACT_PUBLIC_NODE_STATE_VALUES,
)
from androidctl_contracts.public_screen import (
    PublicScreen as ContractPublicScreen,
)
from androidctl_contracts.public_screen import (
    ScrollDirection as ContractScrollDirection,
)
from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.screen_payloads import build_screen_artifact_payload
from androidctld.commands.result_models import (
    build_semantic_failure_result,
    build_semantic_success_result,
)
from androidctld.refs.models import RefRegistry
from androidctld.refs.service import RefRegistryBuilder
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.semantics.public_models import (
    BLOCKING_GROUP_NAMES,
    GROUP_NAMES,
    PUBLIC_NODE_ACTION_VALUES,
    PUBLIC_NODE_AMBIGUITY_VALUES,
    PUBLIC_NODE_ORIGIN_VALUES,
    PUBLIC_NODE_ROLE_VALUES,
    PUBLIC_NODE_STATE_VALUES,
    OmittedEntry,
    PublicApp,
    PublicFocus,
    PublicGroup,
    PublicScreen,
    PublicSurface,
    TransientItem,
    public_group_nodes,
)
from androidctld.semantics.public_models import (
    ScrollDirection as DaemonScrollDirection,
)
from androidctld.snapshots.models import (
    RawIme,
    RawSnapshot,
    RawWindow,
    parse_raw_snapshot,
)

from .support.semantic_screen import make_contract_snapshot, make_raw_node

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "golden" / "fixtures"
ANDROIDCTL_SRC = Path(__file__).resolve().parents[2].parent / "androidctl" / "src"


def _load_snapshot_fixture(name: str) -> RawSnapshot:
    return parse_raw_snapshot(json.loads((FIXTURES_DIR / name).read_text("utf-8")))


def _iter_node_payloads(
    nodes: list[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    collected: list[dict[str, object]] = []
    for node in nodes:
        collected.append(node)
        children = node.get("children")
        if isinstance(children, list):
            collected.extend(_iter_node_payloads(children))
    return tuple(collected)


def _assert_contract_public_screen_payload(
    payload: dict[str, object],
) -> ContractPublicScreen:
    assert "sequence" not in payload
    assert "sourceSnapshotId" not in payload
    assert "capturedAt" not in payload
    return ContractPublicScreen.model_validate(payload)


def test_daemon_public_closed_sets_match_shared_contract() -> None:
    assert GROUP_NAMES == CONTRACT_PUBLIC_GROUP_NAMES
    assert BLOCKING_GROUP_NAMES == CONTRACT_BLOCKING_GROUP_NAMES
    assert PUBLIC_NODE_ROLE_VALUES == CONTRACT_PUBLIC_NODE_ROLE_VALUES
    assert PUBLIC_NODE_ACTION_VALUES == CONTRACT_PUBLIC_NODE_ACTION_VALUES
    assert PUBLIC_NODE_STATE_VALUES == CONTRACT_PUBLIC_NODE_STATE_VALUES
    assert PUBLIC_NODE_ORIGIN_VALUES == CONTRACT_PUBLIC_NODE_ORIGIN_VALUES
    assert PUBLIC_NODE_AMBIGUITY_VALUES == CONTRACT_PUBLIC_NODE_AMBIGUITY_VALUES
    assert set(get_args(DaemonScrollDirection)) == set(
        get_args(ContractScrollDirection)
    )


def test_public_screen_uses_surface_and_ordered_groups_shape() -> None:
    snapshot = RawSnapshot(
        snapshot_id=1,
        captured_at="2026-04-07T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )

    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    screen = finalized.compiled_screen.to_public_screen()
    screen_payload = screen.model_dump(by_alias=True, mode="json")
    _assert_contract_public_screen_payload(screen_payload)

    assert "sessionName" not in screen_payload
    assert screen_payload["screenId"] == screen.screen_id
    assert "sequence" not in screen_payload
    assert "sourceSnapshotId" not in screen_payload
    assert "capturedAt" not in screen_payload
    assert "packageName" not in screen_payload
    assert "activityName" not in screen_payload
    assert "keyboardVisible" not in screen_payload
    assert screen_payload["app"] == {
        "packageName": "com.android.settings",
        "activityName": "SettingsActivity",
    }
    assert screen_payload["surface"] == {
        "keyboardVisible": False,
        "focus": {},
    }
    assert [group["name"] for group in screen_payload["groups"]] == [
        "targets",
        "keyboard",
        "system",
        "context",
        "dialog",
    ]
    assert screen_payload["omitted"] == []
    assert screen_payload["visibleWindows"] == []
    assert screen_payload["transient"] == []


def test_public_screen_rejects_flattened_legacy_shape() -> None:
    legacy_payload = {
        "screenId": "screen-legacy",
        "package_name": "com.android.settings",
        "activity_name": "SettingsActivity",
        "keyboard_visible": False,
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

    with pytest.raises(ValidationError) as exc:
        PublicScreen.model_validate(legacy_payload)

    error_locations = {
        tuple(str(part) for part in error["loc"]) for error in exc.value.errors()
    }

    assert ("app",) in error_locations
    assert ("surface",) in error_locations


def test_finalized_compiled_screen_keeps_public_shape_and_focus_ref() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="w1:input",
            window_id="w1",
            class_name="android.widget.EditText",
            resource_id="android:id/input",
            text=None,
            hint_text="Search settings",
            editable=True,
            focused=True,
            actions=("focus", "setText"),
            bounds=(10, 20, 500, 120),
        ),
        make_raw_node(
            rid="w1:button",
            window_id="w1",
            class_name="android.widget.Button",
            resource_id="android:id/button1",
            text="Search",
            editable=False,
            actions=("click",),
            focused=False,
            bounds=(10, 130, 260, 220),
        ),
        snapshot_id=2,
        captured_at="2026-04-07T00:00:01Z",
        windowless=True,
    )
    raw_compiled = SemanticCompiler().compile(1, snapshot)

    assert all(node.ref == "" for node in raw_compiled.ref_candidates())

    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=raw_compiled,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    assert all(node.ref == "" for node in raw_compiled.ref_candidates())
    assert payload["surface"]["focus"]["inputRef"] is not None
    validated = _assert_contract_public_screen_payload(payload)
    assert validated.surface.focus.input_ref == payload["surface"]["focus"]["inputRef"]
    refs = [
        node["ref"]
        for group in payload["groups"]
        for node in group["nodes"]
        if node.get("ref")
    ]
    assert refs
    assert set(refs) == set(finalized.registry.bindings.keys())
    focused_input = next(
        node
        for group in payload["groups"]
        for node in group["nodes"]
        if node.get("ref") == payload["surface"]["focus"]["inputRef"]
    )
    assert focused_input["actions"] == ["type"]
    assert focused_input["meta"] == {
        "resourceId": "android:id/input",
        "className": "android.widget.EditText",
    }


def test_finalized_dialog_blocking_screen_validates_shared_contract_order() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="dialog-root",
            window_id="w1",
            child_rids=("allow",),
            class_name="android.app.Dialog",
            text=None,
            pane_title="Allow access?",
            editable=False,
            focusable=False,
            actions=(),
            bounds=(0, 0, 500, 300),
        ),
        make_raw_node(
            rid="allow",
            window_id="w1",
            parent_rid="dialog-root",
            class_name="android.widget.Button",
            resource_id="android:id/button1",
            text="Allow",
            editable=False,
            actions=("click",),
            bounds=(100, 200, 240, 260),
        ),
        make_raw_node(
            rid="wifi",
            window_id="w1",
            class_name="android.widget.Switch",
            resource_id="android:id/switch_widget",
            text="Wi-Fi",
            state_description="On",
            editable=False,
            checkable=True,
            checked=True,
            actions=("click",),
            bounds=(0, 320, 500, 420),
        ),
        package_name="com.android.permissioncontroller",
        activity_name="GrantPermissionsActivity",
        windowless=True,
    )
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    validated = _assert_contract_public_screen_payload(payload)

    assert validated.surface.blocking_group == "dialog"
    assert [group["name"] for group in payload["groups"]] == [
        "dialog",
        "targets",
        "keyboard",
        "system",
        "context",
    ]
    dialog_group = next(
        group for group in payload["groups"] if group["name"] == "dialog"
    )
    assert dialog_group["nodes"][0]["label"] == "Allow"
    targets_group = next(
        group for group in payload["groups"] if group["name"] == "targets"
    )
    assert "actions" not in targets_group["nodes"][0]


def test_finalized_keyboard_blocking_screen_validates_shared_contract_order() -> None:
    snapshot = replace(
        make_contract_snapshot(
            make_raw_node(
                rid="w1:input",
                window_id="w1",
                class_name="android.widget.EditText",
                resource_id="android:id/input",
                text="Compose message",
                editable=True,
                focused=False,
                actions=("focus", "setText", "click"),
                bounds=(0, 100, 900, 180),
            ),
            make_raw_node(
                rid="ime:input",
                window_id="ime",
                class_name="android.widget.EditText",
                package_name="com.example.keyboard",
                text="Search emojis",
                editable=True,
                focused=True,
                actions=("focus", "setText", "submit"),
                bounds=(0, 1500, 900, 1580),
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
                bounds=(900, 1500, 1080, 1580),
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
                    root_rid="ime:input",
                ),
            ),
        ),
        ime=RawIme(visible=True, window_id="ime"),
    )
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    validated = _assert_contract_public_screen_payload(payload)

    assert validated.surface.blocking_group == "keyboard"
    assert [group["name"] for group in payload["groups"]] == [
        "keyboard",
        "targets",
        "system",
        "context",
        "dialog",
    ]
    assert payload["surface"]["keyboardVisible"] is True
    keyboard_group = next(
        group for group in payload["groups"] if group["name"] == "keyboard"
    )
    keyboard_input = next(
        node for node in keyboard_group["nodes"] if node["label"] == "Search emojis"
    )
    assert keyboard_input["ref"] == payload["surface"]["focus"]["inputRef"]
    assert keyboard_input["actions"] == ["type", "submit"]
    targets_group = next(
        group for group in payload["groups"] if group["name"] == "targets"
    )
    assert "actions" not in targets_group["nodes"][0]


def test_finalized_system_blocking_screen_validates_shared_contract_order() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="app:toggle",
            window_id="app",
            class_name="android.widget.Switch",
            resource_id="android:id/switch_widget",
            text="Bluetooth",
            state_description="Off",
            editable=False,
            checkable=True,
            checked=False,
            actions=("click",),
            bounds=(0, 320, 500, 420),
        ),
        make_raw_node(
            rid="sys:allow",
            window_id="sys",
            class_name="android.widget.Button",
            package_name="com.android.systemui",
            text="Allow",
            editable=False,
            focusable=False,
            actions=("click",),
            bounds=(780, 20, 1040, 120),
        ),
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        windows=(
            RawWindow(
                window_id="app",
                type="application",
                layer=1,
                package_name="com.android.settings",
                bounds=(0, 0, 1080, 2400),
                root_rid="app:toggle",
            ),
            RawWindow(
                window_id="sys",
                type="system",
                layer=2,
                package_name="com.android.systemui",
                bounds=(0, 0, 1080, 180),
                root_rid="sys:allow",
            ),
        ),
    )
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    validated = _assert_contract_public_screen_payload(payload)

    assert validated.surface.blocking_group == "system"
    assert [group["name"] for group in payload["groups"]] == [
        "system",
        "targets",
        "keyboard",
        "context",
        "dialog",
    ]
    system_group = next(
        group for group in payload["groups"] if group["name"] == "system"
    )
    assert system_group["nodes"][0]["label"] == "Allow"
    assert system_group["nodes"][0]["actions"] == ["tap"]
    targets_group = next(
        group for group in payload["groups"] if group["name"] == "targets"
    )
    assert "actions" not in targets_group["nodes"][0]


def test_artifact_payload_keeps_screen_metadata_outside_public_screen_dump() -> None:
    snapshot = RawSnapshot(
        snapshot_id=1,
        captured_at="2026-04-07T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )

    compiled_screen = SemanticCompiler().compile(1, snapshot)
    screen = compiled_screen.to_public_screen()
    artifact_payload = build_screen_artifact_payload(
        screen,
        RefRegistry(),
        sequence=compiled_screen.sequence,
        source_snapshot_id=compiled_screen.source_snapshot_id,
        captured_at=compiled_screen.captured_at,
    )

    assert artifact_payload["screenId"] == screen.screen_id
    assert artifact_payload["sequence"] == 1
    assert artifact_payload["sourceSnapshotId"] == 1
    assert artifact_payload["capturedAt"] == "2026-04-07T00:00:00Z"


def test_semantic_result_uses_full_public_screen_shape() -> None:
    screen = PublicScreen(
        screen_id="screen-123456789012",
        app=PublicApp(
            package_name="com.android.settings",
            activity_name="SettingsActivity",
        ),
        surface=PublicSurface(
            keyboard_visible=False,
            focus=PublicFocus(input_ref="n1"),
        ),
        groups=(
            PublicGroup(name="targets"),
            PublicGroup(name="keyboard"),
            PublicGroup(name="system"),
            PublicGroup(name="context"),
            PublicGroup(name="dialog"),
        ),
        omitted=(OmittedEntry(group="targets", reason="virtualized", count=27),),
        visible_windows=(),
        transient=(TransientItem(text="Saved", kind="toast"),),
    )

    result = build_semantic_success_result(
        command="observe",
        category="observe",
        source_screen_id=None,
        next_screen=screen,
        artifacts=None,
        continuity_status="none",
        execution_outcome="notApplicable",
        changed=None,
    )
    payload = result.model_dump(by_alias=True, mode="json")

    assert payload["screen"]["surface"] == {
        "keyboardVisible": False,
        "focus": {"inputRef": "n1"},
    }
    assert payload["screen"]["omitted"] == [
        {
            "group": "targets",
            "reason": "virtualized",
            "count": 27,
        }
    ]
    assert payload["screen"]["transient"] == [
        {
            "text": "Saved",
            "kind": "toast",
        }
    ]


def test_semantic_failure_builder_rejects_action_target_channel() -> None:
    screen = PublicScreen(
        screen_id="screen-123456789012",
        app=PublicApp(package_name="com.android.settings"),
        surface=PublicSurface(keyboard_visible=False, focus=PublicFocus()),
        groups=(
            PublicGroup(name="targets"),
            PublicGroup(name="keyboard"),
            PublicGroup(name="system"),
            PublicGroup(name="context"),
            PublicGroup(name="dialog"),
        ),
        omitted=(),
        visible_windows=(),
        transient=(),
    )
    action_target = ActionTargetPayload(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref="n1",
        dispatched_ref="n1",
        next_screen_id="screen-123456789012",
        next_ref="n1",
        identity_status="sameRef",
        evidence=("liveRef", "requestTarget", "focusConfirmation"),
    )

    failure_kwargs: dict[str, object] = {
        "command": "focus",
        "category": "transition",
        "code": SemanticResultCode.TARGET_NOT_ACTIONABLE,
        "message": "not actionable",
        "source_screen_id": "screen-00001",
        "current_screen": screen,
        "artifacts": None,
        "action_target": action_target,
    }
    with pytest.raises(TypeError, match="action_target"):
        build_semantic_failure_result(**failure_kwargs)

    payload = build_semantic_failure_result(
        command="focus",
        category="transition",
        code=SemanticResultCode.TARGET_NOT_ACTIONABLE,
        message="not actionable",
        source_screen_id="screen-00001",
        current_screen=screen,
        artifacts=None,
    ).model_dump(by_alias=True, mode="json")

    assert payload["ok"] is False
    assert payload["payloadMode"] == "full"
    assert "actionTarget" not in payload


@pytest.mark.parametrize(
    "code",
    [
        SemanticResultCode.DEVICE_UNAVAILABLE,
        SemanticResultCode.POST_ACTION_OBSERVATION_LOST,
    ],
)
def test_semantic_failure_builder_clears_artifacts_for_lost_truth_shapes(
    code: SemanticResultCode,
) -> None:
    payload = build_semantic_failure_result(
        command="tap",
        category="transition",
        code=code,
        message="No current screen truth is available.",
        execution_outcome=(
            "dispatched"
            if code is SemanticResultCode.POST_ACTION_OBSERVATION_LOST
            else "notApplicable"
        ),
        source_screen_id="screen-00006",
        current_screen=None,
        artifacts=ScreenArtifacts(
            screen_xml="/repo/.androidctl/artifacts/screens/screen-00007.xml",
            screenshot_png="/repo/.androidctl/screenshots/shot-001.png",
        ),
    ).model_dump(by_alias=True, mode="json")

    assert payload["payloadMode"] == "none"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert payload["artifacts"] == {}


def test_semantic_failure_builder_keeps_other_payload_light_artifacts() -> None:
    payload = build_semantic_failure_result(
        command="wait",
        category="wait",
        code=SemanticResultCode.WAIT_TIMEOUT,
        message="Condition was not satisfied before timeout.",
        source_screen_id="screen-00006",
        current_screen=None,
        artifacts=ScreenArtifacts(
            screen_xml="/repo/.androidctl/artifacts/screens/screen-00007.xml",
            screenshot_png="/repo/.androidctl/screenshots/shot-001.png",
        ),
    ).model_dump(by_alias=True, mode="json")

    assert payload["payloadMode"] == "none"
    assert payload["artifacts"] == {
        "screenXml": "/repo/.androidctl/artifacts/screens/screen-00007.xml",
        "screenshotPng": "/repo/.androidctl/screenshots/shot-001.png",
    }


def test_compiler_emits_scroll_container_with_scroll_directions_for_real_fixture() -> (
    None
):
    snapshot = _load_snapshot_fixture("chrome_scroll_after_snapshot.json")
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )
    _assert_contract_public_screen_payload(payload)

    targets_group = next(
        group for group in payload["groups"] if group["name"] == "targets"
    )
    scroll_container = next(
        node
        for node in targets_group["nodes"]
        if node.get("kind") == "container" and node.get("role") == "scroll-container"
    )

    assert scroll_container["ref"] is not None
    assert scroll_container["actions"] == ["scroll"]
    assert scroll_container["scrollDirections"] == ["down"]
    assert scroll_container["children"] == [
        {
            "kind": "text",
            "text": "openai - 百度",
        }
    ]


def test_real_scroll_fixture_rejects_forged_window_ref_outside_visible_windows() -> (
    None
):
    snapshot = _load_snapshot_fixture("chrome_scroll_after_snapshot.json")
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    assert payload["visibleWindows"] == []
    all_nodes = tuple(
        node
        for group in payload["groups"]
        for node in _iter_node_payloads(group["nodes"])
    )
    assert all("windowRef" not in node for node in all_nodes)

    targets_group = next(
        group for group in payload["groups"] if group["name"] == "targets"
    )
    scroll_container = next(
        node
        for node in targets_group["nodes"]
        if node.get("kind") == "container" and node.get("role") == "scroll-container"
    )
    scroll_container["windowRef"] = "w1"

    with pytest.raises(ValidationError, match="windowRef.*visibleWindows"):
        PublicScreen.model_validate(payload)


def test_real_scroll_fixture_round_trips_compiler_to_contract_to_xml() -> None:
    if str(ANDROIDCTL_SRC) not in sys.path:
        sys.path.append(str(ANDROIDCTL_SRC))
    from androidctl.renderers.xml import render_xml

    snapshot = _load_snapshot_fixture("chrome_scroll_after_snapshot.json")
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=SemanticCompiler().compile(1, snapshot),
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    result = build_semantic_success_result(
        command="observe",
        category="observe",
        source_screen_id=None,
        next_screen=finalized.compiled_screen.to_public_screen(),
        artifacts=None,
        continuity_status="none",
        execution_outcome="notApplicable",
        changed=None,
    )
    payload = result.model_dump(by_alias=True, mode="json")

    validated = CommandResultCore.model_validate(payload)
    assert validated.screen is not None
    _assert_contract_public_screen_payload(payload["screen"])
    xml = render_xml(validated)
    root = fromstring(xml)

    container = root.find("./screen/groups/targets/scroll-container")
    assert container is not None
    assert container.attrib["scrollDirections"] == "down"
    assert "role" not in container.attrib
    child_text = container.find("./literal")
    assert child_text is not None
    assert child_text.text == "openai - 百度"


def _public_screen_parity_payload(
    *,
    context_nodes: list[dict[str, object]],
    transient_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "screenId": "screen-123456789012",
        "app": {"packageName": "com.android.settings"},
        "surface": {
            "keyboardVisible": False,
            "focus": {},
        },
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {
                "name": "context",
                "nodes": context_nodes,
            },
            {"name": "dialog", "nodes": []},
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [] if transient_items is None else transient_items,
    }


def test_public_screen_daemon_contract_valid_text_transient_parity_smoke() -> None:
    payload = _public_screen_parity_payload(
        context_nodes=[
            {"kind": "text", "text": "Network & internet"},
            {"role": "text", "label": "Saved networks"},
        ],
        transient_items=[
            {"text": "Saved", "kind": "toast"},
        ],
    )

    screen = PublicScreen.model_validate(payload)

    context_nodes = public_group_nodes(screen, "context")
    assert context_nodes[0].kind == "text"
    assert context_nodes[0].text == "Network & internet"
    assert context_nodes[1].kind == "node"
    assert context_nodes[1].role == "text"
    assert context_nodes[1].label == "Saved networks"
    assert screen.transient[0].text == "Saved"
    assert screen.transient[0].kind == "toast"

    screen_payload = screen.model_dump(by_alias=True, mode="json")
    assert screen_payload["groups"][3]["nodes"] == [
        {"kind": "text", "text": "Network & internet"},
        {"role": "text", "label": "Saved networks"},
    ]
    assert screen_payload["transient"] == [{"text": "Saved", "kind": "toast"}]
    _assert_contract_public_screen_payload(screen_payload)


def test_public_screen_daemon_contract_invalid_alias_parity_smoke() -> None:
    payloads = (
        _public_screen_parity_payload(
            context_nodes=[
                {"kind": "text", "label": "Network & internet"},
            ]
        ),
        _public_screen_parity_payload(
            context_nodes=[],
            transient_items=[
                {"label": "Saved", "kind": "toast"},
            ],
        ),
    )

    for payload in payloads:
        with pytest.raises(ValidationError):
            PublicScreen.model_validate(payload)
        with pytest.raises(ValidationError):
            ContractPublicScreen.model_validate(payload)
