from __future__ import annotations

from typing import Any, cast

import pytest

from androidctld.actions.postconditions import validate_postcondition
from androidctld.app_targets import match_app_target
from androidctld.commands.command_models import OpenCommand
from androidctld.commands.open_targets import OpenAppTarget, OpenUrlTarget
from androidctld.commands.result_builders import app_payload
from androidctld.device.types import ActionPerformResult, ActionStatus
from androidctld.errors import DaemonError

from .support.semantic_screen import (
    make_contract_screen,
    make_contract_snapshot,
    make_public_node,
)


def make_snapshot(package_name: str):
    return make_contract_snapshot(
        package_name=package_name,
        windowless=True,
        nodes=(),
    )


def make_public_screen(package_name: str = "com.google.android.settings.intelligence"):
    return make_contract_screen(
        package_name=package_name,
    )


def test_make_snapshot_preserves_package_only_zero_node_surface() -> None:
    snapshot = make_snapshot("com.android.settings")

    assert snapshot.package_name == "com.android.settings"
    assert snapshot.windows == ()
    assert snapshot.nodes == ()


def test_validate_open_app_postcondition_returns_alias_match() -> None:
    outcome = validate_postcondition(
        OpenCommand(target=OpenAppTarget("com.android.settings")),
        previous_snapshot=None,
        snapshot=make_snapshot("com.google.android.settings.intelligence"),
        previous_screen=None,
        public_screen=make_public_screen(),
        session=cast(Any, object()),
        focus_context=None,
        action_result=ActionPerformResult(action_id="act-1", status=ActionStatus.DONE),
    )

    assert outcome.app_match is not None
    assert outcome.app_match.requested_package_name == "com.android.settings"
    assert (
        outcome.app_match.resolved_package_name
        == "com.google.android.settings.intelligence"
    )
    assert outcome.app_match.match_type == "alias"


def test_match_app_target_accepts_aosp_settings_intelligence_variant() -> None:
    match = match_app_target(
        "com.android.settings",
        "com.android.settings.intelligence",
    )

    assert match is not None
    assert match.requested_package_name == "com.android.settings"
    assert match.resolved_package_name == "com.android.settings.intelligence"
    assert match.match_type == "alias"


def test_validate_open_url_postcondition_passes_without_previous_screen_basis() -> None:
    outcome = validate_postcondition(
        OpenCommand(target=OpenUrlTarget("https://example.com")),
        previous_snapshot=None,
        snapshot=make_snapshot("com.android.chrome"),
        previous_screen=None,
        public_screen=make_public_screen(package_name="com.android.chrome"),
        session=cast(Any, object()),
        focus_context=None,
        action_result=ActionPerformResult(action_id="act-1", status=ActionStatus.DONE),
    )

    assert outcome.app_match is None


def test_validate_open_url_postcondition_passes_on_public_screen_change() -> None:
    outcome = validate_postcondition(
        OpenCommand(target=OpenUrlTarget("https://example.com")),
        previous_snapshot=make_snapshot("com.android.chrome"),
        snapshot=make_snapshot("com.android.chrome"),
        previous_screen=make_public_screen(package_name="com.android.chrome"),
        public_screen=make_contract_screen(
            package_name="com.android.chrome",
            targets=(make_public_node(ref="n1", label="Example Domain"),),
        ),
        session=cast(Any, object()),
        focus_context=None,
        action_result=ActionPerformResult(action_id="act-1", status=ActionStatus.DONE),
    )

    assert outcome.app_match is None


def test_validate_open_url_postcondition_rejects_unchanged_screen() -> None:
    public_screen = make_public_screen(package_name="com.android.chrome")

    with pytest.raises(DaemonError) as error:
        validate_postcondition(
            OpenCommand(target=OpenUrlTarget("https://example.com")),
            previous_snapshot=make_snapshot("com.android.chrome"),
            snapshot=make_snapshot("com.android.chrome"),
            previous_screen=public_screen,
            public_screen=public_screen,
            session=cast(Any, object()),
            focus_context=None,
            action_result=ActionPerformResult(
                action_id="act-1",
                status=ActionStatus.DONE,
            ),
        )

    assert error.value.code == "OPEN_FAILED"


def test_app_payload_carries_alias_metadata_for_wait_success() -> None:
    match = match_app_target(
        "com.android.settings",
        "com.google.android.settings.intelligence",
    )
    payload = app_payload(
        make_snapshot("com.google.android.settings.intelligence"),
        app_match=match,
    )
    dumped = payload.model_dump(by_alias=True, mode="json")

    assert dumped["requestedPackageName"] == "com.android.settings"
    assert dumped["resolvedPackageName"] == "com.google.android.settings.intelligence"
    assert dumped["matchType"] == "alias"


def test_app_payload_omits_alias_metadata_without_match() -> None:
    dumped = app_payload(make_snapshot("com.android.settings")).model_dump(
        by_alias=True,
        mode="json",
    )

    assert "requestedPackageName" not in dumped
    assert "resolvedPackageName" not in dumped
    assert "matchType" not in dumped
