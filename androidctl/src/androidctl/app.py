from pathlib import Path

import click
import typer
from typer.core import TyperGroup

from androidctl import __version__
from androidctl.cli_options import CliOptions
from androidctl.command_views import help_order_for_public_command
from androidctl.commands.actions import register as register_action_commands
from androidctl.commands.adb_wireless import register as register_adb_wireless_commands
from androidctl.commands.close import register as register_close_command
from androidctl.commands.connect import register as register_connect_command
from androidctl.commands.list_apps import register as register_list_apps_command
from androidctl.commands.observe import register as register_observe_command
from androidctl.commands.open import register as register_open_command
from androidctl.commands.screenshot import register as register_screenshot_command
from androidctl.commands.setup import register as register_setup_command
from androidctl.commands.wait import register as register_wait_command


class OrderedHelpGroup(TyperGroup):
    def list_commands(self, ctx: click.Context) -> list[str]:
        names = list(super().list_commands(ctx))
        return sorted(names, key=help_order_for_public_command)


app = typer.Typer(
    name="androidctl",
    cls=OrderedHelpGroup,
    help=(
        "Agent loop: observe/list-apps/open -> act -> wait. Retained support routes: "
        "connect, screenshot, close."
    ),
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _show_version(value: bool) -> None:
    if not value:
        return
    typer.echo(__version__)
    raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show the androidctl release version and exit.",
    ),
    workspace_root: Path | None = typer.Option(None, "--workspace-root"),
) -> None:
    """Public happy path for driving the daemon-backed device runtime."""
    del version
    ctx.obj = CliOptions(workspace_root=workspace_root)


register_connect_command(app)
register_observe_command(app)
register_list_apps_command(app)
register_open_command(app)
register_action_commands(app)
register_wait_command(app)
register_screenshot_command(app)
register_close_command(app)
register_setup_command(app)
register_adb_wireless_commands(app)
