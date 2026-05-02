from __future__ import annotations

from androidctld.actions.focused_input_admissibility import (
    keyboard_blocker_allows_public_type,
    keyboard_blocker_allows_semantic_type,
    keyboard_blocker_allows_submit_subject,
    public_node_is_focused_input,
    semantic_node_is_focused_input,
    submit_subject_is_cross_checked_focused_input,
)

from .support.semantic_screen import (
    make_compiled_screen,
    make_contract_screen,
    make_public_node,
    make_semantic_node,
)

_SCREEN_ID = "screen-00001"
_INPUT_REF = "n1"
_FOCUSED_RID = "w1:focused"


def _public_input(ref: str = _INPUT_REF):
    return make_public_node(
        ref=ref,
        role="input",
        label="Search",
        state=("focused",) if ref == _INPUT_REF else (),
        actions=("type",),
    )


def _semantic_input(
    *,
    raw_rid: str = _FOCUSED_RID,
    ref: str = _INPUT_REF,
    focused: bool = False,
):
    node = make_semantic_node(
        raw_rid=raw_rid,
        ref=ref,
        role="input",
        label="Search",
    )
    node.actions = ["type"]
    node.state = ["focused"] if focused else []
    return node


def test_public_focused_input_matches_current_public_ref() -> None:
    focused = _public_input()
    other = _public_input("n2")
    screen = make_contract_screen(
        screen_id=_SCREEN_ID,
        targets=(focused, other),
        input_ref=_INPUT_REF,
    )

    assert public_node_is_focused_input(screen, focused) is True
    assert public_node_is_focused_input(screen, other) is False


def test_semantic_focused_input_matches_current_raw_rid() -> None:
    focused = _semantic_input(focused=True)
    same_raw_target = _semantic_input(raw_rid=_FOCUSED_RID, ref="n9")
    other = _semantic_input(raw_rid="w1:other", ref="n2")
    screen = make_compiled_screen(
        _SCREEN_ID,
        fingerprint="focused-input",
        targets=[focused, other],
    )

    assert semantic_node_is_focused_input(screen, same_raw_target) is True
    assert semantic_node_is_focused_input(screen, other) is False


def test_submit_subject_requires_public_and_semantic_focus_match() -> None:
    public_focused = _public_input()
    public_other = _public_input("n2")
    semantic_focused = _semantic_input(focused=True)
    semantic_other = _semantic_input(raw_rid="w1:other", ref=_INPUT_REF)
    public_screen = make_contract_screen(
        screen_id=_SCREEN_ID,
        targets=(public_focused, public_other),
        input_ref=_INPUT_REF,
    )
    compiled_screen = make_compiled_screen(
        _SCREEN_ID,
        fingerprint="focused-input",
        targets=[semantic_focused, semantic_other],
    )

    assert (
        submit_subject_is_cross_checked_focused_input(
            public_screen,
            compiled_screen,
            public_focused,
            semantic_focused,
        )
        is True
    )
    assert (
        submit_subject_is_cross_checked_focused_input(
            public_screen,
            compiled_screen,
            public_other,
            semantic_focused,
        )
        is False
    )
    assert (
        submit_subject_is_cross_checked_focused_input(
            public_screen,
            compiled_screen,
            public_focused,
            semantic_other,
        )
        is False
    )


def test_keyboard_type_helpers_only_allow_type_action() -> None:
    public_focused = _public_input()
    semantic_focused = _semantic_input(focused=True)
    public_screen = make_contract_screen(
        screen_id=_SCREEN_ID,
        targets=(public_focused,),
        input_ref=_INPUT_REF,
        blocking_group="keyboard",
        keyboard_visible=True,
    )
    compiled_screen = make_compiled_screen(
        _SCREEN_ID,
        fingerprint="focused-input",
        targets=[semantic_focused],
    )

    assert (
        keyboard_blocker_allows_public_type(
            blocking_group="keyboard",
            action="type",
            screen=public_screen,
            node=public_focused,
        )
        is True
    )
    assert (
        keyboard_blocker_allows_public_type(
            blocking_group="keyboard",
            action="tap",
            screen=public_screen,
            node=public_focused,
        )
        is False
    )
    assert (
        keyboard_blocker_allows_semantic_type(
            blocking_group="keyboard",
            action="type",
            screen=compiled_screen,
            node=semantic_focused,
        )
        is True
    )
    assert (
        keyboard_blocker_allows_semantic_type(
            blocking_group="keyboard",
            action="tap",
            screen=compiled_screen,
            node=semantic_focused,
        )
        is False
    )


def test_keyboard_submit_subject_helper_does_not_allow_attributed_target() -> None:
    public_input = _public_input()
    public_button = make_public_node(
        ref="n2",
        role="button",
        label="Submit",
        actions=("tap",),
    )
    semantic_input = _semantic_input(focused=True)
    semantic_button = make_semantic_node(
        raw_rid="w1:submit",
        ref="n2",
        role="button",
        label="Submit",
    )
    public_screen = make_contract_screen(
        screen_id=_SCREEN_ID,
        targets=(public_input, public_button),
        input_ref=_INPUT_REF,
        blocking_group="keyboard",
        keyboard_visible=True,
    )
    compiled_screen = make_compiled_screen(
        _SCREEN_ID,
        fingerprint="focused-input",
        targets=[semantic_input, semantic_button],
    )

    assert (
        keyboard_blocker_allows_submit_subject(
            blocking_group="keyboard",
            public_screen=public_screen,
            compiled_screen=compiled_screen,
            public_node=public_input,
            semantic_node=semantic_input,
        )
        is True
    )
    assert (
        keyboard_blocker_allows_submit_subject(
            blocking_group="keyboard",
            public_screen=public_screen,
            compiled_screen=compiled_screen,
            public_node=public_button,
            semantic_node=semantic_button,
        )
        is False
    )
