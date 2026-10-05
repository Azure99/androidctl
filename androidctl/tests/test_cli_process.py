import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from textwrap import dedent

import pytest
from typer.main import get_command

from androidctl import app as cli_app
from androidctl import local_call
from androidctl.private_files import (
    LifetimeLock,
    write_json,
)
from androidctl.protocol import VERSION, Result


def test_piped_help_preserves_copyable_options_and_action_values(
    tmp_path, isolated_state
):
    environment = dict(os.environ, COLUMNS="40")
    for name in ("NO_COLOR", "FORCE_COLOR", "TYPER_USE_RICH"):
        environment.pop(name, None)
    for args in ([], ["fill"], ["snapshot"], ["shell"], ["job", "output"]):
        value = subprocess.run(
            [sys.executable, "-m", "androidctl", *args, "--help"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert value.returncode == 0, (args, value.stdout, value.stderr)
        command = get_command(cli_app.app)
        for name in args:
            command = command.commands[name]
        for parameter in command.params:
            if not getattr(parameter, "hidden", False):
                for option in parameter.opts + parameter.secondary_opts:
                    if option.startswith("--"):
                        assert option in value.stdout, (args, option, value.stdout)
    assert not (isolated_state / "daemon.json").exists()


def test_real_concurrent_cli_uses_one_daemon_and_reports_version_before_connect(
    tmp_path, isolated_state
):
    root = isolated_state
    environment = dict(os.environ)

    def cli(*args, json_output=True):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "androidctl",
                *(["--json"] if json_output else []),
                *args,
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            timeout=20,
        )

    descriptor = root / "daemon.json"
    original = None
    try:
        value = cli("daemon", "stop")
        assert value.returncode == 0, value.stdout
        result = json.loads(value.stdout)
        assert result["result"] == "done"
        assert result["reason"] == "already stopped"
        assert not descriptor.exists()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: cli("devices"), range(4)))
        assert all(value.returncode == 0 for value in results), results
        original = json.loads(descriptor.read_bytes())
        assert original["version"] == VERSION
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: cli("daemon", "status"), range(4)))
        assert {json.loads(value.stdout)["data"]["pid"] for value in results} == {
            original["pid"]
        }
        write_json(descriptor, {**original, "version": "0.0.1"})
        for action in ("status", "stop"):
            value = cli("daemon", action)
            assert value.returncode == 2
            result = json.loads(value.stdout)
            assert result["result"] == "not sent"
            assert result["error"]["code"] == "daemonVersion"
            assert result["error"]["stage"] == "local.version"
        message = result["error"]["message"]
        assert message == (
            f"Running daemon pid={original['pid']} version=0.0.1; "
            f"CLI version={VERSION}. "
            "The daemon exits after about five continuous idle minutes "
            "with no local work or connections. "
            "If a CLI matching the daemon version is still installed, "
            "use it to stop the daemon after its active work finishes."
        )
        text = cli("daemon", "stop", json_output=False)
        assert text.returncode == 2
        assert message in text.stdout.decode("utf-8")
    finally:
        if original is not None:
            write_json(descriptor, original)
        value = cli("daemon", "stop")
        assert value.returncode == 0, value.stdout
        lock = LifetimeLock(root / "daemon.lock")
        try:
            deadline = time.monotonic() + 5
            while not lock.acquire():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert not descriptor.exists()
        finally:
            lock.close()
    value = cli("daemon", "stop")
    assert value.returncode == 0, value.stdout
    result = json.loads(value.stdout)
    assert result["result"] == "done"
    assert result["reason"] == "already stopped"
    assert not descriptor.exists()


@pytest.mark.skipif(
    os.name != "nt", reason="Windows installed entry and directory handle"
)
def test_real_entry_errors_and_daemon_use_trusted_cwd(tmp_path, isolated_state):
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "websockets.py").write_text(
        "raise RuntimeError('untrusted caller import')"
    )
    environment = dict(os.environ)
    entry = Path(sys.executable).parent / "Scripts/androidctl.exe"

    def invoke(*args):
        return subprocess.run(
            [entry, *args], cwd=caller, env=environment, capture_output=True, timeout=25
        )

    for args in (("unknown-command",), ("fill", "e1"), ("fill", "e1", "-5")):
        result = invoke(*args)
        assert result.returncode == 2
        command = "arguments" if args[0] == "unknown-command" else "fill"
        assert result.stdout.startswith(f"{command}: not sent;".encode())
        assert len(result.stdout.splitlines()) == 1
        assert b"Traceback" not in result.stderr
    assert invoke("--help").returncode == 0
    try:
        result = invoke("--json", "fill", "e1", "--", "-5")
        assert result.returncode == 2, result.stderr
        assert json.loads(result.stdout)["command"] == "fill"
        assert json.loads(result.stdout)["error"]["code"] == "deviceRequired"
        moved = tmp_path / "moved"
        caller.rename(moved)
        caller = moved
        assert invoke("daemon", "status").returncode == 0
    finally:
        result = invoke("daemon", "stop")
        assert result.returncode == 0, result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shared record handles")
