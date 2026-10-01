from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from .cli_options import CliCommand, parse_duration, timeout_duration
from .protocol import Fault
from .ui_client import View, execute

DEFAULT_TIMEOUT_MS = 5000
Package = Annotated[str | None, typer.Option("--in", help="Task package.")]
Window = Annotated[int | None, typer.Option("--window", help="Task window ID.")]
Timeout = Annotated[
    int,
    typer.Option(
        "--timeout",
        parser=parse_duration,
        callback=timeout_duration,
        metavar="DURATION",
        show_default="5s",
        help="Total request budget: 1ms–60s.",
    ),
]
Print = Annotated[
    bool,
    typer.Option(
        "--print", help="Echo the saved observation body; incompatible with --json."
    ),
]
Out = Annotated[
    Path | None,
    typer.Option(
        "--out",
        readable=False,
        help="Write the observation body to a new file in an existing directory.",
    ),
]


def scope(package: str | None, window: int | None) -> dict[str, Any]:
    if package is not None and window is not None:
        raise Fault("arguments", "Use --in or --window, not both")
    return (
        {"package": package}
        if package is not None
        else {"window": window}
        if window is not None
        else {}
    )


def register(app: typer.Typer) -> None:
    @app.command(
        "snapshot",
        cls=CliCommand,
        help=(
            "Save the collected task screen to a file; "
            "Resolve relative paths from the CLI working directory."
        ),
    )
    def snapshot(
        ctx: typer.Context,
        package: Package = None,
        window: Window = None,
        all_nodes: Annotated[
            bool,
            typer.Option("--all", help="Include hidden nodes and extra XML fields."),
        ] = False,
        boxes: Annotated[
            bool, typer.Option("--boxes", help="Include node bounds in XML.")
        ] = False,
        raw: Annotated[
            bool,
            typer.Option(
                "--raw", help="Save the device's collected, processed tree as JSON."
            ),
        ] = False,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        out: Out = None,
        print_body: Print = False,
    ) -> None:
        selected = scope(package, window)
        execute(
            ctx,
            "snapshot",
            {
                "scope": selected,
                "timeoutMs": timeout,
            },
            View(
                all_nodes=all_nodes,
                boxes=boxes,
                raw=raw,
                out=out,
                print_body=print_body,
            ),
        )
