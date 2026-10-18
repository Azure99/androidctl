from __future__ import annotations

from pathlib import Path

import pytest
from tools.release.android_release import (
    build_release_paths,
    stage_release_apk,
    write_sha256sums,
)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    output = repo / "android/app/build/outputs/apk/release"
    output.mkdir(parents=True)
    (repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        (repo / name).write_text(name, encoding="utf-8")
    for name in ("androidctl/src/androidctl/resources", "skills/androidctl"):
        directory = repo / name
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (repo / "androidctl/src/androidctl/__init__.py").write_text("", encoding="utf-8")
    (output / "app-release.apk").write_bytes(b"release-apk")
    paths = build_release_paths(repo)
    stage_release_apk(paths)
    write_sha256sums(paths)
    return repo
