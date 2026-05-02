"""Builders for canonical command success results."""

from __future__ import annotations

from androidctld.app_targets import AppTargetMatch
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.commands.result_models import (
    CommandAppPayload,
    CommandScreenPayload,
)
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot


def app_payload(
    snapshot: RawSnapshot,
    *,
    app_match: AppTargetMatch | None = None,
) -> CommandAppPayload:
    return CommandAppPayload(
        package_name=snapshot.package_name,
        activity_name=snapshot.activity_name,
        requested_package_name=(
            None if app_match is None else app_match.requested_package_name
        ),
        resolved_package_name=(
            None if app_match is None else app_match.resolved_package_name
        ),
        match_type=None if app_match is None else app_match.match_type,
    )


def screen_payload(
    public_screen: PublicScreen, artifacts: ScreenArtifacts, *, sequence: int
) -> CommandScreenPayload:
    return CommandScreenPayload(
        screen_id=public_screen.screen_id,
        sequence=sequence,
        path_json=artifacts.screen_json,
    )
