from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from pydantic import ValidationError

from androidctl.cli_options import CliOptions, command_cli_options
from androidctl.commands import run_pipeline
from androidctl.commands.execute import emit_usage_error, run_and_render

RequestBuilder = Callable[[CliOptions], run_pipeline.CliCommandRequest]


def build_and_run_command(
    *,
    ctx: typer.Context,
    workspace_root: Path | None,
    build_request: RequestBuilder,
    public_command: str | None = None,
) -> None:
    options = command_cli_options(
        ctx,
        workspace_root=workspace_root,
    )
    try:
        request = build_request(options)
    except ValidationError as error:
        emit_usage_error(
            _validation_error_message(error),
            command=public_command or _context_command_name(ctx),
        )
    run_and_render(
        request,
        public_command=public_command or _context_command_name(ctx),
    )


def _context_command_name(ctx: typer.Context) -> str | None:
    command_name = ctx.info_name
    if isinstance(command_name, str) and command_name:
        return command_name
    return None


def _validation_error_message(error: ValidationError) -> str:
    details = error.errors(include_url=False)
    if not details:
        return str(error)
    first = details[0]
    location = ".".join(
        str(part) for part in first.get("loc", ()) if part != "__root__"
    ).strip(".")
    message = str(first.get("msg", "invalid input"))
    if not location:
        return message
    return f"{location}: {message}"
