from __future__ import annotations

from importlib import import_module

import pytest

import androidctl_contracts as contracts
from androidctl_contracts import command_catalog
from androidctl_contracts.command_catalog import (
    DAEMON_COMMAND_KINDS,
    LIST_APPS_RESULT_COMMAND_NAMES,
    PUBLIC_COMMAND_NAMES,
    RESULT_COMMAND_NAMES,
    RETAINED_RESULT_COMMAND_NAMES,
    SEMANTIC_RESULT_COMMAND_NAMES,
    CommandCatalogEntry,
    daemon_command_kinds_for_route,
    daemon_kind_for_public_command,
    entries_for_route,
    entry_for_daemon_kind,
    entry_for_list_apps_result_command,
    entry_for_public_command,
    entry_for_result_command,
    entry_for_retained_result_command,
    entry_for_semantic_result_command,
    is_list_apps_result_command,
    is_retained_result_command,
    is_semantic_result_command,
    public_command_for_daemon_kind,
    result_category_for_command,
    result_category_for_public_command,
    result_family_for_command,
    result_family_for_daemon_kind,
    result_family_for_public_command,
    retained_envelope_kind_for_command,
    retained_envelope_kind_for_public_command,
    runtime_close_entry,
)
from androidctl_contracts.vocabulary import (
    PublicResultCategory,
    PublicResultFamily,
    RetainedEnvelopeKind,
)

_EXPECTED_COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    CommandCatalogEntry(
        public_name="connect",
        route="commands_run",
        daemon_kind="connect",
        result_command="connect",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.BOOTSTRAP,
    ),
    CommandCatalogEntry(
        public_name="observe",
        route="commands_run",
        daemon_kind="observe",
        result_command="observe",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OBSERVE,
    ),
    CommandCatalogEntry(
        public_name="open",
        route="commands_run",
        daemon_kind="open",
        result_command="open",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OPEN,
    ),
    CommandCatalogEntry(
        public_name="tap",
        route="commands_run",
        daemon_kind="tap",
        result_command="tap",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="long-tap",
        route="commands_run",
        daemon_kind="longTap",
        result_command="long-tap",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="focus",
        route="commands_run",
        daemon_kind="focus",
        result_command="focus",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="type",
        route="commands_run",
        daemon_kind="type",
        result_command="type",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="submit",
        route="commands_run",
        daemon_kind="submit",
        result_command="submit",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="scroll",
        route="commands_run",
        daemon_kind="scroll",
        result_command="scroll",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="back",
        route="commands_run",
        daemon_kind="back",
        result_command="back",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="home",
        route="commands_run",
        daemon_kind="home",
        result_command="home",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="recents",
        route="commands_run",
        daemon_kind="recents",
        result_command="recents",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="notifications",
        route="commands_run",
        daemon_kind="notifications",
        result_command="notifications",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.TRANSITION,
    ),
    CommandCatalogEntry(
        public_name="wait",
        route="commands_run",
        daemon_kind="wait",
        result_command="wait",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.WAIT,
    ),
    CommandCatalogEntry(
        public_name="list-apps",
        route="commands_run",
        daemon_kind="listApps",
        result_command="list-apps",
        result_family=PublicResultFamily.LIST_APPS,
        result_category=None,
    ),
    CommandCatalogEntry(
        public_name="screenshot",
        route="commands_run",
        daemon_kind="screenshot",
        result_command="screenshot",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.ARTIFACT,
    ),
    CommandCatalogEntry(
        public_name="close",
        route="runtime_close",
        daemon_kind=None,
        result_command="close",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.LIFECYCLE,
    ),
)

_EXPECTED_STABLE_COMMAND_CATALOG_EXPORTS = frozenset(
    {
        "DAEMON_COMMAND_KINDS",
        "LIST_APPS_RESULT_COMMAND_NAMES",
        "PUBLIC_COMMAND_NAMES",
        "RETAINED_RESULT_COMMAND_NAMES",
        "RESULT_COMMAND_NAMES",
        "SEMANTIC_RESULT_COMMAND_NAMES",
        "CommandCatalogEntry",
        "CommandRoute",
        "daemon_kind_for_public_command",
        "entry_for_daemon_kind",
        "entry_for_list_apps_result_command",
        "entry_for_public_command",
        "entry_for_retained_result_command",
        "entry_for_result_command",
        "entry_for_semantic_result_command",
        "is_daemon_command_kind",
        "is_list_apps_result_command",
        "is_public_command",
        "is_retained_result_command",
        "is_semantic_result_command",
        "public_command_for_daemon_kind",
        "retained_envelope_kind_for_command",
        "retained_envelope_kind_for_public_command",
        "result_category_for_command",
        "result_category_for_public_command",
        "result_family_for_command",
        "result_family_for_daemon_kind",
        "result_family_for_public_command",
    }
)


