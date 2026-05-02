"""Pure focused-input admission predicates for action validation."""

from __future__ import annotations

from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.semantics.public_models import PublicNode, PublicScreen


def public_focused_input_ref(screen: PublicScreen) -> str | None:
    return screen.surface.focus.input_ref


def semantic_focused_input_ref(screen: CompiledScreen) -> str | None:
    return screen.focused_input_ref()


def public_node_is_focused_input(
    screen: PublicScreen,
    node: PublicNode,
) -> bool:
    return (
        node.role == "input"
        and node.ref is not None
        and public_focused_input_ref(screen) == node.ref
    )


def semantic_node_is_focused_input(
    screen: CompiledScreen,
    node: SemanticNode,
) -> bool:
    focused_node = screen.focused_input_node()
    return (
        node.role == "input"
        and focused_node is not None
        and focused_node.raw_rid == node.raw_rid
    )


def submit_subject_is_cross_checked_focused_input(
    public_screen: PublicScreen,
    compiled_screen: CompiledScreen,
    public_node: PublicNode,
    semantic_node: SemanticNode,
) -> bool:
    return public_node_is_focused_input(
        public_screen,
        public_node,
    ) and semantic_node_is_focused_input(compiled_screen, semantic_node)


def keyboard_blocker_allows_public_type(
    *,
    blocking_group: str | None,
    action: str,
    screen: PublicScreen,
    node: PublicNode,
) -> bool:
    return (
        blocking_group == "keyboard"
        and action == "type"
        and public_node_is_focused_input(screen, node)
    )


def keyboard_blocker_allows_semantic_type(
    *,
    blocking_group: str | None,
    action: str,
    screen: CompiledScreen,
    node: SemanticNode,
) -> bool:
    return (
        blocking_group == "keyboard"
        and action == "type"
        and semantic_node_is_focused_input(screen, node)
    )


def keyboard_blocker_allows_submit_subject(
    *,
    blocking_group: str | None,
    public_screen: PublicScreen,
    compiled_screen: CompiledScreen,
    public_node: PublicNode,
    semantic_node: SemanticNode,
) -> bool:
    return (
        blocking_group == "keyboard"
        and submit_subject_is_cross_checked_focused_input(
            public_screen,
            compiled_screen,
            public_node,
            semantic_node,
        )
    )


def blocked_by_group_fields(
    *,
    blocking_group: str,
    ref: str | None,
) -> dict[str, object]:
    return {
        "reason": f"blocked_by_{blocking_group}",
        "ref": ref,
        "blockingGroup": blocking_group,
    }


def focus_mismatch_fields(
    *,
    ref: str | None,
    focused_input_ref: str | None,
) -> dict[str, object]:
    return {
        "reason": "focus_mismatch",
        "ref": ref,
        "focusedInputRef": focused_input_ref,
    }
