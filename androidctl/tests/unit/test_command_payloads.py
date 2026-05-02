from __future__ import annotations

from collections.abc import Callable

import pytest
from androidctl_contracts.daemon_api import (
    AppPredicatePayload,
    GlobalActionCommandPayload,
    GonePredicatePayload,
    ScreenChangePredicatePayload,
    WaitCommandPayload,
)

from androidctl.command_payloads import (
    CliCommandPayload,
    LateBoundActionCommand,
    LateBoundGlobalActionCommand,
    LateBoundScreenRelativePredicate,
    LateBoundWaitCommand,
    build_global_action_command,
    build_ref_action_command,
    build_scroll_command,
    build_type_command,
    build_wait_command,
)


def command_display_payload(command: CliCommandPayload) -> dict[str, object]:
    if isinstance(command, LateBoundActionCommand):
        payload: dict[str, object] = {
            "kind": command.kind,
            "ref": command.ref,
        }
        if command.text is not None:
            payload["text"] = command.text
        if command.direction is not None:
            payload["direction"] = command.direction
        return payload
    if isinstance(command, LateBoundGlobalActionCommand):
        return {"kind": command.kind}
    if isinstance(command, LateBoundWaitCommand):
        predicate_payload: dict[str, object] = {"kind": command.predicate.kind}
        if command.predicate.ref is not None:
            predicate_payload["ref"] = command.predicate.ref
        payload: dict[str, object] = {
            "kind": "wait",
            "predicate": predicate_payload,
        }
        if command.timeout_ms is not None:
            payload["timeout_ms"] = command.timeout_ms
        return payload
    return command.model_dump(
        by_alias=False,
        exclude_none=True,
        exclude_defaults=True,
    )


def test_build_wait_command_returns_late_bound_wrapper_for_missing_screen_id() -> None:
    command = build_wait_command(
        predicate=LateBoundScreenRelativePredicate(kind="gone", ref="n7"),
        timeout_ms=1_234,
    )

    assert command == LateBoundWaitCommand(
        predicate=LateBoundScreenRelativePredicate(kind="gone", ref="n7"),
        timeout_ms=1_234,
    )
    assert command.bind("screen-1").model_dump(exclude_none=True) == {
        "kind": "wait",
        "predicate": {
            "kind": "gone",
            "ref": "n7",
            "sourceScreenId": "screen-1",
        },
        "timeoutMs": 1234,
    }


def test_build_wait_command_returns_shared_payload_for_bound_screen_relative_wait() -> (
    None
):
    command = build_wait_command(
        predicate=ScreenChangePredicatePayload(
            kind="screen-change",
            source_screen_id="screen-1",
        ),
        timeout_ms=250,
    )

    assert isinstance(command, WaitCommandPayload)
    assert command.model_dump(exclude_none=True) == {
        "kind": "wait",
        "predicate": {
            "kind": "screen-change",
            "sourceScreenId": "screen-1",
        },
        "timeoutMs": 250,
    }


def test_build_ref_action_command_returns_shared_payload_when_bound() -> None:
    command = build_ref_action_command(
        kind="tap",
        ref="n3",
        source_screen_id="screen-1",
    )

    assert command.model_dump(exclude_none=True) == {
        "kind": "tap",
        "ref": "n3",
        "sourceScreenId": "screen-1",
    }


def test_build_ref_action_command_returns_late_bound_wrapper_when_unbound() -> None:
    command = build_ref_action_command(
        kind="tap",
        ref="n3",
        source_screen_id=None,
    )

    assert command == LateBoundActionCommand(kind="tap", ref="n3")
    assert command.bind("screen-live").model_dump(exclude_none=True) == {
        "kind": "tap",
        "ref": "n3",
        "sourceScreenId": "screen-live",
    }


def test_build_global_action_command_returns_shared_payload_when_bound() -> None:
    command = build_global_action_command(
        kind="home",
        source_screen_id="screen-override",
    )

    assert isinstance(command, GlobalActionCommandPayload)
    assert command.model_dump(exclude_none=True) == {
        "kind": "home",
        "sourceScreenId": "screen-override",
    }


