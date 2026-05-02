from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass(frozen=True)
class CliOptions:
    workspace_root: Path | None


def read_cli_options(ctx: typer.Context) -> CliOptions:
    payload = ctx.obj
    if isinstance(payload, CliOptions):
        return payload
    return CliOptions(workspace_root=None)


def command_cli_options(
    ctx: typer.Context,
    *,
    workspace_root: Path | None = None,
) -> CliOptions:
    root_options = read_cli_options(ctx)
    return CliOptions(workspace_root=workspace_root or root_options.workspace_root)
