"""Command result payload helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from androidctl_contracts.command_catalog import entry_for_result_command
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.vocabulary import PublicResultFamily
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.commands.models import CachedCommandError, CommandRecord, CommandStatus
from androidctld.commands.result_builders import screen_payload
from androidctld.errors import DaemonError
from androidctld.schema.base import dump_api_model
from androidctld.semantics.public_models import PublicScreen


def complete_record_with_result(
    record: CommandRecord,
    payload: dict[str, Any],
) -> None:
    expected_result_command = record.result_command
    if expected_result_command is None or not expected_result_command.strip():
        raise ValueError("record result_command must be populated")
    catalog_entry = entry_for_result_command(expected_result_command)
    if catalog_entry is None:
        raise ValueError(f"unknown result command: {expected_result_command!r}")
    result: CommandResultCore | RetainedResultEnvelope | ListAppsResult
    if catalog_entry.result_family is PublicResultFamily.SEMANTIC:
        result = CommandResultCore.model_validate(payload)
    elif catalog_entry.result_family is PublicResultFamily.RETAINED:
        result = RetainedResultEnvelope.model_validate(payload)
    elif catalog_entry.result_family is PublicResultFamily.LIST_APPS:
        result = ListAppsResult.model_validate(payload)
    else:
        raise ValueError(f"unsupported result family: {catalog_entry.result_family!r}")
    if result.command != expected_result_command:
        raise ValueError("result.command must match record result command")
    record.status = CommandStatus.SUCCEEDED
    record.completed_at = _now_isoformat()
    record.result = result
    record.error = None


def complete_record_with_error(
    record: CommandRecord,
    error: DaemonError,
) -> None:
    record.status = CommandStatus.FAILED
    record.completed_at = _now_isoformat()
    record.result = None
    record.error = CachedCommandError.from_daemon_error(error)


def screen_summary(
    public_screen: PublicScreen, artifacts: ScreenArtifacts
) -> dict[str, Any]:
    return dump_api_model(
        screen_payload(
            public_screen,
            artifacts,
            sequence=_screen_sequence_from_artifacts(artifacts),
        )
    )


def screen_changed(
    previous_screen: PublicScreen | None,
    public_screen: PublicScreen,
) -> bool:
    if previous_screen is None:
        return True
    previous_groups = [
        group.model_dump(by_alias=True, mode="json") for group in previous_screen.groups
    ]
    current_groups = [
        group.model_dump(by_alias=True, mode="json") for group in public_screen.groups
    ]
    return (
        previous_screen.app.package_name != public_screen.app.package_name
        or previous_screen.app.activity_name != public_screen.app.activity_name
        or previous_screen.surface.keyboard_visible
        != public_screen.surface.keyboard_visible
        or previous_groups != current_groups
    )


def _screen_sequence_from_artifacts(artifacts: ScreenArtifacts) -> int:
    screen_json = artifacts.screen_json
    if screen_json is None:
        return 0
    stem = screen_json.rsplit("/", maxsplit=1)[-1].removesuffix(".json")
    sequence_text = stem.removeprefix("obs-")
    return int(sequence_text) if sequence_text.isdigit() else 0


def _now_isoformat() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
