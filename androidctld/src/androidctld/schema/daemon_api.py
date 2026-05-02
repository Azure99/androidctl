"""Typed daemon ingress parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from androidctl_contracts import daemon_api as wire_api
from androidctl_contracts.command_catalog import is_daemon_command_kind
from androidctl_contracts.daemon_api import HealthResult
from androidctld.commands.command_models import InternalCommand
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
from androidctld.errors import bad_request
from androidctld.schema.validation_errors import validation_error_to_bad_request

__all__ = [
    "HealthResult",
    "ParsedCommandRun",
    "parse_command_run_request",
    "require_empty_payload",
]


@dataclass(frozen=True)
class ParsedCommandRun:
    command: InternalCommand


def require_empty_payload(payload: dict[str, Any], route: str) -> None:
    if payload:
        raise bad_request(f"{route} does not accept request payload")


def parse_command_run_request(payload: dict[str, Any]) -> ParsedCommandRun:
    _validate_command_run_payload_shape(payload)
    try:
        boundary = wire_api.CommandRunRequest.model_validate(
            payload,
            strict=True,
        )
    except ValidationError as error:
        raise validation_error_to_bad_request(error, field_name=None) from error
    return ParsedCommandRun(
        command=_adapt_wire_command_payload(boundary.command),
    )


def _validate_command_run_payload_shape(payload: dict[str, Any]) -> None:
    if "command" not in payload:
        raise bad_request("command is required", {"field": "command"})

    command = payload["command"]
    if not isinstance(command, dict):
        raise bad_request("command must be a JSON object", {"field": "command"})

    kind_raw = command.get("kind")
    if not isinstance(kind_raw, str):
        raise bad_request("command.kind must be a string", {"field": "command.kind"})

    kind = kind_raw.strip()
    if not kind:
        raise bad_request(
            "command.kind must be a non-empty string",
            {"field": "command.kind"},
        )

    if not is_daemon_command_kind(kind):
        raise bad_request(
            "unsupported command kind",
            {"field": "command.kind", "kind": kind},
        )


def _adapt_wire_command_payload(
    payload: wire_api.DaemonCommandPayload,
) -> InternalCommand:
    if isinstance(payload, wire_api.ConnectCommandPayload):
        return compile_connect_command(payload)
    if isinstance(payload, wire_api.ObserveCommandPayload):
        return compile_observe_command(payload)
    if isinstance(payload, wire_api.ListAppsCommandPayload):
        return compile_list_apps_command(payload)
    if isinstance(payload, wire_api.OpenCommandPayload):
        return compile_open_command(payload)
    if isinstance(payload, wire_api.RefActionCommandPayload):
        return compile_ref_action_command(payload)
    if isinstance(payload, wire_api.TypeCommandPayload):
        return compile_ref_action_command(payload)
    if isinstance(payload, wire_api.ScrollCommandPayload):
        return compile_ref_action_command(payload)
    if isinstance(payload, wire_api.GlobalActionCommandPayload):
        return compile_global_action_command(payload)
    if isinstance(payload, wire_api.WaitCommandPayload):
        return compile_service_wait_command(payload)
    if isinstance(payload, wire_api.ScreenshotCommandPayload):
        return compile_screenshot_command(payload)
    raise TypeError(f"unsupported wire command payload: {type(payload)!r}")
