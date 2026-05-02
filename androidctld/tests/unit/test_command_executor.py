from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from androidctl_contracts import daemon_api as wire_api
from androidctld.commands.assembly import assemble_command_service
from androidctld.commands.command_models import (
    ConnectCommand,
    GlobalCommand,
    GoneWaitPredicate,
    IdleWaitPredicate,
    ListAppsCommand,
    ObserveCommand,
    OpenCommand,
    ScreenChangeWaitPredicate,
    ScreenshotCommand,
    TapCommand,
    WaitCommand,
)
from androidctld.commands.dispatch import CommandDispatch
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.open_targets import OpenAppTarget
from androidctld.commands.registry import COMMAND_SPECS
from androidctld.device.types import ConnectionConfig
from androidctld.protocol import ConnectionMode

from ..support.runtime_store import runtime_store_for_workspace


def build_executor() -> tuple[CommandExecutor, list[str]]:
    handled: list[str] = []
    executor = CommandExecutor(
        handlers={
            "observe": lambda *, command: (
                handled.append(command.kind.value)
                or {
                    "command": command.kind.value,
                }
            )
        }
    )
    return executor, handled


def test_executor_dispatches_typed_command_to_handler(tmp_path: Path) -> None:
    del tmp_path
    executor, handled = build_executor()

    outcome = executor.run(command=ObserveCommand())

    assert outcome == {"command": "observe"}
    assert handled == ["observe"]


class _UnexpectedHandleHandler:
    def __init__(self, name: str) -> None:
        self._name = name

    def handle(self, *, command: Any) -> dict[str, object]:
        raise AssertionError(
            f"unexpected {self._name} handler call for {command.kind.value!r}"
        )


class _UnexpectedActionHandler:
    def handle_open(self, *, command: Any) -> dict[str, object]:
        raise AssertionError(
            f"unexpected action open handler call for {command.kind.value!r}"
        )

    def handle_ref_action(self, *, command: Any) -> dict[str, object]:
        raise AssertionError(
            f"unexpected action ref handler call for {command.kind.value!r}"
        )

    def handle_global_action(self, *, command: Any) -> dict[str, object]:
        raise AssertionError(
            f"unexpected action global handler call for {command.kind.value!r}"
        )


class _UnexpectedWaitHandler:
    def handle_service_wait(self, *, command: Any) -> dict[str, object]:
        raise AssertionError(f"unexpected wait handler call for {command.kind.value!r}")


def _build_dispatch(
    *,
    connect_handler: Any | None = None,
    observe_handler: Any | None = None,
    list_apps_handler: Any | None = None,
    action_handler: Any | None = None,
    wait_handler: Any | None = None,
    screenshot_handler: Any | None = None,
) -> CommandDispatch:
    return CommandDispatch(
        connect_handler=cast(
            Any, connect_handler or _UnexpectedHandleHandler("connect")
        ),
        observe_handler=cast(
            Any, observe_handler or _UnexpectedHandleHandler("observe")
        ),
        list_apps_handler=cast(
            Any, list_apps_handler or _UnexpectedHandleHandler("listApps")
        ),
        action_handler=cast(Any, action_handler or _UnexpectedActionHandler()),
        wait_handler=cast(Any, wait_handler or _UnexpectedWaitHandler()),
        screenshot_handler=cast(
            Any, screenshot_handler or _UnexpectedHandleHandler("screenshot")
        ),
    )


def test_command_assembly_builds_dispatch_backed_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_handlers: dict[str, Any] | None = None
    original_init = CommandExecutor.__init__

    def _capture_init(self: CommandExecutor, *, handlers: dict[str, Any]) -> None:
        nonlocal captured_handlers
        captured_handlers = handlers
        original_init(self, handlers=handlers)

    monkeypatch.setattr(CommandExecutor, "__init__", _capture_init)

    assembly = assemble_command_service(
        runtime_store=runtime_store_for_workspace(tmp_path)
    )
    expected_handlers = assembly.dispatch.build_handlers()

    assert isinstance(assembly.dispatch, CommandDispatch)
    assert captured_handlers is not None
    assert captured_handlers == expected_handlers
    assert set(captured_handlers) == set(COMMAND_SPECS)

    for daemon_kind, spec in COMMAND_SPECS.items():
        handler = captured_handlers[daemon_kind]

        assert getattr(handler, "__self__", None) is assembly.dispatch
        assert getattr(handler, "__func__", None) is getattr(
            CommandDispatch,
            spec.dispatch_method_name,
        )


def test_command_dispatches_internal_open_command() -> None:
    seen: dict[str, Any] = {}

    class _ActionHandler:
        def handle_open(self, *, command: Any) -> dict[str, object]:
            seen["command"] = command
            return {"command": "open"}

    dispatch = _build_dispatch(action_handler=_ActionHandler())

    outcome = dispatch.execute_open(
        command=OpenCommand(target=OpenAppTarget(package_name="com.example.app"))
    )

    assert outcome == {"command": "open"}
    assert seen["command"].kind.value == "open"
    assert seen["command"].target.package_name == "com.example.app"


def test_command_dispatches_internal_observe_command() -> None:
    seen: dict[str, Any] = {}

    class _ObserveHandler:
        def handle(self, *, command: Any) -> dict[str, object]:
            seen["command"] = command
            return {"command": "observe"}

    dispatch = _build_dispatch(observe_handler=_ObserveHandler())

    outcome = dispatch.execute_observe(command=ObserveCommand())

    assert outcome == {"command": "observe"}
    assert seen["command"].kind.value == "observe"
    assert type(seen["command"]).__name__ == "ObserveCommand"


