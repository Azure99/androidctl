from __future__ import annotations

import sys
from pathlib import Path


def _prepend_repo_source_path(path: Path) -> None:
    candidate = str(path.resolve())
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


_TESTS_DIR = Path(__file__).resolve().parent
_ANDROIDCTL_ROOT = _TESTS_DIR.parent
_REPO_ROOT = _ANDROIDCTL_ROOT.parent

_prepend_repo_source_path(_ANDROIDCTL_ROOT / "src")
_prepend_repo_source_path(_REPO_ROOT / "contracts" / "src")
