from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLED_VERSION_TIMEOUT_SECONDS = 30


def _canonical_version() -> str:
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8")
    return raw.removesuffix("\n")


def test_editable_install_keeps_runtime_and_metadata_versions_in_lockstep(
    editable_install_env,
) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            editable_install_env.python_executable.as_posix(),
            "-c",
            (
                "import json; "
                "from importlib.metadata import version as dist_version; "
                "import androidctl, androidctld, androidctl_contracts; "
                "print(json.dumps({"
                "'androidctl': [androidctl.__version__, dist_version('androidctl')], "
                "'androidctld': [androidctld.__version__, "
                "dist_version('androidctld')], "
                "'androidctl-contracts': [androidctl_contracts.__version__, "
                "dist_version('androidctl-contracts')]"
                "}))"
            ),
        ],
        cwd=REPO_ROOT / "androidctl",
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=INSTALLED_VERSION_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    version = _canonical_version()

    assert payload == {
        "androidctl": [version, version],
        "androidctld": [version, version],
        "androidctl-contracts": [version, version],
    }