def test_command_dispatches_internal_connect_command() -> None:
    seen: dict[str, Any] = {}

    class _ConnectHandler:
        def handle(self, *, command: Any) -> dict[str, object]:
            seen["command"] = command
            return {"command": "connect", "ok": True}

    dispatch = _build_dispatch(connect_handler=_ConnectHandler())

    outcome = dispatch.execute_connect(
        command=ConnectCommand(
            connection=ConnectionConfig(
                mode=ConnectionMode.ADB,
                token="device-token",
                serial="emulator-5554",
            )
        )
    )

    assert outcome == {"command": "connect", "ok": True}
    assert seen["command"].kind.value == "connect"
    assert seen["command"].connection.mode is ConnectionMode.ADB
    assert seen["command"].connection.token == "device-token"
    assert seen["command"].connection.serial == "emulator-5554"


def test_command_dispatches_internal_list_apps_command() -> None:
    seen: dict[str, Any] = {}

    class _ListAppsHandler:
        def handle(self, *, command: Any) -> dict[str, object]:
            seen["command"] = command
            return {"command": "list-apps", "ok": True, "apps": []}

    dispatch = _build_dispatch(list_apps_handler=_ListAppsHandler())

    outcome = dispatch.execute_list_apps(command=ListAppsCommand())

    assert outcome == {"command": "list-apps", "ok": True, "apps": []}
    assert seen["command"].kind.value == "listApps"
    assert type(seen["command"]).__name__ == "ListAppsCommand"


def test_command_dispatches_internal_actions() -> None:
    seen: dict[str, Any] = {}

    class _ActionHandler:
        def handle_ref_action(self, *, command: Any) -> dict[str, object]:
            seen["ref"] = command
            return {"command": command.kind.value}

        def handle_global_action(
            self,
            *,
            command: Any,
        ) -> dict[str, object]:
            seen["global"] = command
            return {"command": command.action}

    dispatch = _build_dispatch(action_handler=_ActionHandler())

    ref_outcome = dispatch.execute_ref_action(
        command=TapCommand(ref="n1", source_screen_id="screen-1")
    )
    global_outcome = dispatch.execute_global_action(
        command=GlobalCommand(action="back", source_screen_id="screen-9")
    )

    assert ref_outcome == {"command": "tap"}
    assert seen["ref"].kind.value == "tap"
    assert seen["ref"].ref == "n1"
    assert global_outcome == {"command": "back"}
    assert seen["global"].kind.value == "global"
    assert seen["global"].action == "back"
    assert seen["global"].source_screen_id == "screen-9"


def test_command_dispatches_internal_wait() -> None:
    seen: dict[str, list[Any]] = {"service": []}

    class _WaitHandler:
        def handle_service_wait(self, *, command: Any) -> dict[str, object]:
            seen["service"].append(command)
            return {"path": "service"}

    dispatch = _build_dispatch(wait_handler=_WaitHandler())

    service_outcome = dispatch.execute_wait(
        command=WaitCommand(predicate=IdleWaitPredicate())
    )
    screen_relative_outcome = dispatch.execute_wait(
        command=WaitCommand(
            predicate=GoneWaitPredicate(source_screen_id="screen-1", ref="n7"),
        )
    )

    assert service_outcome == {"path": "service"}
    assert seen["service"][0].kind.value == "wait"
    assert seen["service"][0].wait_kind.value == "idle"
    assert screen_relative_outcome == {"path": "service"}
    assert seen["service"][1].kind.value == "wait"
    assert seen["service"][1].wait_kind.value == "gone"
    assert seen["service"][1].predicate.ref == "n7"


def test_command_dispatch_screen_relative_wait_uses_service_wait_handler() -> None:
    seen: dict[str, Any] = {}

    class _WaitHandler:
        def handle_service_wait(self, *, command: Any) -> dict[str, object]:
            seen["service"] = command
            return {"path": "service"}

    dispatch = _build_dispatch(wait_handler=_WaitHandler())

    outcome = dispatch.execute_wait(
        command=WaitCommand(
            predicate=ScreenChangeWaitPredicate(source_screen_id="screen-1"),
        )
    )

    assert outcome == {"path": "service"}
    assert seen["service"].kind.value == "wait"
    assert seen["service"].wait_kind.value == "screen-change"
    assert seen["service"].predicate.source_screen_id == "screen-1"


@pytest.mark.parametrize(
    "predicate",
    [
        {"kind": "screen-change", "sourceScreenId": "screen-1"},
        {"kind": "gone", "sourceScreenId": "screen-1", "ref": "n7"},
        {"kind": "app", "packageName": "com.example.settings"},
    ],
)
def test_command_service_rejects_wire_wait_payload_passthrough(
    predicate: dict[str, object],
) -> None:
    seen: dict[str, Any] = {}

    class _WaitHandler:
        def handle_service_wait(self, *, command: Any) -> dict[str, object]:
            seen["service"] = command
            return {"path": "service"}

    dispatch = _build_dispatch(wait_handler=_WaitHandler())

    try:
        dispatch.execute_wait(
            command=wire_api.WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": predicate,
                }
            )
        )
    except TypeError as error:
        assert str(error) == "wait handler received 'wait' command"
    else:
        raise AssertionError("shared service wait should be rejected")

    assert seen == {}


def test_command_dispatches_internal_screenshot_command() -> None:
    seen: dict[str, Any] = {}

    class _ScreenshotHandler:
        def handle(self, *, command: Any) -> dict[str, object]:
            seen["command"] = command
            return {"command": "screenshot", "ok": True}

    dispatch = _build_dispatch(screenshot_handler=_ScreenshotHandler())

    outcome = dispatch.execute_screenshot(command=ScreenshotCommand())

    assert outcome == {"command": "screenshot", "ok": True}
    assert seen["command"].kind.value == "screenshot"
    assert type(seen["command"]).__name__ == "ScreenshotCommand"
