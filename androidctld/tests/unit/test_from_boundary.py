from __future__ import annotations

import pytest

import androidctld.commands.from_boundary as from_boundary_module
from androidctl_contracts.command_catalog import DAEMON_COMMAND_KINDS
from androidctl_contracts.daemon_api import (
    ConnectCommandPayload,
    GlobalActionCommandPayload,
    ListAppsCommandPayload,
    ObserveCommandPayload,
    OpenCommandPayload,
    RefActionCommandPayload,
    ScreenshotCommandPayload,
    ScrollCommandPayload,
    TypeCommandPayload,
    WaitCommandPayload,
)
from androidctld.commands.command_models import (
    AppWaitPredicate,
    GoneWaitPredicate,
    ScreenChangeWaitPredicate,
)
from androidctld.commands.from_boundary import (
    compile_connect_command,
    compile_global_action_command,
    compile_list_apps_command,
    compile_observe_command,
    compile_open_command,
    compile_ref_action_command,
    compile_screenshot_command,
    compile_service_wait_command,
)

_FROM_BOUNDARY_COMPILED_KINDS = {
    "connect",
    "observe",
    "listApps",
    "open",
    "tap",
    "longTap",
    "focus",
    "submit",
    "type",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
    "wait",
    "screenshot",
}


def test_from_boundary_documents_compiled_command_kinds() -> None:
    assert _FROM_BOUNDARY_COMPILED_KINDS == DAEMON_COMMAND_KINDS
    assert hasattr(from_boundary_module, "compile_connect_command")
    assert hasattr(from_boundary_module, "compile_list_apps_command")
    assert hasattr(from_boundary_module, "compile_screenshot_command")


def test_compile_connect_command_uses_adb_internal_default_port() -> None:
    command = ConnectCommandPayload.model_validate(
        {
            "kind": "connect",
            "connection": {
                "mode": "adb",
                "token": "device-token",
                "serial": "emulator-5554",
            },
        }
    )

    compiled = compile_connect_command(command)

    assert compiled.kind.value == "connect"
    assert compiled.connection.mode.value == "adb"
    assert compiled.connection.token == "device-token"
    assert compiled.connection.serial == "emulator-5554"
    assert compiled.connection.port == 17171


def test_compile_connect_command_preserves_lan_explicit_port() -> None:
    command = ConnectCommandPayload.model_validate(
        {
            "kind": "connect",
            "connection": {
                "mode": "lan",
                "token": "device-token",
                "host": "192.168.0.10",
                "port": 18181,
            },
        }
    )

    compiled = compile_connect_command(command)

    assert compiled.connection.mode.value == "lan"
    assert compiled.connection.host == "192.168.0.10"
    assert compiled.connection.port == 18181


def test_compile_observe_command_builds_internal_observe_command() -> None:
    compiled = compile_observe_command(ObserveCommandPayload(kind="observe"))

    assert compiled.kind.value == "observe"


def test_compile_list_apps_command_builds_internal_list_apps_command() -> None:
    compiled = compile_list_apps_command(ListAppsCommandPayload(kind="listApps"))

    assert compiled.kind.value == "listApps"


def test_compile_screenshot_command_builds_internal_screenshot_command() -> None:
    compiled = compile_screenshot_command(ScreenshotCommandPayload(kind="screenshot"))

    assert compiled.kind.value == "screenshot"


def test_compile_open_command_builds_internal_open_command() -> None:
    command = OpenCommandPayload.model_validate(
        {"kind": "open", "target": {"kind": "app", "value": "com.example.app"}}
    )

    compiled = compile_open_command(command)

    assert compiled.kind.value == "open"
    assert compiled.target.package_name == "com.example.app"


def test_compile_open_command_builds_internal_url_open_command() -> None:
    command = OpenCommandPayload.model_validate(
        {"kind": "open", "target": {"kind": "url", "value": "https://example.test"}}
    )

    compiled = compile_open_command(command)

    assert compiled.kind.value == "open"
    assert compiled.target.url == "https://example.test"


