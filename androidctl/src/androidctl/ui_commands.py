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
Screenshot = Annotated[
    bool,
    typer.Option("--screenshot", help="Attach an image within the remaining budget."),
]
ImageOut = Annotated[
    Path | None,
    typer.Option(
        "--image-out",
        readable=False,
        help="Write PNG to a new file in an existing directory; implies --screenshot.",
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
Ref = Annotated[str | None, typer.Argument(help="Ref in the current snapshot.")]


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
            "--cached or REF reads the last observation "
            "without scope or image options. "
            "Resolve relative paths from the CLI working directory."
        ),
    )
    def snapshot(
        ctx: typer.Context,
        ref: Ref = None,
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
        cached: Annotated[
            bool,
            typer.Option("--cached", help="Read the last observation; no new scope."),
        ] = False,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        out: Out = None,
        print_body: Print = False,
    ) -> None:
        selected = scope(package, window)
        cached = cached or ref is not None
        if cached and selected:
            raise Fault(
                "arguments", "Take a scoped snapshot first, then render its cache"
            )
        if cached and (screenshot or image_out is not None):
            raise Fault(
                "arguments", "Cached observations cannot request a new screenshot"
            )
        execute(
            ctx,
            "snapshot",
            {
                "cached": cached,
                "scope": selected,
                "timeoutMs": timeout,
                "screenshot": screenshot,
            },
            View(
                ref=ref,
                all_nodes=all_nodes,
                boxes=boxes,
                raw=raw,
                out=out,
                image_out=image_out,
                print_body=print_body,
            ),
        )

    @app.command(
        "find",
        cls=CliCommand,
        help="Search current snapshot text without collecting a new screen.",
    )
    def find(
        ctx: typer.Context,
        query: str,
        all_nodes: Annotated[
            bool,
            typer.Option(
                "--all",
                help="Search hidden nodes and extra fields in the XML projection.",
            ),
        ] = False,
        boxes: Annotated[
            bool, typer.Option("--boxes", help="Include node bounds in XML.")
        ] = False,
        raw: Annotated[
            bool,
            typer.Option(
                "--raw",
                help="Save the device's collected, processed cached tree as JSON.",
            ),
        ] = False,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        out: Out = None,
        print_body: Print = False,
    ) -> None:
        execute(
            ctx,
            "find",
            {"cached": True, "timeoutMs": timeout},
            View(
                query=query,
                all_nodes=all_nodes,
                boxes=boxes,
                raw=raw,
                out=out,
                print_body=print_body,
            ),
        )

    @app.command(
        "screenshot", cls=CliCommand, help="Capture PNG and its task snapshot."
    )
    def screenshot(
        ctx: typer.Context,
        timeout: Annotated[
            int,
            typer.Option(
                "--timeout",
                parser=parse_duration,
                callback=timeout_duration,
                metavar="DURATION",
                show_default="15s",
                help="Total request budget: 1ms–60s.",
            ),
        ] = 15000,
        image_out: Annotated[
            Path | None,
            typer.Option(
                "--image-out",
                readable=False,
                help="Write PNG to a new file in an existing directory.",
            ),
        ] = None,
        print_body: Print = False,
    ) -> None:
        execute(
            ctx,
            "screenshot",
            {"timeoutMs": timeout},
            View(image_out=image_out, print_body=print_body),
        )
