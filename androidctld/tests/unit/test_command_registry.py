from __future__ import annotations

from androidctl_contracts.command_catalog import (
    daemon_command_kinds_for_route,
    runtime_close_entry,
)
from androidctld.commands.command_models import GlobalCommand
from androidctld.commands.registry import COMMAND_SPECS, resolve_command_spec

_REPRESENTATIVE_COMMAND_REGISTRY: dict[str, tuple[str, str]] = {
    "connect": ("connect", "execute_connect"),
    "observe": ("observe", "execute_observe"),
    "listApps": ("list_apps", "execute_list_apps"),
    "open": ("open", "execute_open"),
    "longTap": ("ref_action", "execute_ref_action"),
    "type": ("type", "execute_ref_action"),
    "scroll": ("scroll", "execute_ref_action"),
    "home": ("global_action", "execute_global_action"),
    "wait": ("wait", "execute_wait"),
    "screenshot": ("screenshot", "execute_screenshot"),
}


def test_command_registry_matches_shared_daemon_kind_catalog() -> None:
    assert set(COMMAND_SPECS) == daemon_command_kinds_for_route("commands_run")
    assert runtime_close_entry().public_name == "close"
    assert "close" not in COMMAND_SPECS

    dispatch_method_names = {
        spec.dispatch_method_name for spec in COMMAND_SPECS.values()
    }
    assert dispatch_method_names == {
        "execute_connect",
        "execute_observe",
        "execute_list_apps",
        "execute_open",
        "execute_ref_action",
        "execute_global_action",
        "execute_wait",
        "execute_screenshot",
    }


def test_command_registry_keeps_daemon_owned_family_and_dispatch_binding() -> None:
    for daemon_kind, (
        expected_family,
        expected_dispatch_method_name,
    ) in _REPRESENTATIVE_COMMAND_REGISTRY.items():
        spec = COMMAND_SPECS[daemon_kind]
        assert spec.daemon_kind == daemon_kind
        assert spec.family == expected_family
        assert spec.dispatch_method_name == expected_dispatch_method_name


def test_command_registry_resolves_internal_global_action_by_public_kind() -> None:
    spec = resolve_command_spec(
        GlobalCommand(action="home", source_screen_id="screen-1")
    )

    assert spec.daemon_kind == "home"
    assert spec.family == "global_action"
