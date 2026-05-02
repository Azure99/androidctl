from __future__ import annotations

from pathlib import Path

from androidctl.workspace.resolve import resolve_workspace_root


def test_workspace_root_priority_prefers_flag_over_env(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    flag_root = tmp_path / "flag"
    env_root = tmp_path / "env"

    resolved = resolve_workspace_root(
        flag_value=flag_root,
        env_value=str(env_root),
        cwd=cwd,
    )

    assert resolved == flag_root.resolve()


def test_workspace_root_priority_prefers_env_over_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    env_root = tmp_path / "env"

    resolved = resolve_workspace_root(
        flag_value=None,
        env_value=str(env_root),
        cwd=cwd,
    )

    assert resolved == env_root.resolve()


def test_workspace_root_falls_back_to_cwd(tmp_path: Path) -> None:
    resolved = resolve_workspace_root(
        flag_value=None,
        env_value=None,
        cwd=tmp_path,
    )
    assert resolved == tmp_path.resolve()


def test_workspace_root_treats_empty_env_as_unset(tmp_path: Path) -> None:
    resolved = resolve_workspace_root(
        flag_value=None,
        env_value="",
        cwd=tmp_path,
    )
    assert resolved == tmp_path.resolve()


def test_workspace_root_normalizes_relative_flag_input(tmp_path: Path) -> None:
    cwd = tmp_path / "repo" / "work"
    cwd.mkdir(parents=True)

    resolved = resolve_workspace_root(
        flag_value=Path("../workspace"),
        env_value=None,
        cwd=cwd,
    )

    assert resolved == (cwd / "../workspace").resolve()