def test_public_command_names_match_current_surface() -> None:
    assert {
        "connect",
        "observe",
        "open",
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
        "wait",
        "list-apps",
        "screenshot",
        "close",
    } == PUBLIC_COMMAND_NAMES


def test_command_catalog_freezes_routing_and_result_dimensions() -> None:
    assert entries_for_route("commands_run") == tuple(
        entry for entry in _EXPECTED_COMMAND_CATALOG if entry.daemon_kind is not None
    )
    assert entries_for_route("runtime_close") == (_EXPECTED_COMMAND_CATALOG[-1],)
    assert {
        entry.daemon_kind
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.daemon_kind is not None
    } == DAEMON_COMMAND_KINDS
    assert {
        entry.result_command for entry in _EXPECTED_COMMAND_CATALOG
    } == RESULT_COMMAND_NAMES
    assert {
        entry.result_command
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.SEMANTIC
    } == SEMANTIC_RESULT_COMMAND_NAMES
    assert {
        entry.result_command
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.RETAINED
    } == RETAINED_RESULT_COMMAND_NAMES
    assert {
        entry.result_command
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.LIST_APPS
    } == LIST_APPS_RESULT_COMMAND_NAMES


def test_public_command_classification_splits_success_result_families() -> None:
    semantic_entries = tuple(
        entry
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.SEMANTIC
    )
    retained_entries = tuple(
        entry
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.RETAINED
    )
    list_apps_entries = tuple(
        entry
        for entry in _EXPECTED_COMMAND_CATALOG
        if entry.result_family is PublicResultFamily.LIST_APPS
    )

    assert {
        entry.result_command for entry in semantic_entries
    } == SEMANTIC_RESULT_COMMAND_NAMES
    assert RESULT_COMMAND_NAMES == PUBLIC_COMMAND_NAMES
    assert {
        entry.result_command for entry in retained_entries
    } == RETAINED_RESULT_COMMAND_NAMES
    assert {
        entry.result_command for entry in list_apps_entries
    } == LIST_APPS_RESULT_COMMAND_NAMES
    assert PUBLIC_COMMAND_NAMES == RESULT_COMMAND_NAMES
    assert (
        SEMANTIC_RESULT_COMMAND_NAMES
        | RETAINED_RESULT_COMMAND_NAMES
        | LIST_APPS_RESULT_COMMAND_NAMES
    ) == RESULT_COMMAND_NAMES
    assert SEMANTIC_RESULT_COMMAND_NAMES.isdisjoint(RETAINED_RESULT_COMMAND_NAMES)
    assert SEMANTIC_RESULT_COMMAND_NAMES.isdisjoint(LIST_APPS_RESULT_COMMAND_NAMES)
    assert RETAINED_RESULT_COMMAND_NAMES.isdisjoint(LIST_APPS_RESULT_COMMAND_NAMES)

    for command in SEMANTIC_RESULT_COMMAND_NAMES:
        assert entry_for_result_command(command) == entry_for_public_command(command)
        assert entry_for_semantic_result_command(command) == entry_for_public_command(
            command
        )
        assert entry_for_public_command(command).result_category is not None
        assert result_category_for_command(
            command
        ) == result_category_for_public_command(command)
        assert result_family_for_command(command) is PublicResultFamily.SEMANTIC
        assert is_semantic_result_command(command) is True
        assert is_retained_result_command(command) is False
        assert is_list_apps_result_command(command) is False

    for command in RETAINED_RESULT_COMMAND_NAMES:
        assert entry_for_retained_result_command(command) == entry_for_public_command(
            command
        )
        assert entry_for_result_command(command) == entry_for_public_command(command)
        assert entry_for_semantic_result_command(command) is None
        assert result_category_for_command(command) is None
        assert result_category_for_public_command(command) is None
        assert retained_envelope_kind_for_command(
            command
        ) == retained_envelope_kind_for_public_command(command)
        assert result_family_for_command(command) is PublicResultFamily.RETAINED
        assert is_semantic_result_command(command) is False
        assert is_retained_result_command(command) is True
        assert is_list_apps_result_command(command) is False

    for command in LIST_APPS_RESULT_COMMAND_NAMES:
        assert entry_for_list_apps_result_command(command) == entry_for_public_command(
            command
        )
        assert entry_for_result_command(command) == entry_for_public_command(command)
        assert entry_for_semantic_result_command(command) is None
        assert entry_for_retained_result_command(command) is None
        assert result_category_for_command(command) is None
        assert result_category_for_public_command(command) is None
        assert retained_envelope_kind_for_command(command) is None
        assert retained_envelope_kind_for_public_command(command) is None
        assert result_family_for_command(command) is PublicResultFamily.LIST_APPS
        assert is_semantic_result_command(command) is False
        assert is_retained_result_command(command) is False
        assert is_list_apps_result_command(command) is True


