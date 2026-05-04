from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from androidctl.command_payloads import (
    SCROLL_DIRECTIONS,
    build_global_action_command,
    build_ref_action_command,
    build_scroll_command,
    build_type_command,
)
from androidctl.commands import run_pipeline
from androidctl.commands.execute import emit_usage_error
from androidctl.commands.plumbing import build_and_run_command
from androidctl.parsing.refs import parse_ref
from androidctl.parsing.screen_id import parse_screen_id_override
from androidctl_contracts.command_catalog import daemon_kind_for_public_command

RefActionKind = Literal["tap", "longTap", "focus", "submit"]
GlobalActionKind = Literal["back", "home", "recents", "notifications"]
ActionCommandKind = Literal[
    "tap",
    "longTap",
    "focus",
    "submit",
    "type",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
]
ActionCommandSignature = Literal["ref", "ref_text", "ref_direction", "screen"]
ScrollDirection = Literal["up", "down", "left", "right", "backward"]
TargetRefArgument = Annotated[
    str,
    typer.Argument(help="Target ref, for example n3."),
]
ScrollableRefArgument = Annotated[
    str,
    typer.Argument(help="Scrollable ref, for example n8."),
]
TypeTextArgument = Annotated[
    str,
    typer.Argument(help="Text to replace the current value with."),
]
ScrollDirectionArgument = Annotated[
    str,
    typer.Argument(help="Scroll direction: up/down/left/right/backward."),
]
ScreenIdOption = Annotated[
    str | None,
    typer.Option("--screen-id", help="Override the source screen id for this command."),
]
WorkspaceRootOption = Annotated[Path | None, typer.Option("--workspace-root")]


@dataclass(frozen=True)
class _ActionCommandSpec:
    public_command: str
    signature: ActionCommandSignature

    @property
    def kind(self) -> ActionCommandKind:
        daemon_kind = daemon_kind_for_public_command(self.public_command)
        if daemon_kind is None:
            raise RuntimeError(
                f"missing daemon kind for action command {self.public_command!r}"
            )
        return cast(ActionCommandKind, daemon_kind)


_ACTION_COMMAND_SPECS = (
    _ActionCommandSpec(public_command="tap", signature="ref"),
    _ActionCommandSpec(public_command="long-tap", signature="ref"),
    _ActionCommandSpec(public_command="focus", signature="ref"),
    _ActionCommandSpec(public_command="submit", signature="ref"),
    _ActionCommandSpec(public_command="type", signature="ref_text"),
    _ActionCommandSpec(
        public_command="scroll",
        signature="ref_direction",
    ),
    _ActionCommandSpec(public_command="back", signature="screen"),
    _ActionCommandSpec(public_command="home", signature="screen"),
    _ActionCommandSpec(public_command="recents", signature="screen"),
    _ActionCommandSpec(
        public_command="notifications",
        signature="screen",
    ),
)


def register(app: typer.Typer) -> None:
    for spec in _ACTION_COMMAND_SPECS:
        if spec.signature == "ref":
            _register_ref_command(app, spec)
            continue
        if spec.signature == "ref_text":
            _register_ref_text_command(app, spec)
            continue
        if spec.signature == "ref_direction":
            _register_ref_direction_command(app, spec)
            continue
        _register_screen_command(app, spec)


def _register_ref_command(app: typer.Typer, spec: _ActionCommandSpec) -> None:
    @app.command(spec.public_command)
    def action_command(
        ctx: typer.Context,
        ref: TargetRefArgument,
        screen_id: ScreenIdOption = None,
        workspace_root: WorkspaceRootOption = None,
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command=spec.public_command,
                command=build_ref_action_command(
                    kind=cast(RefActionKind, spec.kind),
                    ref=_parse_ref_or_fail(ref),
                    source_screen_id=_parse_screen_id_or_fail(screen_id),
                ),
                workspace_root=options.workspace_root,
            ),
            public_command=spec.public_command,
        )


def _register_ref_text_command(app: typer.Typer, spec: _ActionCommandSpec) -> None:
    @app.command(spec.public_command)
    def action_command(
        ctx: typer.Context,
        ref: TargetRefArgument,
        text: TypeTextArgument,
        screen_id: ScreenIdOption = None,
        workspace_root: WorkspaceRootOption = None,
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command=spec.public_command,
                command=build_type_command(
                    ref=_parse_ref_or_fail(ref),
                    text=text,
                    source_screen_id=_parse_screen_id_or_fail(screen_id),
                ),
                workspace_root=options.workspace_root,
            ),
            public_command=spec.public_command,
        )


def _register_ref_direction_command(
    app: typer.Typer,
    spec: _ActionCommandSpec,
) -> None:
    @app.command(spec.public_command)
    def action_command(
        ctx: typer.Context,
        ref: ScrollableRefArgument,
        direction: ScrollDirectionArgument,
        screen_id: ScreenIdOption = None,
        workspace_root: WorkspaceRootOption = None,
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command=spec.public_command,
                command=build_scroll_command(
                    ref=_parse_ref_or_fail(ref),
                    direction=_parse_direction_or_fail(
                        direction,
                        allowed=SCROLL_DIRECTIONS,
                    ),
                    source_screen_id=_parse_screen_id_or_fail(screen_id),
                ),
                workspace_root=options.workspace_root,
            ),
            public_command=spec.public_command,
        )


def _register_screen_command(app: typer.Typer, spec: _ActionCommandSpec) -> None:
    @app.command(spec.public_command)
    def action_command(
        ctx: typer.Context,
        screen_id: ScreenIdOption = None,
        workspace_root: WorkspaceRootOption = None,
    ) -> None:
        build_and_run_command(
            ctx=ctx,
            workspace_root=workspace_root,
            build_request=lambda options: run_pipeline.CliCommandRequest(
                public_command=spec.public_command,
                command=build_global_action_command(
                    kind=cast(GlobalActionKind, spec.kind),
                    source_screen_id=_parse_screen_id_or_fail(screen_id),
                ),
                workspace_root=options.workspace_root,
            ),
            public_command=spec.public_command,
        )


def _parse_ref_or_fail(raw_ref: str) -> str:
    try:
        return parse_ref(raw_ref)
    except ValueError as error:
        emit_usage_error(str(error))


def _parse_direction_or_fail(
    raw_direction: str,
    *,
    allowed: Collection[str],
) -> ScrollDirection:
    normalized = raw_direction.strip().lower()
    if normalized not in allowed:
        emit_usage_error(f"direction must be one of: {', '.join(sorted(allowed))}")
    return cast(ScrollDirection, normalized)


def _parse_screen_id_or_fail(raw_screen_id: str | None) -> str | None:
    try:
        return parse_screen_id_override(raw_screen_id)
    except ValueError as error:
        emit_usage_error(str(error))
