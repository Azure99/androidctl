from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import threading
from pathlib import Path

import httpx
import pytest
from tests.e2e.support import (
    read_active_record,
    run_androidctl,
    stop_daemon,
    workspace_active_record_path,
)

from androidctl.daemon.client import DaemonApiError, DaemonClient


def _write_barrier_daemon_shim(
    tmp_path: Path,
    editable_install_env,
    barrier_dir: Path,
) -> Path:
    script_path = tmp_path / "barrier_daemon_shim.py"
    barrier_dir_literal = repr(barrier_dir.as_posix())
    python_executable_literal = repr(editable_install_env.python_executable.as_posix())
    script_path.write_text(
        textwrap.dedent(f"""
            from __future__ import annotations

            import os
            import sys
            import time
            from pathlib import Path

            TIMEOUT_SECONDS = 8.0
            POLL_SECONDS = 0.02


            def main() -> int:
                barrier_dir = Path({barrier_dir_literal})
                python_executable = {python_executable_literal}
                barrier_dir.mkdir(parents=True, exist_ok=True)
                ready_path = barrier_dir / f"ready-{{os.getpid()}}"
                ready_path.write_text(str(os.getpid()), encoding="utf-8")
                deadline = time.monotonic() + TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    if len(list(barrier_dir.glob("ready-*"))) >= 2:
                        os.execv(
                            python_executable,
                            [
                                python_executable,
                                "-m",
                                "androidctld",
                                *sys.argv[1:],
                            ],
                        )
                    time.sleep(POLL_SECONDS)
                return 75


            if __name__ == "__main__":
                raise SystemExit(main())
            """).lstrip(),
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper_path = tmp_path / "androidctld.cmd"
        wrapper_path.write_text(
            f'@echo off\r\n"{editable_install_env.python_executable}" '
            f'"{script_path}" %*\r\n',
            encoding="utf-8",
        )
        return wrapper_path
    wrapper_path = tmp_path / "androidctld-shim"
    wrapper_path.write_text(
        "#!/bin/sh\n"
        f'exec "{editable_install_env.python_executable}" "{script_path}" "$@"\n',
        encoding="utf-8",
    )
    wrapper_path.chmod(0o755)
    return wrapper_path


def test_workspace_defaults_to_cwd_without_workspace_env(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"
    cwd = tmp_path / "standalone-workspace"

    try:
        result = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
            cwd=cwd,
        )
        record = read_active_record(home_dir, workspace_root=cwd)

        assert result.returncode != 2
        assert record.workspace_root == cwd.resolve().as_posix()
    finally:
        stop_daemon(home_dir, workspace_root=cwd)


def test_workspace_defaults_to_git_subdirectory_cwd_without_workspace_env(
    tmp_path: Path,
    editable_install_env,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for git-root fallback regression test")
    home_dir = tmp_path / "home"
    repo = tmp_path / "repo"
    cwd = repo / "nested" / "workspace"
    repo.mkdir(parents=True)
    cwd.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )

    try:
        result = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
            cwd=cwd,
        )
        record = read_active_record(home_dir, workspace_root=cwd)

        assert result.returncode != 2
        assert record.workspace_root == cwd.resolve().as_posix()
        assert not workspace_active_record_path(
            home_dir,
            workspace_root=repo,
        ).exists()
    finally:
        stop_daemon(home_dir, workspace_root=cwd)
        stop_daemon(home_dir, workspace_root=repo)


def test_workspace_env_overrides_cwd_independently_from_owner(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"
    cwd = tmp_path / "command-cwd"
    workspace_env_root = tmp_path / "env-workspace"

    try:
        result = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
            cwd=cwd,
            workspace_env_root=workspace_env_root,
        )
        record = read_active_record(home_dir, workspace_root=workspace_env_root)

        assert result.returncode != 2
        assert record.workspace_root == workspace_env_root.resolve().as_posix()
        assert record.owner_id == "shell:a"
        assert not workspace_active_record_path(home_dir, workspace_root=cwd).exists()
    finally:
        stop_daemon(home_dir, workspace_root=workspace_env_root)
        stop_daemon(home_dir, workspace_root=cwd)


def test_same_owner_reuses_workspace_daemon(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"

    try:
        first = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
        )
        first_record = read_active_record(home_dir)
        second = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
        )
        second_record = read_active_record(home_dir)

        assert first.returncode != 2
        assert second.returncode != 2
        assert first_record.identity == second_record.identity
    finally:
        stop_daemon(home_dir)


def test_concurrent_same_owner_starts_resolve_winning_daemon(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"
    barrier_dir = tmp_path / "launcher-barrier"
    launcher_shim = _write_barrier_daemon_shim(
        tmp_path,
        editable_install_env,
        barrier_dir,
    )
    start_barrier = threading.Barrier(2)
    results = []
    errors: list[BaseException] = []

    def run_observe() -> None:
        try:
            start_barrier.wait(timeout=5.0)
            results.append(
                run_androidctl(
                    ["observe"],
                    home_dir=home_dir,
                    editable_install_env=editable_install_env,
                    owner_id="shell:a",
                    extra_env={"ANDROIDCTLD_BIN": launcher_shim.as_posix()},
                )
            )
        except BaseException as error:  # pragma: no cover - surfaced after join
            errors.append(error)

    threads = [threading.Thread(target=run_observe) for _ in range(2)]

    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=40.0)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert len(list(barrier_dir.glob("ready-*"))) == 2

        record = read_active_record(home_dir)
        assert record.owner_id == "shell:a"
        for result in results:
            assert result.returncode != 2
            combined_output = f"{result.stdout}\n{result.stderr}"
            assert "owner.lock" not in combined_output
            assert "WORKSPACE_BUSY" not in combined_output
    finally:
        stop_daemon(home_dir)


def test_different_owner_gets_workspace_busy_even_with_copied_token(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"

    try:
        first = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
        )
        assert first.returncode != 2
        record = read_active_record(home_dir)
        client = DaemonClient(
            httpx.Client(
                base_url=f"http://{record.host}:{record.port}", trust_env=False
            ),
            owner_id="shell:b",
            token=record.token,
        )

        with pytest.raises(DaemonApiError) as error:
            client.health(record)
        assert error.value.code == "WORKSPACE_BUSY"
    finally:
        stop_daemon(home_dir)


def test_close_releases_ownership_for_later_owner(
    tmp_path: Path,
    editable_install_env,
) -> None:
    home_dir = tmp_path / "home"

    try:
        first = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
        )
        assert first.returncode != 2

        close_result = run_androidctl(
            ["close"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:a",
        )
        later_owner = run_androidctl(
            ["observe"],
            home_dir=home_dir,
            editable_install_env=editable_install_env,
            owner_id="shell:b",
        )
        later_record = read_active_record(home_dir)

        assert close_result.returncode == 0
        assert later_owner.returncode != 2
        assert later_record.owner_id == "shell:b"
    finally:
        stop_daemon(home_dir)