def test_build_global_action_command_returns_late_bound_wrapper_when_unbound() -> None:
    command = build_global_action_command(
        kind="home",
        source_screen_id=None,
    )

    assert command == LateBoundGlobalActionCommand(kind="home")
    assert command.bind("screen-live").model_dump(exclude_none=True) == {
        "kind": "home",
        "sourceScreenId": "screen-live",
    }
    assert command.bind(None).model_dump(exclude_none=True) == {"kind": "home"}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: build_ref_action_command(
            kind="tap",
            ref="n3",
            source_screen_id="   ",
        ),
        lambda: build_type_command(
            ref="n3",
            text="hello",
            source_screen_id="   ",
        ),
        lambda: build_scroll_command(
            ref="n3",
            direction="down",
            source_screen_id="   ",
        ),
        lambda: build_global_action_command(
            kind="home",
            source_screen_id="   ",
        ),
    ],
)
def test_command_builders_reject_blank_source_screen_id(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="source_screen_id must be non-empty"):
        factory()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            LateBoundWaitCommand(
                predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
                timeout_ms=500,
            ),
            {
                "kind": "wait",
                "predicate": {"kind": "screen-change"},
                "timeout_ms": 500,
            },
        ),
        (
            LateBoundWaitCommand(
                predicate=LateBoundScreenRelativePredicate(kind="gone", ref="n7"),
                timeout_ms=250,
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "ref": "n7",
                },
                "timeout_ms": 250,
            },
        ),
        (
            LateBoundActionCommand(kind="scroll", ref="n8", direction="down"),
            {
                "kind": "scroll",
                "ref": "n8",
                "direction": "down",
            },
        ),
        (
            LateBoundGlobalActionCommand(kind="home"),
            {
                "kind": "home",
            },
        ),
    ],
)
def test_command_display_payload_projects_late_bound_commands_without_binding(
    command: CliCommandPayload,
    expected: dict[str, object],
) -> None:
    assert command_display_payload(command) == expected


def test_late_bound_wait_command_rejects_negative_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_ms must be >= 0"):
        LateBoundWaitCommand(
            predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
            timeout_ms=-1,
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: LateBoundWaitCommand(
            predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
            timeout_ms=True,  # type: ignore[arg-type]
        ),
        lambda: build_wait_command(
            predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
            timeout_ms=True,  # type: ignore[arg-type]
        ),
        lambda: build_wait_command(
            predicate=ScreenChangePredicatePayload(
                kind="screen-change",
                source_screen_id="screen-1",
            ),
            timeout_ms=True,  # type: ignore[arg-type]
        ),
        lambda: build_wait_command(
            predicate=AppPredicatePayload(
                kind="app",
                package_name="com.example.settings",
            ),
            timeout_ms=True,  # type: ignore[arg-type]
        ),
    ],
)
def test_wait_command_builders_reject_bool_timeout(
    build: Callable[[], object],
) -> None:
    with pytest.raises(TypeError, match="timeout_ms must be an int, not bool"):
        build()


def test_late_bound_action_command_rejects_unknown_scroll_direction() -> None:
    with pytest.raises(ValueError, match="late-bound scroll command requires a valid"):
        LateBoundActionCommand(
            kind="scroll",
            ref="n8",
            direction="sideways",  # type: ignore[arg-type]
        )


def test_late_bound_action_command_rejects_blank_ref() -> None:
    with pytest.raises(ValueError, match="late-bound action command requires ref"):
        LateBoundActionCommand(kind="tap", ref="   ")


def test_late_bound_screen_relative_predicate_rejects_blank_gone_ref() -> None:
    with pytest.raises(
        ValueError, match="late-bound gone predicate requires a non-empty ref"
    ):
        LateBoundScreenRelativePredicate(kind="gone", ref="   ")


def test_late_bound_screen_relative_predicate_binds_gone_to_shared_contract() -> None:
    predicate = LateBoundScreenRelativePredicate(kind="gone", ref="n7")

    bound = predicate.bind("screen-1")

    assert isinstance(bound, GonePredicatePayload)
    assert bound.model_dump(exclude_none=True) == {
        "kind": "gone",
        "ref": "n7",
        "sourceScreenId": "screen-1",
    }
