import os
import subprocess
import sys

from typer.main import get_command

from androidctl import app as cli_app


def test_piped_help_preserves_copyable_options_and_action_values(
    tmp_path, isolated_state
):
    environment = dict(os.environ, COLUMNS="40")
    for name in ("NO_COLOR", "FORCE_COLOR", "TYPER_USE_RICH"):
        environment.pop(name, None)
    for args in ([],):
        value = subprocess.run(
            [sys.executable, "-m", "androidctl", *args, "--help"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert value.returncode == 0, (args, value.stdout, value.stderr)
        command = get_command(cli_app.app)
        for name in args:
            command = command.commands[name]
        for parameter in command.params:
            if not getattr(parameter, "hidden", False):
                for option in parameter.opts + parameter.secondary_opts:
                    if option.startswith("--"):
                        assert option in value.stdout, (args, option, value.stdout)
    assert not (isolated_state / "daemon.json").exists()