def test_route_state_remains_separate_from_commands_run_catalog_entries() -> None:
    runtime_close_entries = entries_for_route("runtime_close")
    commands_run_entries = entries_for_route("commands_run")

    assert runtime_close_entries == (entry_for_public_command("close"),)
    assert runtime_close_entry() == entry_for_public_command("close")
    assert {entry.public_name for entry in runtime_close_entries} == {"close"}
    assert {entry.public_name for entry in commands_run_entries} == (
        PUBLIC_COMMAND_NAMES - {"close"}
    )
    assert all(entry.daemon_kind is None for entry in runtime_close_entries)
    assert all(entry.daemon_kind is not None for entry in commands_run_entries)
    assert daemon_command_kinds_for_route("commands_run") == DAEMON_COMMAND_KINDS
    assert daemon_command_kinds_for_route("runtime_close") == frozenset()


def test_close_is_retained_lifecycle_not_commands_run() -> None:
    close_entry = entry_for_public_command("close")

    assert close_entry == CommandCatalogEntry(
        public_name="close",
        route="runtime_close",
        daemon_kind=None,
        result_command="close",
        result_family=PublicResultFamily.RETAINED,
        result_category=None,
        retained_envelope_kind=RetainedEnvelopeKind.LIFECYCLE,
    )
    assert "close" in RESULT_COMMAND_NAMES
    assert "close" in RETAINED_RESULT_COMMAND_NAMES
    assert "close" not in DAEMON_COMMAND_KINDS
    assert entry_for_daemon_kind("close") is None
    assert daemon_kind_for_public_command("close") is None
    assert public_command_for_daemon_kind("close") is None
    assert entry_for_result_command("close") == close_entry
    assert entry_for_retained_result_command("close") == close_entry
    assert retained_envelope_kind_for_command("close") == RetainedEnvelopeKind.LIFECYCLE


def test_catalog_lookup_helpers_project_public_and_daemon_spelling() -> None:
    assert daemon_kind_for_public_command("long-tap") == "longTap"
    assert public_command_for_daemon_kind("longTap") == "long-tap"
    assert daemon_kind_for_public_command("list-apps") == "listApps"
    assert public_command_for_daemon_kind("listApps") == "list-apps"
    assert result_family_for_public_command("list-apps") is PublicResultFamily.LIST_APPS
    assert result_family_for_daemon_kind("listApps") is PublicResultFamily.LIST_APPS
    assert entry_for_public_command("raw") is None
    assert retained_envelope_kind_for_public_command("raw") is None
    assert result_category_for_command("close") is None
    assert retained_envelope_kind_for_command("close") == RetainedEnvelopeKind.LIFECYCLE


def test_command_catalog_uniqueness_guards_reject_duplicate_values() -> None:
    duplicate_public = CommandCatalogEntry(
        public_name="observe",
        route="commands_run",
        daemon_kind="observe-copy",
        result_command="observe-copy",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OBSERVE,
    )
    duplicate_daemon = CommandCatalogEntry(
        public_name="observe-copy",
        route="commands_run",
        daemon_kind="observe",
        result_command="observe-copy",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OBSERVE,
    )
    duplicate_result = CommandCatalogEntry(
        public_name="observe-copy",
        route="commands_run",
        daemon_kind="observe-copy",
        result_command="observe",
        result_family=PublicResultFamily.SEMANTIC,
        result_category=PublicResultCategory.OBSERVE,
    )

    with pytest.raises(RuntimeError, match="public_name"):
        command_catalog._build_unique_entry_index(
            _EXPECTED_COMMAND_CATALOG + (duplicate_public,),
            field_name="public_name",
        )
    with pytest.raises(RuntimeError, match="daemon_kind"):
        command_catalog._build_unique_entry_index(
            _EXPECTED_COMMAND_CATALOG + (duplicate_daemon,),
            field_name="daemon_kind",
        )
    with pytest.raises(RuntimeError, match="result_command"):
        command_catalog._build_unique_entry_index(
            _EXPECTED_COMMAND_CATALOG + (duplicate_result,),
            field_name="result_command",
        )


