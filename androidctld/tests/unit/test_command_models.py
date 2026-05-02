from __future__ import annotations

import pytest

from androidctld.commands import command_models
from androidctld.commands.command_models import (
    AppWaitPredicate,
    GoneWaitPredicate,
    IdleWaitPredicate,
    ScreenChangeWaitPredicate,
    TextWaitPredicate,
    WaitCommand,
    WaitKind,
)
from androidctld.protocol import CommandKind


def test_command_models_exports_wait_command_and_wait_kinds() -> None:
    assert "WaitCommand" in command_models.__all__
    assert {"observe", "wait"} <= {command_kind.value for command_kind in CommandKind}
    assert {"gone", "screen-change"} <= {wait_kind.value for wait_kind in WaitKind}


@pytest.mark.parametrize(
    "command",
    [
        WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi")),
        WaitCommand(predicate=ScreenChangeWaitPredicate(source_screen_id="screen-1")),
        WaitCommand(predicate=GoneWaitPredicate(source_screen_id="screen-1", ref="n7")),
        WaitCommand(predicate=AppWaitPredicate(package_name="com.example.settings")),
        WaitCommand(predicate=IdleWaitPredicate()),
    ],
)
def test_wait_command_accepts_canonical_typed_predicates(
    command: WaitCommand,
) -> None:
    assert command.kind.value == "wait"


def test_wait_command_derives_wait_kind_from_predicate() -> None:
    command = WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi"))

    assert command.wait_kind is WaitKind.TEXT


def test_wait_command_rejects_untyped_predicate() -> None:
    with pytest.raises(ValueError, match="wait requires typed predicate"):
        WaitCommand(predicate=object())  # type: ignore[arg-type]


def test_wait_command_no_longer_accepts_flattened_optional_fields() -> None:
    with pytest.raises(TypeError):
        WaitCommand(  # type: ignore[call-arg]
            wait_kind=WaitKind.GONE,
            source_screen_id="screen-1",
            ref="n7",
            text="Wi-Fi",
        )
