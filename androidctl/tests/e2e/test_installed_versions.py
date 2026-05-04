from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.e2e.support import editable_metadata_dirs, remove_editable_metadata_dirs

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
    version_probe = (
        "import json\n"
        "from importlib.metadata import PackageNotFoundError, version as dist_version\n"
        "import androidctl, androidctld, androidctl_contracts\n"
        "\n"
        "def metadata_version(name):\n"
        "    try:\n"
        "        return dist_version(name)\n"
        "    except PackageNotFoundError:\n"
        "        return None\n"
        "\n"
        "print(json.dumps({\n"
        "    'distributions': {\n"
        "        'androidctl': metadata_version('androidctl'),\n"
        "        'androidctld': metadata_version('androidctld'),\n"
        "        'androidctl-contracts': metadata_version('androidctl-contracts'),\n"
        "    },\n"
        "    'packages': {\n"
        "        'androidctl': androidctl.__version__,\n"
        "        'androidctld': androidctld.__version__,\n"
        "        'androidctl_contracts': androidctl_contracts.__version__,\n"
        "    },\n"
        "}))\n"
    )

    result = subprocess.run(
        [
            editable_install_env.python_executable.as_posix(),
            "-c",
            version_probe,
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
        "distributions": {
            "androidctl": version,
            "androidctld": None,
            "androidctl-contracts": None,
        },
        "packages": {
            "androidctl": version,
            "androidctld": version,
            "androidctl_contracts": version,
        },
    }


def test_editable_metadata_cleanup_covers_root_and_old_child_metadata(
    tmp_path: Path,
) -> None:
    fake_metadata_dirs = (
        tmp_path / "androidctl.egg-info",
        tmp_path / "androidctl.dist-info",
        tmp_path / "contracts" / "src" / "androidctl_contracts.egg-info",
        tmp_path / "contracts" / "src" / "androidctl_contracts.dist-info",
        tmp_path / "androidctld" / "src" / "androidctld.egg-info",
        tmp_path / "androidctld" / "src" / "androidctld.dist-info",
        tmp_path / "androidctl" / "src" / "androidctl.egg-info",
        tmp_path / "androidctl" / "src" / "androidctl.dist-info",
    )
    for fake_metadata_dir in fake_metadata_dirs:
        fake_metadata_dir.mkdir(parents=True)
        (fake_metadata_dir / "PKG-INFO").write_text("Name: fake\n", encoding="utf-8")

    assert editable_metadata_dirs(tmp_path) == sorted(fake_metadata_dirs)

    remove_editable_metadata_dirs(tmp_path)

    assert editable_metadata_dirs(tmp_path) == []
