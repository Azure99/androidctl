from __future__ import annotations

import pytest

from androidctl_contracts.public_screen import (
    PUBLIC_NODE_ACTION_VALUES,
    PUBLIC_NODE_ROLE_VALUES,
    PUBLIC_NODE_STATE_VALUES,
    SCROLL_DIRECTION_VALUES,
)
from androidctl_contracts.public_screen import (
    PublicScreen as ContractPublicScreen,
)
from androidctld.refs.service import RefRegistryBuilder
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.semantics.labels import extract_state, infer_role
from androidctld.semantics.public_models import PublicNode, public_group_nodes
from androidctld.semantics.surface import scroll_directions_for_raw_node
from androidctld.semantics.targets import (
    public_primary_actions_for,
    secondary_public_actions_for,
    semantic_actions_for,
)

from .support.semantic_screen import (
    make_contract_screen,
    make_contract_snapshot,
    make_raw_node,
)


def test_make_contract_snapshot_uses_shared_windowless_defaults() -> None:
    snapshot = make_contract_snapshot(windowless=True)

    assert snapshot.snapshot_id == 42
    assert snapshot.captured_at == "2026-04-08T00:00:00Z"
    assert snapshot.package_name == "com.android.settings"
    assert snapshot.activity_name == "SettingsActivity"
    assert snapshot.windows == ()


def test_make_contract_screen_supports_focus_blocking_and_dialog_groups() -> None:
    dialog = (
        PublicNode(
            ref="n9",
            role="button",
            label="Confirm",
            actions=("tap",),
        ),
    )

    screen = make_contract_screen(
        targets=(
            PublicNode(
                ref="n1",
                role="input",
                label="Search",
                actions=("type", "submit"),
            ),
        ),
        dialog=dialog,
        input_ref="n1",
        blocking_group="dialog",
    )

    assert screen.screen_id == "screen-00001"
    assert screen.app.package_name == "com.android.settings"
    assert screen.app.activity_name == "SettingsActivity"
    assert screen.surface.keyboard_visible is False
    assert screen.surface.focus.input_ref == "n1"
    assert screen.surface.blocking_group == "dialog"
    assert public_group_nodes(screen, "targets")[0].ref == "n1"
    assert public_group_nodes(screen, "dialog") == dialog


def test_make_contract_screen_allows_keyboard_visibility_to_be_set_explicitly() -> None:
    screen = make_contract_screen(
        targets=(
            PublicNode(
                ref="n1",
                role="input",
                label="Search",
                actions=("type",),
            ),
        ),
        input_ref="n1",
        keyboard_visible=True,
    )

    assert screen.surface.focus.input_ref == "n1"
    assert screen.surface.keyboard_visible is True


def test_semantic_producers_emit_only_contract_public_tokens() -> None:
    role_samples = {
        infer_role(
            make_raw_node(
                class_name=class_name,
                text=text,
                editable=editable,
                clickable=clickable,
                actions=actions,
            )
        )
        for class_name, text, editable, clickable, actions in (
            ("android.app.Dialog", "Dialog", False, False, ()),
            ("android.widget.TabWidget", "Tab", False, False, ()),
            ("android.inputmethodservice.Keyboard$Key", "A", False, False, ()),
            ("android.widget.EditText", "Search", True, False, ()),
            ("android.widget.Switch", "Wi-Fi", False, False, ()),
            ("android.widget.CheckBox", "Use network", False, False, ()),
            ("android.widget.RadioButton", "Choice", False, False, ()),
            ("android.widget.Button", "Save", False, True, ("click",)),
            ("android.widget.ImageView", "Logo", False, False, ()),
            ("android.view.View", "", False, True, ("click",)),
            ("android.widget.TextView", "Title", False, False, ()),
            ("android.view.ViewGroup", "", False, False, ()),
        )
    }
    role_samples.add("scroll-container")

    action_node = make_raw_node(
        editable=True,
        focused=True,
        scrollable=True,
        actions=("click", "longClick", "setText", "scrollForward", "submit"),
    )
    primary_actions = semantic_actions_for(action_node)
    action_samples = set(primary_actions)
    action_samples.update(
        public_primary_actions_for(
            anchor_node=action_node,
            role="input",
            primary_actions=primary_actions,
        )
    )
    action_samples.update(
        secondary_public_actions_for(
            anchor_node=action_node,
            role="input",
            primary_actions=primary_actions,
        )
    )
    action_samples.update(
        secondary_public_actions_for(
            anchor_node=make_raw_node(
                editable=True,
                focused=False,
                actions=("focus", "setText"),
            ),
            role="input",
            primary_actions=["type"],
        )
    )

    state_samples = set(
        extract_state(
            make_raw_node(
                checkable=True,
                checked=False,
                selected=True,
                enabled=False,
                focused=True,
                password=True,
                state_description="expanded, collapsed",
            )
        )
    )
    state_samples.update(
        extract_state(
            make_raw_node(
                checkable=True,
                checked=True,
            )
        )
    )

    scroll_direction_samples = set(
        scroll_directions_for_raw_node(
            make_raw_node(
                actions=(
                    "scrollForward",
                    "scrollBackward",
                    "scrollUp",
                    "scrollDown",
                    "scrollLeft",
                    "scrollRight",
                ),
            )
        )
    )

    assert role_samples <= set(PUBLIC_NODE_ROLE_VALUES)
    assert action_samples <= set(PUBLIC_NODE_ACTION_VALUES)
    assert state_samples <= set(PUBLIC_NODE_STATE_VALUES)
    assert scroll_direction_samples <= set(SCROLL_DIRECTION_VALUES)


@pytest.mark.parametrize(
    ("raw_actions", "expected_directions"),
    [
        (("scrollForward",), ("down",)),
        (("scrollDown",), ("down",)),
        (("scrollUp",), ("up",)),
        (("scrollLeft",), ("left",)),
        (("scrollRight",), ("right",)),
        (("scrollBackward",), ("backward",)),
        (("scrollForward", "scrollDown"), ("down",)),
    ],
)
def test_raw_scroll_actions_project_to_public_scroll_directions(
    raw_actions: tuple[str, ...],
    expected_directions: tuple[str, ...],
) -> None:
    assert (
        scroll_directions_for_raw_node(make_raw_node(actions=raw_actions))
        == expected_directions
    )


def test_compiled_scroll_container_public_screen_validates_contract_registry() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="results",
            window_id="w1",
            class_name="androidx.recyclerview.widget.RecyclerView",
            text="Results",
            editable=False,
            scrollable=True,
            actions=("scrollForward", "scrollBackward"),
        ),
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

    ContractPublicScreen.model_validate(payload)
    target_nodes = next(
        group["nodes"] for group in payload["groups"] if group["name"] == "targets"
    )
    assert target_nodes[0]["role"] == "scroll-container"
    assert target_nodes[0]["scrollDirections"] == ["down", "backward"]
