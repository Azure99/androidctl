from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from .cli_options import (
    CliCommand,
    parse_duration,
    swipe_duration,
    timeout_duration,
    wait_duration,
)
from .protocol import Fault
from .ui_client import View, execute

DEFAULT_TIMEOUT_MS = 5000
DEFAULT_SETTLE_MS = 1500

ROLES = (
    "button",
    "textbox",
    "checkbox",
    "radio",
    "switch",
    "list",
    "item",
    "scroll",
    "image",
    "text",
)
LOCATE_ACTIONS = (
    "click",
    "fill",
    "check",
    "uncheck",
    "focus",
    "long-click",
    "expand",
    "collapse",
    "dismiss",
    "ime",
    "scroll-forward",
    "scroll-backward",
    "scroll-up",
    "scroll-down",
    "scroll-left",
    "scroll-right",
)
Role = Annotated[
    str | None, typer.Option("--role", help="Exact role: " + ", ".join(ROLES))
]
Name = Annotated[str | None, typer.Option("--name", help="Exact accessible name.")]
Text = Annotated[str | None, typer.Option("--text", help="Exact node text.")]
Identifier = Annotated[
    str | None, typer.Option("--id", help="Full resource ID or :id/ suffix.")
]
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
Settle = Annotated[
    int,
    typer.Option(
        "--settle",
        parser=parse_duration,
        callback=wait_duration,
        metavar="DURATION",
        show_default="1500ms",
        help="Ordinary observation cap (0–60s) within --timeout; check/uncheck "
        "verification can continue beyond --settle within the remaining "
        "request budget.",
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


def selector(
    role: str | None, name: str | None, text: str | None, identifier: str | None
) -> dict[str, str]:
    if role is not None and role not in ROLES:
        raise Fault("arguments", "Use --role " + "|".join(ROLES))
    return {
        key: value
        for key, value in (
            ("role", role),
            ("name", name),
            ("text", text),
            ("id", identifier),
        )
        if value is not None
    }


def target(
    ref: str | None, fields: dict[str, str], selected: dict[str, Any]
) -> dict[str, Any]:
    if ref is not None and fields:
        raise Fault("arguments", "Use a ref or selector flags, not both")
    if ref is None and not fields:
        raise Fault("arguments", "Provide a ref or at least one selector flag")
    return (
        {"ref": ref, "scope": selected}
        if ref is not None
        else {"selector": fields, "scope": selected}
    )


def run_action(
    ctx: typer.Context,
    command: str,
    args: dict[str, Any],
    timeout: int,
    settle_ms: int,
    screenshot: bool,
    image_out: Path | None,
    print_body: bool,
) -> None:
    execute(
        ctx,
        command,
        {**args, "timeoutMs": timeout, "settleMs": settle_ms, "screenshot": screenshot},
        View(image_out=image_out, print_body=print_body),
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
        "locate",
        cls=CliCommand,
        help="Wait for a unique, visible, enabled target supporting the action.",
    )
    def locate(
        ctx: typer.Context,
        role: Role = None,
        name: Name = None,
        text: Text = None,
        identifier: Identifier = None,
        package: Package = None,
        window: Window = None,
        action: Annotated[
            str,
            typer.Option(
                "--action", help="Required action:\n\n\b\n" + "\n".join(LOCATE_ACTIONS)
            ),
        ] = "click",
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        print_body: Print = False,
    ) -> None:
        if action not in LOCATE_ACTIONS:
            raise Fault("arguments", "Use --action " + "|".join(LOCATE_ACTIONS))
        args = target(
            None, selector(role, name, text, identifier), scope(package, window)
        )
        execute(
            ctx,
            "locate",
            {**args, "action": action, "timeoutMs": timeout},
            View(print_body=print_body),
        )

    def add_action(command: str, help_text: str) -> None:
        @app.command(command, cls=CliCommand, help=help_text)
        def action(
            ctx: typer.Context,
            ref: Ref = None,
            role: Role = None,
            name: Name = None,
            text: Text = None,
            identifier: Identifier = None,
            package: Package = None,
            window: Window = None,
            timeout: Timeout = DEFAULT_TIMEOUT_MS,
            settle_ms: Settle = DEFAULT_SETTLE_MS,
            screenshot: Screenshot = False,
            image_out: ImageOut = None,
            print_body: Print = False,
        ) -> None:
            args = target(
                ref, selector(role, name, text, identifier), scope(package, window)
            )
            run_action(
                ctx,
                command,
                args,
                timeout,
                settle_ms,
                screenshot,
                image_out,
                print_body,
            )

    add_action(
        "click", "Request the target's default action once; this is not a touch."
    )
    add_action("check", "Check a checkable target and verify its exposed state.")
    add_action("uncheck", "Uncheck a checkable target and verify its exposed state.")
    add_action("focus", "Request focus on the target once.")
    add_action("long-click", "Request the target's long-click action once.")
    add_action("expand", "Request the target's exposed expand action once.")
    add_action("collapse", "Request the target's exposed collapse action once.")
    add_action("dismiss", "Request the target's exposed dismiss action once.")
    add_action("ime", "Request the focused target's exposed IME action once.")

    @app.command(
        "key",
        cls=CliCommand,
        help=(
            "Send enter, tab, del or escape once; a REF must already be focused "
            "in the input-focus window. App results are not verified."
        ),
    )
    def key(
        ctx: typer.Context,
        name: Annotated[str, typer.Argument(metavar="KEY")],
        ref: Ref = None,
        package: Package = None,
        window: Window = None,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
    ) -> None:
        if name not in ("enter", "tab", "del", "escape"):
            raise Fault("arguments", "Use key enter|tab|del|escape [REF]")
        args: dict[str, Any] = {"key": name, "scope": scope(package, window)}
        if ref is None and args["scope"]:
            raise Fault(
                "arguments", "A scoped key requires a REF; omit scope for global input"
            )
        if ref is not None:
            args["ref"] = ref
        run_action(
            ctx, "key", args, timeout, settle_ms, screenshot, image_out, print_body
        )

    def add_global(command: str, help_text: str) -> None:
        @app.command(command, cls=CliCommand, help=help_text)
        def global_action(
            ctx: typer.Context,
            timeout: Timeout = DEFAULT_TIMEOUT_MS,
            settle_ms: Settle = DEFAULT_SETTLE_MS,
            screenshot: Screenshot = False,
            image_out: ImageOut = None,
            print_body: Print = False,
        ) -> None:
            run_action(
                ctx, command, {}, timeout, settle_ms, screenshot, image_out, print_body
            )

    for command in ("back", "home", "recents"):
        add_global(command, f"Request {command} once; app results are not verified.")
    add_global(
        "notifications", "Request notification expansion; panel state is not verified."
    )
    add_global("wake", "Wake the screen; credential locks still require the user.")

    @app.command("open", cls=CliCommand, help="Launch a package or an explicit URL.")
    def open_app(
        ctx: typer.Context,
        package: Annotated[str | None, typer.Argument()] = None,
        url: Annotated[
            str | None,
            typer.Option("--url", help="Open this URL instead of a package argument."),
        ] = None,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
    ) -> None:
        if (package is None) == (url is None):
            raise Fault("arguments", "Provide one package or --url URL")
        run_action(
            ctx,
            "open",
            {"package": package} if package is not None else {"url": url},
            timeout,
            settle_ms,
            screenshot,
            image_out,
            print_body,
        )

    @app.command(
        "fill",
        cls=CliCommand,
        help="Set text once; verify exposed text or report a password write receipt.",
    )
    def fill(
        ctx: typer.Context,
        values: Annotated[list[str], typer.Argument(metavar="[REF] TEXT")],
        role: Role = None,
        name: Name = None,
        text: Text = None,
        identifier: Identifier = None,
        package: Package = None,
        window: Window = None,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
        submit: Annotated[
            bool,
            typer.Option(
                "--submit",
                help="After text verification or a positive password write receipt, "
                "submit the same focused target once.",
            ),
        ] = False,
    ) -> None:
        fields = selector(role, name, text, identifier)
        if len(values) != (1 if fields else 2):
            raise Fault(
                "arguments", "Use fill REF TEXT or fill TEXT with selector flags"
            )
        args = target(None if fields else values[0], fields, scope(package, window))
        run_action(
            ctx,
            "fill",
            {**args, "value": values[-1], "submit": submit},
            timeout,
            settle_ms,
            screenshot,
            image_out,
            print_body,
        )

    @app.command(
        "scroll",
        cls=CliCommand,
        help="Scroll a REF or selector once: forward/backward/up/down/left/right.",
    )
    def scroll(
        ctx: typer.Context,
        values: Annotated[
            list[str] | None, typer.Argument(metavar="[REF] [DIRECTION]")
        ] = None,
        role: Role = None,
        name: Name = None,
        text: Text = None,
        identifier: Identifier = None,
        package: Package = None,
        window: Window = None,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
    ) -> None:
        fields = selector(role, name, text, identifier)
        parts = list(values or [])
        directions = {"forward", "backward", "up", "down", "left", "right"}
        if not fields and parts and parts[0] in directions:
            raise Fault(
                "arguments", "Provide a scroll ref or selector before the direction"
            )
        ref = parts.pop(0) if not fields and parts else None
        if len(parts) > 1:
            raise Fault("arguments", "Use scroll REF [DIRECTION] or selector flags")
        direction = parts[0] if parts else "forward"
        if direction not in directions:
            raise Fault(
                "arguments", "Direction must be forward|backward|up|down|left|right"
            )
        args = target(ref, fields, scope(package, window))
        run_action(
            ctx,
            "scroll",
            {**args, "direction": direction},
            timeout,
            settle_ms,
            screenshot,
            image_out,
            print_body,
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

    @app.command(
        "tap",
        cls=CliCommand,
        help="Tap using the session's latest qualified screenshot.",
    )
    def tap(
        ctx: typer.Context,
        x: int,
        y: int,
        image: Annotated[
            str | None,
            typer.Option(
                "--image", help="Require this ID to match the session's latest image."
            ),
        ] = None,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
    ) -> None:
        args: dict[str, Any] = {"point": [x, y]}
        if image is not None:
            args["image"] = image
        run_action(
            ctx, "tap", args, timeout, settle_ms, screenshot, image_out, print_body
        )

    @app.command(
        "swipe",
        cls=CliCommand,
        help="Swipe using the session's latest qualified screenshot.",
    )
    def swipe(
        ctx: typer.Context,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        image: Annotated[
            str | None,
            typer.Option(
                "--image", help="Require this ID to match the session's latest image."
            ),
        ] = None,
        duration: Annotated[
            int,
            typer.Option(
                "--duration",
                parser=parse_duration,
                callback=swipe_duration,
                metavar="DURATION",
                show_default="400ms",
                help="Swipe duration: 50ms–5s.",
            ),
        ] = 400,
        timeout: Timeout = DEFAULT_TIMEOUT_MS,
        settle_ms: Settle = DEFAULT_SETTLE_MS,
        screenshot: Screenshot = False,
        image_out: ImageOut = None,
        print_body: Print = False,
    ) -> None:
        args: dict[str, Any] = {
            "from": [x1, y1],
            "to": [x2, y2],
            "durationMs": duration,
        }
        if image is not None:
            args["image"] = image
        run_action(
            ctx, "swipe", args, timeout, settle_ms, screenshot, image_out, print_body
        )
