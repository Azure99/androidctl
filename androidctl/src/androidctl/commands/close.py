from __future__ import annotations

from pathlib import Path

import typer

from androidctl.cli_options import command_cli_options
from androidctl.commands import execute, run_pipeline


def register(app: typer.Typer) -> None:
    @app.command("close", help="Retained support route for runtime lifecycle shutdown.")
    def close(
        ctx: typer.Context,
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        options = command_cli_options(ctx, workspace_root=workspace_root)
        try:
            outcome = run_pipeline.run_close_command(
                run_pipeline.build_context(),
                options.workspace_root,
            )
        except typer.Exit:
            raise
        except Exception as error:
            execute.render_exception(error, command="close")
        execute.render_command_outcome(
            outcome=outcome,
            public_command="close",
        )
