import re
from dataclasses import dataclass
from typing import Any, cast

import typer
from typer.core import TyperCommand

from .protocol import Fault, Result, rejected


@dataclass(frozen=True)
class CliOptions:
    device: str | None
    session: str
    as_json: bool


def options(ctx: typer.Context) -> CliOptions:
    return cast(CliOptions, ctx.obj)


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


def idle_duration(value: int) -> int:
    if value % 1000 or not 1000 <= value <= 86400000:
        raise typer.BadParameter(
            "Idle duration must be whole seconds between 1s and 24h"
        )
    return value // 1000
