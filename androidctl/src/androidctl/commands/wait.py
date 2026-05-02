from __future__ import annotations

from pathlib import Path

import typer

from androidctl.cli_options import CliOptions
from androidctl.command_payloads import build_wait_command
from androidctl.commands import run_pipeline
from androidctl.commands.execute import emit_usage_error
from androidctl.commands.plumbing import build_and_run_command
from androidctl.parsing.duration import parse_duration_ms
from androidctl.parsing.wait import parse_wait_predicate


def register(app: typer.Typer) -> None:
    @app.command("wait")
    def wait_command(
        ctx: typer.Context,
        until: str = typer.Option(..., "--until", help="Predicate kind to wait for."),
        ref: str | None = typer.Option(None, "--ref", help="Ref for gone waits."),
        text: str | None = typer.Option(
            None, "--text", help="Text to match for text-present waits."
        ),
        app_target: str | None = typer.Option(
            None, "--app", help="Package name to match for app waits."
        ),
        screen_id: str | None = typer.Option(
            None, "--screen-id", help="Override the source screen id for this wait."
        ),
        timeout: str = typer.Option("2000ms", "--timeout"),
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        def build_request(options: CliOptions) -> run_pipeline.CliCommandRequest:
            try:
                predicate = parse_wait_predicate(
                    until,
                    ref=ref,
                    text=text,
                    package_name=app_target,
                    source_screen_id=screen_id,
                )
                timeout_ms = parse_duration_ms(timeout)
            except ValueError as error:
                emit_usage_error(str(error))

            return run_pipeline.CliCommandRequest(
                public_command="wait",
                command=build_wait_command(
                    predicate=predicate,
                    timeout_ms=timeout_ms,
                ),
                workspace_root=options.workspace_root,
            )

        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=build_request,
        )
