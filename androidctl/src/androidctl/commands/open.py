from __future__ import annotations

from pathlib import Path

import typer
from androidctl_contracts.daemon_api import OpenCommandPayload

from androidctl.cli_options import CliOptions
from androidctl.commands import run_pipeline
from androidctl.commands.execute import emit_usage_error
from androidctl.commands.plumbing import build_and_run_command
from androidctl.parsing.open_target import parse_open_target


def register(app: typer.Typer) -> None:
    @app.command("open")
    def open_target(
        ctx: typer.Context,
        target: str = typer.Argument(
            ...,
            help="Target app:..., url:<target>, or bare http(s)://...",
        ),
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        def build_request(options: CliOptions) -> run_pipeline.CliCommandRequest:
            try:
                parsed_target = parse_open_target(target)
            except ValueError as error:
                emit_usage_error(str(error))

            return run_pipeline.CliCommandRequest(
                public_command="open",
                command=OpenCommandPayload(kind="open", target=parsed_target),
                workspace_root=options.workspace_root,
            )

        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=build_request,
        )
