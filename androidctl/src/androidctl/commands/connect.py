from __future__ import annotations

from pathlib import Path

import typer

from androidctl.cli_options import CliOptions
from androidctl.commands import run_pipeline
from androidctl.commands.execute import emit_usage_error
from androidctl.commands.plumbing import build_and_run_command
from androidctl_contracts.daemon_api import ConnectCommandPayload, ConnectionPayload


def register(app: typer.Typer) -> None:
    @app.command(
        "connect",
        help="Retained support route for starting a device runtime.",
    )
    def connect(
        ctx: typer.Context,
        adb: bool = typer.Option(False, "--adb", help="Use ADB transport."),
        token: str = typer.Option(..., "--token", help="Device agent token."),
        serial: str | None = typer.Option(None, "--serial", help="ADB serial."),
        host: str | None = typer.Option(None, "--host", help="Device host."),
        port: int | None = typer.Option(None, "--port", help="Device port."),
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        def build_request(options: CliOptions) -> run_pipeline.CliCommandRequest:
            if adb and host:
                emit_usage_error("--adb cannot be used with --host")
            if adb and port is not None:
                emit_usage_error("--adb cannot be used with --port")
            if not adb and host is None:
                emit_usage_error("choose --adb or provide --host and --port")
            if not adb and port is None:
                emit_usage_error("choose --adb or provide --host and --port")
            if not adb and serial:
                emit_usage_error("--serial can only be used with --adb")
            normalized_token = token.strip()
            if not normalized_token:
                emit_usage_error("--token cannot be empty")

            if adb:
                connection = ConnectionPayload(
                    mode="adb",
                    token=normalized_token,
                    serial=serial,
                )
            else:
                if host is None or port is None:
                    emit_usage_error("choose --adb or provide --host and --port")
                connection = ConnectionPayload(
                    mode="lan",
                    token=normalized_token,
                    host=host,
                    port=port,
                )

            return run_pipeline.CliCommandRequest(
                public_command="connect",
                command=ConnectCommandPayload(kind="connect", connection=connection),
                workspace_root=options.workspace_root,
            )

        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=build_request,
        )