@pytest.mark.parametrize(
    "entry_kwargs",
    [
        {
            "result_family": PublicResultFamily.SEMANTIC,
            "result_category": None,
            "retained_envelope_kind": None,
        },
        {
            "result_family": PublicResultFamily.SEMANTIC,
            "result_category": PublicResultCategory.OBSERVE,
            "retained_envelope_kind": RetainedEnvelopeKind.ARTIFACT,
        },
        {
            "result_family": PublicResultFamily.RETAINED,
            "result_category": None,
            "retained_envelope_kind": None,
        },
        {
            "result_family": PublicResultFamily.RETAINED,
            "result_category": PublicResultCategory.OBSERVE,
            "retained_envelope_kind": RetainedEnvelopeKind.ARTIFACT,
        },
        {
            "result_family": PublicResultFamily.LIST_APPS,
            "result_category": PublicResultCategory.OBSERVE,
            "retained_envelope_kind": None,
        },
        {
            "result_family": PublicResultFamily.LIST_APPS,
            "result_category": None,
            "retained_envelope_kind": RetainedEnvelopeKind.ARTIFACT,
        },
    ],
)
def test_command_catalog_entry_rejects_invalid_family_dimensions(
    entry_kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="result family"):
        CommandCatalogEntry(
            public_name="invalid",
            route="commands_run",
            daemon_kind="invalid",
            result_command="invalid",
            **entry_kwargs,
        )


def test_root_package_exports_remain_small() -> None:
    assert set(contracts.__all__) == {
        "__version__",
        "DAEMON_COMMAND_KINDS",
        "LIST_APPS_RESULT_COMMAND_NAMES",
        "PUBLIC_COMMAND_NAMES",
        "RETAINED_RESULT_COMMAND_NAMES",
        "RESULT_COMMAND_NAMES",
        "SEMANTIC_RESULT_COMMAND_NAMES",
        "is_list_apps_result_command",
        "is_public_command",
        "is_retained_result_command",
        "is_semantic_result_command",
        "retained_envelope_kind_for_command",
        "retained_envelope_kind_for_public_command",
        "result_family_for_command",
        "result_family_for_daemon_kind",
        "result_family_for_public_command",
        "DaemonCommandPayload",
        "CommandRunRequest",
        "CommandResultCore",
        "ListAppsResult",
        "PublicResultFamily",
        "RetainedEnvelopeKind",
        "RetainedResultEnvelope",
        "DaemonError",
        "DaemonErrorCode",
    }
    assert contracts.RESULT_COMMAND_NAMES == RESULT_COMMAND_NAMES
    assert contracts.SEMANTIC_RESULT_COMMAND_NAMES == SEMANTIC_RESULT_COMMAND_NAMES
    assert contracts.RETAINED_RESULT_COMMAND_NAMES == RETAINED_RESULT_COMMAND_NAMES
    assert contracts.LIST_APPS_RESULT_COMMAND_NAMES == LIST_APPS_RESULT_COMMAND_NAMES
    assert contracts.PUBLIC_COMMAND_NAMES == PUBLIC_COMMAND_NAMES
    assert contracts.DAEMON_COMMAND_KINDS == DAEMON_COMMAND_KINDS
    assert contracts.is_public_command is command_catalog.is_public_command
    assert (
        contracts.is_semantic_result_command
        is command_catalog.is_semantic_result_command
    )
    assert (
        contracts.is_retained_result_command
        is command_catalog.is_retained_result_command
    )
    assert (
        contracts.is_list_apps_result_command
        is command_catalog.is_list_apps_result_command
    )
    assert (
        contracts.retained_envelope_kind_for_command
        is command_catalog.retained_envelope_kind_for_command
    )
    assert (
        contracts.retained_envelope_kind_for_public_command
        is command_catalog.retained_envelope_kind_for_public_command
    )
    assert (
        contracts.result_family_for_command is command_catalog.result_family_for_command
    )
    assert (
        contracts.result_family_for_public_command
        is command_catalog.result_family_for_public_command
    )
    assert (
        contracts.result_family_for_daemon_kind
        is command_catalog.result_family_for_daemon_kind
    )


def test_command_catalog_all_exports_match_supported_helper_surface() -> None:
    all_exports = tuple(command_catalog.__all__)
    stable_exports = _EXPECTED_STABLE_COMMAND_CATALOG_EXPORTS

    assert len(all_exports) == len(set(all_exports))
    assert set(all_exports) == stable_exports

    root_catalog_exports = set(contracts.__all__) & set(command_catalog.__all__)
    assert root_catalog_exports <= stable_exports


def test_command_results_module_keeps_runtime_payload_outside_result_surface() -> None:
    command_results = import_module("androidctl_contracts.command_results")

    assert not hasattr(command_results, "RuntimePayload")


def test_public_result_category_vocabulary_keeps_only_semantic_categories() -> None:
    assert {item.value for item in PublicResultCategory} == {
        "observe",
        "open",
        "transition",
        "wait",
    }


def test_public_result_family_vocabulary_freezes_success_families() -> None:
    assert {item.value for item in PublicResultFamily} == {
        "semantic",
        "retained",
        "listApps",
    }


def test_retained_envelope_kind_vocabulary_is_separate() -> None:
    assert {item.value for item in RetainedEnvelopeKind} == {
        "bootstrap",
        "artifact",
        "lifecycle",
    }