@pytest.mark.parametrize("kind", ["descriptor", "diagnostic"])
def test_real_stop_and_restart_while_record_is_being_read(
    tmp_path, isolated_state, monkeypatch, kind
):
    root = isolated_state

    def cli(*args):
        value = subprocess.run(
            [sys.executable, "-m", "androidctl", "--json", *args],
            cwd=tmp_path,
            capture_output=True,
            timeout=20,
        )
        assert value.returncode == 0, (value.stdout, value.stderr)
        return json.loads(value.stdout)

    opened, release = threading.Event(), threading.Event()
    original = local_call.open_read

    def held_read(path):
        stream = original(path)
        opened.set()
        if not release.wait(50):
            stream.close()
            raise AssertionError("Record reader was not released")
        return stream

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        try:
            cli("devices")
            if kind == "diagnostic":
                cli("daemon", "stop")
                cli("devices")
            getter = getattr(local_call, kind)
            old = getter(root)
            running = local_call.descriptor(root)
            monkeypatch.setattr(local_call, "open_read", held_read)
            reading = pool.submit(getter, root)
            assert opened.wait(5), "Getter did not open the record"
            assert cli("daemon", "stop")["result"] == "done"
            assert not (root / "daemon.json").exists()
            stopped = json.loads((root / "daemon-status.json").read_bytes())
            assert stopped == {"instance": running["instance"], "phase": "stopped"}
            cli("devices")
            restarted = json.loads((root / "daemon.json").read_bytes())
            assert restarted["instance"] != running["instance"]
            release.set()
            assert reading.result(timeout=5) == old
        finally:
            release.set()
            cli("daemon", "stop")
    assert local_call.daemon_free(root)
    assert not (root / "daemon.json").exists()


def test_real_stop_confirmation_survives_successor_preparing_its_record(
    tmp_path, isolated_state, monkeypatch
):
    root = isolated_state

    def cli(*args):
        value = subprocess.run(
            [sys.executable, "-m", "androidctl", "--json", *args],
            cwd=tmp_path,
            capture_output=True,
            timeout=20,
        )
        assert value.returncode == 0, (value.stdout, value.stderr)
        return Result.model_validate_json(value.stdout)

    script = tmp_path / "successor.py"
    script.write_text(
        dedent("""\
            import asyncio
            import sys
            from androidctl import daemon
            from androidctl.private_files import state_directory

            original = daemon.write_json
            def write(path, value, **kwargs):
                if value.get("phase") == "stopped":
                    print("stopping", flush=True)
                    if sys.stdin.readline() != "release\\n":
                        raise RuntimeError("Successor publisher was not released")
                original(path, value, **kwargs)
                if path.name == "daemon.json":
                    print("ready", flush=True)
            daemon.write_json = write
            asyncio.run(daemon.run(state_directory(), "successor"))
            """),
        encoding="utf-8",
    )
    reached, release = threading.Event(), threading.Event()
    diagnostic = local_call.diagnostic

    def held_confirmation(path):
        reached.set()
        assert release.wait(30), "Prior stop confirmation was not released"
        return diagnostic(path)

    successor = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        try:
            cli("devices")
            prior = cli("daemon", "stop")
            successor = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=tmp_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            assert pool.submit(successor.stdout.readline).result(5) == "ready\n"
            monkeypatch.setattr(local_call, "diagnostic", held_confirmation)
            confirming = pool.submit(
                asyncio.run, local_call.confirm_stop(prior, time.monotonic() + 15)
            )
            assert reached.wait(5), "Prior stop did not observe the successor"
            stopping = pool.submit(cli, "daemon", "stop")
            assert pool.submit(successor.stdout.readline).result(5) == "stopping\n"
            assert not (root / "daemon.json").exists()
            assert not local_call.daemon_free(root)
            release.set()
            assert confirming.result(5).result == "done"
            _, error = successor.communicate("release\n", timeout=5)
            assert successor.returncode == 0, error
            assert stopping.result(5).result == "done"
            assert diagnostic(root) == {"instance": "successor", "phase": "stopped"}
            assert local_call.daemon_free(root)
        finally:
            release.set()
            if successor is not None:
                if successor.poll() is None:
                    successor.terminate()
                successor.communicate(timeout=5)
            cli("daemon", "stop")
