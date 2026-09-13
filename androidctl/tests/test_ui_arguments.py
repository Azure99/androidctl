import json
import subprocess
import sys


def test_duration_error_in_real_cli_process():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "androidctl",
            "--json",
            "session",
            "open",
            "--idle",
            "1500ms",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 2, result.stderr
    value = json.loads(result.stdout)
    assert value["command"] == "session open"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"
