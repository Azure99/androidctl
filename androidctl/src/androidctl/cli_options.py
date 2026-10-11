import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import typer
from typer.core import TyperCommand

from . import workspace
from .local_call import BoundDevice
from .protocol import MAX_RUN_MS, Fault, Result, rejected


@dataclass(frozen=True)
class CliOptions:
    device: str | None
    session: str
    as_json: bool
    workspace_root: Path = field(default_factory=Path.cwd)
    device_source: str = "default"
    session_source: str = "default"


def device_context(
    context: CliOptions, device_id: str | None, source: str | None
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "workspaceRoot": str(context.workspace_root),
        "deviceSource": (
            context.device_source
            if context.device_source != "default"
            else source or "default"
        ),
    }
    if device_id is not None:
        facts["deviceId"] = device_id
    return facts


def call_context(context: CliOptions, binding: BoundDevice | None) -> dict[str, Any]:
    return {
        **device_context(
            context,
            binding.device_id if binding is not None else None,
            binding.source if binding is not None else None,
        ),
        "session": context.session,
        "sessionSource": context.session_source,
    }


def options(ctx: typer.Context) -> CliOptions:
    return cast(CliOptions, ctx.obj)


def load_workspace_options(context: CliOptions) -> CliOptions:
    root = context.workspace_root
    workspace.require_root(root)
    saved = workspace.read(root)
    if context.device_source == "default" and "device" in saved:
        context = replace(context, device=saved["device"], device_source="config")
    if context.session_source == "default" and "session" in saved:
        context = replace(context, session=saved["session"], session_source="config")
    return context


def command_name(ctx: Any) -> str:
    names = []
    while ctx is not None and ctx.parent is not None:
        if ctx.info_name:
            names.append(ctx.info_name)
        ctx = ctx.parent
    return " ".join(reversed(names)) or "arguments"


class CommandFailure(Exception):
    def __init__(self, result: Result, as_json: bool):
        super().__init__(result.reason)
        self.result = result
        self.as_json = as_json


class CliCommand(TyperCommand):
    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except Fault as error:
            result = rejected(command_name(ctx), error)
            raise CommandFailure(result, options(ctx).as_json) from error


def parse_duration(value: str | int) -> int:
    """Parse CLI durations; integer values are framework-provided defaults."""
    if isinstance(value, int):
        return value
    if value == "0":
        return 0
    match = re.fullmatch(r"([0-9]+)(ms|s|m|h)", value)
    if match is None:
        raise typer.BadParameter(
            "Use an integer with ms, s, m or h; only 0 may omit the unit"
        )
    return int(match[1]) * {"ms": 1, "s": 1000, "m": 60000, "h": 3600000}[match[2]]


def timeout_duration(value: int) -> int:
    if not 1 <= value <= 60000:
        raise typer.BadParameter("Duration must be between 1ms and 60s")
    return value


def wait_duration(value: int | None) -> int | None:
    if value is not None and not 0 <= value <= 60000:
        raise typer.BadParameter("Duration must be between 0 and 60s")
    return value


def swipe_duration(value: int) -> int:
    if not 50 <= value <= 5000:
        raise typer.BadParameter("Duration must be between 50ms and 5s")
    return value


def run_duration(value: int | None) -> int | None:
    if value is not None and not 1 <= value <= MAX_RUN_MS:
        raise typer.BadParameter("Duration must be between 1ms and 10m")
    return value


def idle_duration(value: int) -> int:
    if value % 1000 or not 1000 <= value <= 86400000:
        raise typer.BadParameter(
            "Idle duration must be whole seconds between 1s and 24h"
        )
    return value // 1000
