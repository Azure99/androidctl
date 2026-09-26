import json
import os
import subprocess
import sys

import pytest


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows alternate data streams")
@pytest.mark.parametrize(
    "args",
    [
        ["job", "output", "j1", "--out"],
    ],
)
def test_output_path_rejects_ads_before_request(tmp_path, args):
    original = tmp_path / "evidence.png"
    original.write_bytes(b"preserve")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "androidctl",
            "--json",
            *args,
            str(original) + ":capture",
        ],
        env={**os.environ, "LOCALAPPDATA": str(tmp_path / "host")},
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 2, result.stderr
    value = json.loads(result.stdout)
    assert value["command"] == ("job output" if args[0] == "job" else args[0])
    assert value["error"]["code"] == "filePath"
    assert original.read_bytes() == b"preserve"
    assert not (tmp_path / "host").exists()
