from __future__ import annotations

import pytest

from androidctl.command_views import (
    command_view_for_public_command,
    help_order_for_public_command,
    pre_dispatch_execution_outcome_for_public_command,
)
from androidctl.commands.actions import _ACTION_COMMAND_SPECS
from androidctl_contracts.command_catalog import (
    PUBLIC_COMMAND_NAMES,
    daemon_kind_for_public_command,
    result_category_for_public_command,
    result_family_for_public_command,
    retained_envelope_kind_for_public_command,
)
from androidctl_contracts.vocabulary import PublicResultFamily

_EXPECTED_COMMAND_VIEWS = (
    ("observe", 0, "notApplicable", "notApplicable"),
    ("list-apps", 1, "notApplicable", "notApplicable"),
    ("open", 2, "notAttempted", "unknown"),
    ("tap", 3, "notAttempted", "unknown"),
    ("long-tap", 4, "notAttempted", "unknown"),
    ("focus", 5, "notAttempted", "unknown"),
    ("type", 6, "notAttempted", "unknown"),
    ("submit", 7, "notAttempted", "unknown"),
    ("scroll", 8, "notAttempted", "unknown"),
    ("back", 9, "notAttempted", "unknown"),
    ("home", 10, "notAttempted", "unknown"),
    ("recents", 11, "notAttempted", "unknown"),
    ("notifications", 12, "notAttempted", "unknown"),
    ("wait", 13, "notApplicable", "notApplicable"),
    ("connect", 14, "notApplicable", "notApplicable"),
    ("screenshot", 15, "notApplicable", "notApplicable"),
    ("close", 16, "notApplicable", "notApplicable"),
)

_EXPECTED_ACTION_COMMANDS = {
    "tap",
    "long-tap",
    "focus",
    "type",
    "submit",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
}


def error_xml_category_for_public_command(public_name: str) -> str:
    if retained_envelope_kind_for_public_command(public_name) is not None:
        raise RuntimeError(
            f"retained command {public_name!r} does not have a semantic XML category"
        )
    category = result_category_for_public_command(public_name)
    if category is None:
        return "transition"
    return category.value


def error_xml_execution_outcome_for_public_command(public_name: str) -> str:
    view = command_view_for_public_command(public_name)
    if view is None:
        return "unknown"
    if view.pre_dispatch_execution_outcome == "notApplicable":
        return "notApplicable"
    return "unknown"


def test_public_command_views_match_expected_catalog_projection() -> None:
    expected_public_names = [public_name for public_name, *_ in _EXPECTED_COMMAND_VIEWS]

    assert set(expected_public_names) == PUBLIC_COMMAND_NAMES
    assert sorted(PUBLIC_COMMAND_NAMES, key=help_order_for_public_command) == (
        expected_public_names
    )

    for (
        public_name,
        help_order,
        pre_dispatch_execution_outcome,
        error_xml_execution_outcome,
    ) in _EXPECTED_COMMAND_VIEWS:
        view = command_view_for_public_command(public_name)
        assert view is not None
        assert view.public_name == public_name
        assert view.help_order == help_order
        assert view.pre_dispatch_execution_outcome == pre_dispatch_execution_outcome
        assert help_order_for_public_command(public_name) == help_order
        assert (
            pre_dispatch_execution_outcome_for_public_command(public_name)
            == pre_dispatch_execution_outcome
        )
        assert (
            error_xml_execution_outcome_for_public_command(public_name)
            == error_xml_execution_outcome
        )


def test_setup_stays_outside_shared_command_catalog() -> None:
    assert "setup" not in PUBLIC_COMMAND_NAMES
    assert command_view_for_public_command("setup") is None
    assert help_order_for_public_command("setup") == len(_EXPECTED_COMMAND_VIEWS)
    assert pre_dispatch_execution_outcome_for_public_command("setup") is None


def test_error_xml_category_is_semantic_catalog_projection() -> None:
    semantic_public_names = {
        public_name
        for public_name in PUBLIC_COMMAND_NAMES
        if result_category_for_public_command(public_name) is not None
    }

    for public_name in semantic_public_names:
        category = result_category_for_public_command(public_name)
        assert category is not None
        assert error_xml_category_for_public_command(public_name) == category.value


def test_retained_commands_use_retained_envelope_classification() -> None:
    expected_envelopes = {
        "connect": "bootstrap",
        "screenshot": "artifact",
        "close": "lifecycle",
    }

    for public_name, envelope in expected_envelopes.items():
        retained_envelope = retained_envelope_kind_for_public_command(public_name)
        assert retained_envelope is not None
        assert retained_envelope.value == envelope
        assert command_view_for_public_command(public_name) is not None
        with pytest.raises(RuntimeError, match="does not have a semantic XML category"):
            error_xml_category_for_public_command(public_name)


def test_list_apps_uses_dedicated_result_family() -> None:
    assert result_family_for_public_command("list-apps") is PublicResultFamily.LIST_APPS
    assert result_category_for_public_command("list-apps") is None
    assert retained_envelope_kind_for_public_command("list-apps") is None
    assert command_view_for_public_command("list-apps") is not None


def test_action_command_specs_match_shared_public_action_catalog() -> None:
    action_commands = {spec.public_command for spec in _ACTION_COMMAND_SPECS}

    assert action_commands == _EXPECTED_ACTION_COMMANDS
    assert action_commands <= PUBLIC_COMMAND_NAMES
    expected_daemon_kinds: set[str] = set()
    for public_name in _EXPECTED_ACTION_COMMANDS:
        daemon_kind = daemon_kind_for_public_command(public_name)
        assert daemon_kind is not None
        expected_daemon_kinds.add(daemon_kind)
    assert {spec.kind for spec in _ACTION_COMMAND_SPECS} == expected_daemon_kinds
    assert daemon_kind_for_public_command("long-tap") == "longTap"