@pytest.mark.parametrize(
    ("payload", "expected_kind", "expected_value"),
    [
        (
            RefActionCommandPayload(
                kind="tap",
                ref="n1",
                source_screen_id="screen-1",
            ),
            "tap",
            "n1",
        ),
        (
            RefActionCommandPayload(
                kind="longTap",
                ref="n1",
                source_screen_id="screen-1",
            ),
            "longTap",
            "n1",
        ),
        (
            RefActionCommandPayload(
                kind="focus",
                ref="n4",
                source_screen_id="screen-4",
            ),
            "focus",
            "n4",
        ),
        (
            RefActionCommandPayload(
                kind="submit",
                ref="n5",
                source_screen_id="screen-5",
            ),
            "submit",
            "n5",
        ),
        (
            TypeCommandPayload(
                kind="type",
                ref="n2",
                source_screen_id="screen-2",
                text="hello",
            ),
            "type",
            "hello",
        ),
        (
            ScrollCommandPayload(
                kind="scroll",
                ref="n3",
                source_screen_id="screen-3",
                direction="down",
            ),
            "scroll",
            "down",
        ),
    ],
)
def test_compile_ref_action_command_builds_internal_commands(
    payload: RefActionCommandPayload | TypeCommandPayload | ScrollCommandPayload,
    expected_kind: str,
    expected_value: str,
) -> None:
    compiled = compile_ref_action_command(payload)

    assert compiled.kind.value == expected_kind
    if expected_kind == "type":
        assert compiled.text == expected_value
    elif expected_kind == "scroll":
        assert compiled.direction == expected_value
    else:
        assert compiled.ref == expected_value


def test_compile_global_action_command_builds_internal_global_command() -> None:
    for action in ("back", "home", "recents", "notifications"):
        compiled = compile_global_action_command(
            GlobalActionCommandPayload(kind=action, source_screen_id="screen-1")
        )

        assert compiled.kind.value == "global"
        assert compiled.action == action
        assert compiled.source_screen_id == "screen-1"


def test_compile_global_action_command_allows_missing_source_screen_id() -> None:
    compiled = compile_global_action_command(
        GlobalActionCommandPayload.model_validate({"kind": "back"})
    )

    assert compiled.kind.value == "global"
    assert compiled.action == "back"
    assert compiled.source_screen_id is None


def test_compile_service_wait_command_only_handles_service_backed_predicates() -> None:
    text_wait = compile_service_wait_command(
        WaitCommandPayload.model_validate(
            {
                "kind": "wait",
                "predicate": {"kind": "text-present", "text": "Wi-Fi"},
                "timeoutMs": 250,
            }
        )
    )
    app_wait = compile_service_wait_command(
        WaitCommandPayload.model_validate(
            {
                "kind": "wait",
                "predicate": {"kind": "app", "packageName": "com.example.settings"},
            }
        )
    )
    idle_wait = compile_service_wait_command(
        WaitCommandPayload.model_validate(
            {
                "kind": "wait",
                "predicate": {"kind": "idle"},
            }
        )
    )
    screen_change_wait = compile_service_wait_command(
        WaitCommandPayload.model_validate(
            {
                "kind": "wait",
                "predicate": {
                    "kind": "screen-change",
                    "sourceScreenId": "screen-1",
                },
                "timeoutMs": 500,
            }
        )
    )
    gone_wait = compile_service_wait_command(
        WaitCommandPayload.model_validate(
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "sourceScreenId": "screen-1",
                    "ref": "n7",
                },
            }
        )
    )

    assert text_wait.kind.value == "wait"
    assert text_wait.wait_kind.value == "text"
    assert text_wait.timeout_ms == 250
    assert app_wait.kind.value == "wait"
    assert app_wait.wait_kind.value == "app"
    assert isinstance(app_wait.predicate, AppWaitPredicate)
    assert app_wait.predicate.package_name == "com.example.settings"
    assert idle_wait.kind.value == "wait"
    assert idle_wait.wait_kind.value == "idle"
    assert screen_change_wait.kind.value == "wait"
    assert screen_change_wait.wait_kind.value == "screen-change"
    assert isinstance(screen_change_wait.predicate, ScreenChangeWaitPredicate)
    assert screen_change_wait.predicate.source_screen_id == "screen-1"
    assert screen_change_wait.timeout_ms == 500
    assert gone_wait.kind.value == "wait"
    assert gone_wait.wait_kind.value == "gone"
    assert isinstance(gone_wait.predicate, GoneWaitPredicate)
    assert gone_wait.predicate.source_screen_id == "screen-1"
    assert gone_wait.predicate.ref == "n7"
