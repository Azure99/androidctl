from __future__ import annotations

from pathlib import Path

import pytest

from androidctld.actions.submit_routing import resolve_submit_route
from androidctld.commands.command_models import SubmitCommand
from androidctld.errors import DaemonError
from androidctld.refs.models import NodeHandle

from .support.runtime import build_runtime, build_screen_artifacts, install_screen_state
from .support.semantic_screen import (
    make_compiled_screen,
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
    make_semantic_node,
)

_SCREEN_ID = "screen-00042"
_SNAPSHOT_ID = 42
_INPUT_REF = "n1"
_SUBMIT_REF = "n2"
_INPUT_HANDLE = NodeHandle(snapshot_id=_SNAPSHOT_ID, rid="w1:input")


def _input_node(
    *,
    submit_refs: tuple[str, ...] = (_SUBMIT_REF,),
    actions: tuple[str, ...] = ("focus", "type"),
):
    return make_public_node(
        ref=_INPUT_REF,
        role="input",
        label="Search",
        state=("focused",),
        actions=actions,
    ).model_copy(update={"submit_refs": submit_refs})


def _submit_node(*, ref: str = _SUBMIT_REF):
    return make_public_node(
        ref=ref,
        role="button",
        label="Search",
        actions=("tap",),
    )


def _semantic_input(*, group: str = "targets"):
    node = make_semantic_node(
        raw_rid=_INPUT_HANDLE.rid,
        ref=_INPUT_REF,
        role="input",
        label="Search",
        group=group,
    )
    node.actions = ["focus", "type"]
    node.state = ["focused"]
    return node


def _semantic_submit(*, ref: str = _SUBMIT_REF, group: str = "targets"):
    node = make_semantic_node(
        raw_rid=f"w1:submit:{ref}",
        ref=ref,
        role="button",
        label="Search",
        group=group,
    )
    node.actions = ["tap"]
    return node


def _install_route_screen(
    tmp_path: Path,
    *,
    targets,
    compiled_targets,
    dialog=(),
    compiled_dialog=(),
    input_ref: str | None = _INPUT_REF,
    blocking_group: str | None = None,
):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=_SNAPSHOT_ID,
        current_screen_id=_SCREEN_ID,
    )
    compiled_screen = make_compiled_screen(
        _SCREEN_ID,
        sequence=_SNAPSHOT_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        fingerprint="submit-route",
        targets=list(compiled_targets),
    )
    compiled_screen.dialog = list(compiled_dialog)
    compiled_screen.blocking_group = blocking_group
    public_screen = make_contract_screen(
        screen_id=_SCREEN_ID,
        targets=tuple(targets),
        dialog=tuple(dialog),
        input_ref=input_ref,
        blocking_group=blocking_group,
        keyboard_visible=blocking_group == "keyboard",
    )
    install_screen_state(
        runtime,
        snapshot=make_contract_snapshot(snapshot_id=_SNAPSHOT_ID),
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=build_screen_artifacts(runtime, screen_id=_SCREEN_ID),
    )
    return runtime


def _resolve(runtime, *, handle: NodeHandle = _INPUT_HANDLE):
    return resolve_submit_route(
        runtime,
        SubmitCommand(ref=_INPUT_REF, source_screen_id=_SCREEN_ID),
        subject_handle=handle,
        source_evidence="liveRef",
    )


def test_submit_route_fails_closed_when_single_submit_ref_target_is_missing(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(submit_refs=("n9",)), _submit_node(ref="n9")),
        compiled_targets=(_semantic_input(),),
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_unresolved_target"
    assert error.value.details["ref"] == "n9"


def test_submit_route_prefers_single_submit_ref_over_direct_submit(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(
            _input_node(actions=("focus", "type", "submit")),
            _submit_node(),
        ),
        compiled_targets=(_semantic_input(), _semantic_submit()),
    )

    route = _resolve(runtime)

    assert route.route == "attributed"
    assert route.subject_ref == _INPUT_REF
    assert route.dispatched_ref == _SUBMIT_REF


def test_submit_route_uses_direct_submit_without_submit_refs(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(submit_refs=(), actions=("focus", "type", "submit")),),
        compiled_targets=(_semantic_input(),),
    )

    route = _resolve(runtime)

    assert route.route == "direct"
    assert route.subject_ref == _INPUT_REF
    assert route.dispatched_ref == _INPUT_REF


def test_submit_route_fails_closed_for_multiple_submit_refs(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(
            _input_node(
                submit_refs=(_SUBMIT_REF, "n3"),
                actions=("focus", "type", "submit"),
            ),
            _submit_node(),
            _submit_node(ref="n3"),
        ),
        compiled_targets=(
            _semantic_input(),
            _semantic_submit(),
            _semantic_submit(ref="n3"),
        ),
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_ambiguous"


def test_submit_route_fails_closed_when_subject_is_blocked(tmp_path: Path) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(),),
        compiled_targets=(_semantic_input(group="targets"),),
        dialog=(_submit_node(),),
        compiled_dialog=(_semantic_submit(group="dialog"),),
        blocking_group="dialog",
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_dialog"
    assert error.value.details["ref"] == _INPUT_REF


def test_submit_route_fails_closed_when_attributed_target_is_blocked(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_submit_node(),),
        compiled_targets=(_semantic_submit(group="targets"),),
        dialog=(_input_node(),),
        compiled_dialog=(_semantic_input(group="dialog"),),
        blocking_group="dialog",
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_dialog"
    assert error.value.details["ref"] == _SUBMIT_REF


def test_submit_route_allows_keyboard_focused_input_direct_submit(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(submit_refs=(), actions=("focus", "type", "submit")),),
        compiled_targets=(_semantic_input(group="targets"),),
        blocking_group="keyboard",
    )

    route = _resolve(runtime)

    assert route.route == "direct"
    assert route.subject_ref == _INPUT_REF
    assert route.dispatched_ref == _INPUT_REF


def test_submit_route_keeps_keyboard_focused_input_attributed_target_blocked(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(), _submit_node()),
        compiled_targets=(_semantic_input(group="targets"), _semantic_submit()),
        blocking_group="keyboard",
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_BLOCKED"
    assert error.value.details["reason"] == "blocked_by_keyboard"
    assert error.value.details["ref"] == _SUBMIT_REF


def test_submit_route_fails_closed_on_focus_mismatch(tmp_path: Path) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(), _submit_node()),
        compiled_targets=(_semantic_input(), _semantic_submit()),
        input_ref=None,
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime)

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "focus_mismatch"
    assert error.value.details["ref"] == _INPUT_REF


def test_submit_route_fails_closed_on_non_current_subject_handle_snapshot(
    tmp_path: Path,
) -> None:
    runtime = _install_route_screen(
        tmp_path,
        targets=(_input_node(), _submit_node()),
        compiled_targets=(_semantic_input(), _semantic_submit()),
    )

    with pytest.raises(DaemonError) as error:
        _resolve(runtime, handle=NodeHandle(snapshot_id=41, rid=_INPUT_HANDLE.rid))

    assert error.value.code == "TARGET_NOT_ACTIONABLE"
    assert error.value.details["reason"] == "submit_route_stale_subject_handle"
