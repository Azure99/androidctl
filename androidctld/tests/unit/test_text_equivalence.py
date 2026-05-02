from __future__ import annotations

from androidctld.actions.type_confirmation import matches_typed_value
from androidctld.commands.command_models import TypeCommand
from androidctld.refs.service import fingerprint_for_candidate
from androidctld.semantics.compiler import (
    SemanticCompiler,
    SemanticNode,
)
from androidctld.semantics.models import SemanticMeta
from androidctld.text_equivalence import (
    canonical_text_key,
    semantic_state_description_remainder,
)
from androidctld.waits.matcher import matches_text

from .support.semantic_screen import make_contract_snapshot, make_raw_node


def make_snapshot(
    *,
    state_description: str | None = None,
    text: str | None = None,
    resource_id: str | None = None,
    class_name: str = "android.widget.TextView",
    clickable: bool = False,
    actions: tuple[str, ...] = (),
):
    return make_contract_snapshot(
        make_raw_node(
            rid="w1:0.1",
            window_id="w1",
            class_name=class_name,
            resource_id=resource_id,
            text=text,
            state_description=state_description,
            bounds=(0, 0, 10, 10),
            clickable=clickable,
            editable=False,
            focusable=False,
            actions=actions,
        ),
        snapshot_id=1,
        captured_at="2026-03-17T00:00:00Z",
        activity_name="com.android.settings.Settings",
        windowless=True,
    )


def make_semantic_candidate(*, label: str) -> SemanticNode:
    return SemanticNode(
        raw_rid="w1:0.1",
        role="button",
        label=label,
        state=[],
        actions=["tap"],
        bounds=(0, 0, 10, 10),
        meta=SemanticMeta(
            resource_id="android:id/button1", class_name="android.widget.Button"
        ),
        targetable=True,
        score=100,
        group="targets",
        parent_role="container",
        parent_label="Network",
        sibling_labels=["Bluetooth"],
        relative_bounds=(0, 0, 10, 10),
    )


def test_semantic_state_description_remainder_drops_pure_state_token() -> None:
    assert semantic_state_description_remainder("Expanded") == ""


def test_semantic_state_description_remainder_keeps_non_state_prefix() -> None:
    assert semantic_state_description_remainder("Wi-Fi, Expanded") == "Wi-Fi"


def test_semantic_state_description_remainder_handles_full_width_state_token() -> None:
    assert semantic_state_description_remainder("Wi-Fi, Ｅｘｐａｎｄｅｄ") == "Wi-Fi"


def test_wait_matcher_uses_only_semantic_state_description_remainder() -> None:
    snapshot = make_snapshot(state_description="Connected, Expanded")
    assert matches_text(snapshot, "connected")
    assert not matches_text(snapshot, "expanded")


def test_wait_matcher_searches_only_raw_surface_text() -> None:
    snapshot = make_snapshot()
    assert not matches_text(snapshot, "connect")


def test_canonical_text_key_normalizes_nfkc_casefold_and_whitespace() -> None:
    assert (
        canonical_text_key("  Ｃｏｎｎｅｃｔｅｄ\u3000\u3000Ｓｔａｔｕｓ  ")
        == "connected status"
    )


def test_canonical_text_key_preserves_punctuation() -> None:
    assert canonical_text_key(" Wi-Fi_Status ") == "wi-fi_status"
    assert canonical_text_key("Wi-Fi_Status") != canonical_text_key("wi fi status")


def test_semantic_compiler_treats_full_width_state_only_value_as_state_not_label() -> (
    None
):
    snapshot = make_snapshot(
        state_description="Ｅｘｐａｎｄｅｄ",
        resource_id="android:id/advanced_settings",
        class_name="android.widget.Button",
        clickable=True,
        actions=("tap",),
    )
    compiled_screen = SemanticCompiler().compile(1, snapshot)
    assert compiled_screen.targets[0].label == "advanced_settings"
    assert "expanded" in compiled_screen.targets[0].state


def test_type_confirmation_and_ref_fingerprint_share_canonical_key() -> None:
    candidate = make_semantic_candidate(
        label="  Ｃｏｎｎｅｃｔｅｄ\u3000\u3000Ｓｔａｔｕｓ  "
    )
    fingerprint = fingerprint_for_candidate(candidate)
    assert canonical_text_key("connected   status") == fingerprint.normalized_label
    assert matches_typed_value(
        TypeCommand(ref="n1", source_screen_id="scr-1", text="connected   status"),
        "  Ｃｏｎｎｅｃｔｅｄ\u3000\u3000Ｓｔａｔｕｓ  ",
    )


def test_type_confirmation_uses_replace_only_matching() -> None:
    command = TypeCommand(ref="n1", source_screen_id="scr-1", text=" settings")
    assert not matches_typed_value(command, "wifisettings")
    assert not matches_typed_value(command, "wifi settings")
    assert matches_typed_value(command, " settings")
