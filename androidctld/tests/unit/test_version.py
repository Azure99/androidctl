from __future__ import annotations

from pathlib import Path

import androidctld
from androidctld._version import __version__ as runtime_version

REPO_ROOT = Path(__file__).resolve().parents[3]


def _canonical_version() -> str:
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8")
    return raw.removesuffix("\n")


def test_package_version_matches_canonical_version() -> None:
    version = _canonical_version()

    assert androidctld.__version__ == version
    assert runtime_version == version
