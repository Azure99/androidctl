from __future__ import annotations

from pathlib import Path

import typer

from androidctl.commands import run_pipeline
from androidctl.commands.plumbing import build_and_run_command
from androidctl_contracts.daemon_api import ScreenshotCommandPayload


def register(app: typer.Typer) -> None:
    @app.command(
        "screenshot",
        help="Retained support route that captures an explicit screenshot artifact.",
    )
    def screenshot(
        ctx: typer.Context,
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command="screenshot",
                command=ScreenshotCommandPayload(kind="screenshot"),
                workspace_root=options.workspace_root,
            ),
        )
