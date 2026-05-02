from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

import androidctl_contracts
from androidctl_contracts._version import __version__ as runtime_version

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "contracts"


def _canonical_version() -> str:
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8")
    return raw.removesuffix("\n")


def _pyproject() -> dict[str, object]:
    with (PACKAGE_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_package_version_matches_canonical_version() -> None:
    version = _canonical_version()

    assert androidctl_contracts.__version__ == version
    assert runtime_version == version
    assert "__version__" in androidctl_contracts.__all__


def test_pyproject_version_matches_canonical_version() -> None:
    project = _pyproject()["project"]

    assert project["version"] == _canonical_version()
