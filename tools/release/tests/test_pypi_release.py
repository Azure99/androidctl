from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.release import android_release


def init_git(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        "dist/\nandroid/app/build/\n*.apk\n/build/\n*.egg-info/\n"
    )
    for args in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.parametrize("prepare", [android_release.main], ids=["android"])
def test_prepare_rejects_dirty_git_before_replacing_candidate(
    release_repo: Path, prepare: Callable[[list[str]], int]
) -> None:
    paths = android_release.build_release_paths(release_repo)
    paths.staged_apk_path.write_bytes(b"previous-candidate")
    paths.checksums_path.write_bytes(b"previous-checksum")
    before = {
        path: path.read_bytes()
        for path in (paths.staged_apk_path, paths.checksums_path)
    }
    init_git(release_repo)
    (release_repo / "README.md").write_text("changed source")

    with pytest.raises(SystemExit, match="pre-stage: source tree must be clean"):
        prepare(["--repo-root", str(release_repo), "prepare"])

    assert {path: path.read_bytes() for path in before} == before
