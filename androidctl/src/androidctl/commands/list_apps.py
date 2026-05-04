from __future__ import annotations

from pathlib import Path

import typer

from androidctl.commands import run_pipeline
from androidctl.commands.plumbing import build_and_run_command
from androidctl_contracts.daemon_api import ListAppsCommandPayload


def register(app: typer.Typer) -> None:
    @app.command("list-apps")
    def list_apps(
        ctx: typer.Context,
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command="list-apps",
                command=ListAppsCommandPayload(kind="listApps"),
                workspace_root=options.workspace_root,
            ),
        )
