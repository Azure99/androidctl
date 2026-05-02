import pytest
from typer.main import get_command
from typer.testing import CliRunner

from androidctl import __version__
from androidctl.app import app

_EXPECTED_HELP_ORDER = [
    "observe",
    "list-apps",
    "open",
    "tap",
    "long-tap",
    "focus",
    "type",
    "submit",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
    "wait",
    "connect",
    "screenshot",
    "close",
    "setup",
    "adb-pair",
    "adb-connect",
]


def test_help_shows_public_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "observe/list-apps/open -> act -> wait" in result.stdout
    assert "connect -> observe/open -> act -> wait" not in result.stdout
    assert "Retained support routes" in result.stdout
    assert "--version" in result.stdout
    for command in _EXPECTED_HELP_ORDER:
        assert command in result.stdout


def test_version_writes_plain_version_to_stdout_and_exits_zero() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == f"{__version__}\n"
    assert result.stderr == ""


def test_help_describes_retained_support_and_diagnostic_routes() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Retained support route for starting a device runtime." in result.stdout
    assert (
        "Retained support route that captures an explicit screenshot" in result.stdout
    )
    assert "artifact." in result.stdout
    assert "Retained support route for runtime lifecycle shutdown." in result.stdout
    assert "Onboarding helper for preparing an authorized ADB device." in result.stdout
    assert "Auxiliary helper for Android wireless debugging pairing." in result.stdout
    assert "Auxiliary helper for connecting an already paired wireless" in result.stdout
    assert "ADB device." in result.stdout


def test_help_lists_public_commands_in_frozen_order() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0

    assert get_command(app).list_commands(None) == _EXPECTED_HELP_ORDER


def test_raw_command_is_not_registered() -> None:
    result = CliRunner().invoke(app, ["raw", "--help"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No such command 'raw'" in result.stderr


def test_list_apps_help_keeps_public_usage_without_raw_method() -> None:
    result = CliRunner().invoke(app, ["list-apps", "--help"])

    assert result.exit_code == 0
    assert "Usage: androidctl list-apps [OPTIONS]" in result.stdout
    assert "--workspace-root" in result.stdout
    assert "apps.list" not in result.stdout


@pytest.mark.parametrize(
    ("command_args", "usage", "description"),
    [
        (["tap"], "Usage: androidctl tap [OPTIONS] REF", "Target ref, for example n3."),
        (
            ["long-tap"],
            "Usage: androidctl long-tap [OPTIONS] REF",
            "Target ref, for example n3.",
        ),
        (
            ["scroll"],
            "Usage: androidctl scroll [OPTIONS] REF DIRECTION",
            "Scrollable ref, for example n8.",
        ),
    ],
)
def test_action_command_help_keeps_public_usage_and_description(
    command_args: list[str],
    usage: str,
    description: str,
) -> None:
    runner = CliRunner()

    help_result = runner.invoke(app, [*command_args, "--help"])

    assert help_result.exit_code == 0
    assert usage in help_result.stdout
    assert description in help_result.stdout
    assert help_result.stdout.index("--screen-id") < help_result.stdout.index(
        "--workspace-root"
    )
    if command_args == ["scroll"]:
        assert "Scroll direction: up/down/left/right/backward." in help_result.stdout


def test_home_help_keeps_public_options() -> None:
    runner = CliRunner()
    home_help = runner.invoke(app, ["home", "--help"])
    assert home_help.exit_code == 0
    assert "Usage: androidctl home [OPTIONS]" in home_help.stdout
    assert "--screen-id" in home_help.stdout
    assert "--workspace-root" in home_help.stdout


def test_connect_requires_token() -> None:
    result = CliRunner().invoke(app, ["connect"])

    assert result.exit_code == 2
    assert "Missing option '--token'" in result.output


def test_open_requires_target_argument() -> None:
    result = CliRunner().invoke(app, ["open"])

    assert result.exit_code == 2
    assert "Missing argument 'TARGET'" in result.output
