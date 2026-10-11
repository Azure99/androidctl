import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import local_call, workspace
from androidctl.private_files import PublishedWriteError, private_directory, write_json
from androidctl.protocol import Fault, Problem, Result, completed
from androidctl.state import HostState
from connection_test_support import connections
from pairing_test_support import Gateway, new_device_identity, save_active


@pytest.mark.parametrize(
    "raw", ["{", "[]", '{"device":""}', '{"session":2}', '{"extra":"x"}']
)
def test_bad_config_keeps_leaf_and_absolute_path_before_connection(
    tmp_path, monkeypatch, capsys, raw
):
    monkeypatch.chdir(tmp_path)
    path = workspace.config_path(tmp_path)
    path.parent.mkdir()
    path.write_text(raw)

    async def forbidden(**kwargs):
        pytest.fail("Invalid workspace configuration reached IPC")

    monkeypatch.setattr(local_call, "connect_local", forbidden)
    monkeypatch.setattr(
        sys, "argv", ["androidctl", "--json", "--device", "phone", "-s", "task", "apps"]
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "apps"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "workspace"
    assert str(path) in value["reason"]
    assert "context" not in value["data"]


def test_use_rejects_empty_session_without_changing_config(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    path = workspace.config_path(tmp_path)
    path.parent.mkdir()
    before = b'{"device":"configured-device","session":"retained"}'
    path.write_bytes(before)

    async def forbidden(**kwargs):
        pytest.fail("Empty workspace session reached IPC")

    monkeypatch.setattr(local_call, "connect_local", forbidden)
    monkeypatch.setattr(sys, "argv", ["androidctl", "--json", "--session", "", "use"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "use"
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "arguments"
    assert "nonempty" in value["reason"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["address", "trust-path", "trust-encoding"])
def test_management_preflight_has_no_unconsumed_context(
    tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.chdir(tmp_path)
    path = workspace.config_path(tmp_path)
    path.parent.mkdir()
    path.write_text('{"device":"configured-device","session":"retained"}')
    if failure == "address":
        args = ["address", "target", "bad"]
        code = "endpoint"
    else:
        certificate = tmp_path / "certificate.pem"
        if failure == "trust-path":
            certificate.mkdir()
        else:
            certificate.write_bytes(b"\xff")
        args = ["trust", "target", str(certificate)]
        code = "arguments" if failure == "trust-path" else "certificate"

    async def forbidden(**kwargs):
        pytest.fail("Management input failure reached IPC")

    monkeypatch.setattr(local_call, "connect_local", forbidden)
    monkeypatch.setattr(sys, "argv", ["androidctl", "--json", "device", *args])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    value = json.loads(capsys.readouterr().out)
    assert value["command"] == "device " + args[0]
    assert value["result"] == "not sent"
    assert value["error"]["code"] == code
    assert "context" not in value["data"]


@pytest.mark.parametrize("default", ["missing-global", "pending-sole"])
async def test_devices_reports_unusable_default_without_selected_identity(
    tmp_path, monkeypatch, default
):
    async def dispatch(method, args):
        pytest.fail("Listing unusable local defaults sent a device RPC")

    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        state = coordinator.state
        if default == "missing-global":
            identifier = str(uuid.uuid4())
            write_json(state.selection, {"deviceId": identifier})
            source, code = "global", "deviceNotFound"
        else:
            identifier = material["deviceId"]
            record, _ = state.prepare_pairing(
                state.record(identifier).identity, material["endpoint"], "pending"
            )
            record.active = None
            state.save_record(record)
            source, code = "sole", "pairingUnconfirmed"
        monkeypatch.setattr(local_call, "connect_local", connect)
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "--workspace-root", str(tmp_path), "devices"],
        )
    assert response.exit_code == 0, response.output
    value = json.loads(response.stdout)
    assert value["data"]["selectionDevice"] == identifier
    assert "selectionSource" not in value["data"]
    assert value["data"]["context"] == {
        "workspaceRoot": str(tmp_path),
        "deviceSource": source,
    }
    assert value["data"]["selectionError"]["code"] == code
    assert not any(item["selected"] for item in value["data"]["devices"])
    assert not workspace.config_path(tmp_path).exists()


@pytest.mark.parametrize("published", [False, True])
async def test_use_preserves_configuration_publication_fact(
    tmp_path, monkeypatch, capsys, published
):
    root = tmp_path / "workspace"
    root.mkdir()

    def save(path, value):
        if published:
            write_json(path, value)
            raise PublishedWriteError("final synchronization failed")
        raise OSError("configuration write failed")

    async def dispatch(method, args):
        pytest.fail("Saving local defaults sent a device RPC")

    async with connections(tmp_path, dispatch) as (_, material, connect):
        monkeypatch.setattr(local_call, "connect_local", connect)
        monkeypatch.setattr(workspace, "write_json", save)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "androidctl",
                "--json",
                "--workspace-root",
                str(root),
                "--device",
                "phone",
                "-s",
                "task",
                "use",
            ],
        )
        with pytest.raises(SystemExit) as stopped:
            await asyncio.to_thread(cli.main)
    value = json.loads(capsys.readouterr().out)
    assert stopped.value.code == (3 if published else 2), value
    assert value["command"] == "use"
    assert value["result"] == ("done" if published else "not sent")
    path = workspace.config_path(root)
    assert str(path) in value["reason"]
    assert "context" not in value["data"]
    if published:
        assert json.loads(path.read_bytes()) == {
            "device": material["deviceId"],
            "session": "task",
        }
        assert value["data"]["saved"] is True
        assert value["data"]["device"] == material["deviceId"]
        assert value["data"]["session"] == "task"
    else:
        assert not path.exists()


@pytest.mark.parametrize("source", ["args", "global", "sole"])
@pytest.mark.parametrize("outcome", ["success", "unavailable", "read-failed"])
async def test_job_export_final_result_keeps_bound_context(
    tmp_path, monkeypatch, outcome, source
):
    body = b"retained output"
    metadata = {
        "stream": "stdout",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }

    async def dispatch(method, args):
        if method == "job.get":
            return completed(
                method,
                {
                    "job": "j1",
                    "output": {
                        "state": "unavailable"
                        if outcome == "unavailable"
                        else "available",
                        "streams": [metadata],
                    },
                },
            ).wire()
        assert method == "output.read"
        if outcome == "read-failed":
            raise Fault("outputUnavailable", "Retained output read failed")
        return completed(
            method,
            {
                "job": "j1",
                "offset": args["offset"],
                **metadata,
                "bytes": base64.b64encode(body).decode(),
            },
        ).wire()

    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        if source == "global":
            write_json(coordinator.state.selection, {"deviceId": material["deviceId"]})
        monkeypatch.setattr(local_call, "connect_local", connect)
        destination = tmp_path / "output.bin"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--workspace-root",
                str(tmp_path),
                *(["--device", "phone"] if source == "args" else []),
                "-s",
                "task",
                "job",
                "output",
                "j1",
                "--out",
                str(destination),
            ],
        )
    assert response.exit_code == (0 if outcome == "success" else 1), response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["data"]["context"] == {
        "workspaceRoot": str(tmp_path),
        "deviceId": material["deviceId"],
        "deviceSource": source,
        "session": "task",
        "sessionSource": "args",
    }
    if outcome == "success":
        assert destination.read_bytes() == body
    else:
        assert not destination.exists()


@pytest.mark.parametrize(
    ("query_result", "exit_code"), [("not sent", 2), ("UNKNOWN", 3)]
)
async def test_job_export_query_failure_keeps_confirmed_device_context(
    tmp_path, monkeypatch, query_result, exit_code
):
    problem = Problem(
        code="queryUnavailable",
        message="Original job query unavailable",
        stage="device.query",
        diagnostic_truncated=True,
    )
    received = []

    async def dispatch(method, args):
        received.append((method, args["job"]))
        if query_result == "not sent":
            raise Fault.from_problem(problem)
        return Result(
            command=method, result="UNKNOWN", reason=problem.message, error=problem
        ).wire()

    async with connections(tmp_path, dispatch) as (coordinator, material, connect):
        write_json(coordinator.state.selection, {"deviceId": material["deviceId"]})
        monkeypatch.setattr(local_call, "connect_local", connect)
        destination = tmp_path / "output.bin"
        response = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "--json",
                "--workspace-root",
                str(tmp_path),
                "-s",
                "task",
                "job",
                "output",
                "j1",
                "--out",
                str(destination),
            ],
        )
    assert response.exit_code == exit_code, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == query_result
    assert value["error"] == problem.wire()
    assert value["data"]["job"] == "j1"
    assert value["data"]["context"] == {
        "workspaceRoot": str(tmp_path),
        "deviceId": material["deviceId"],
        "deviceSource": "global",
        "session": "task",
        "sessionSource": "args",
    }
    assert received == [("job.get", "j1")]
    assert not destination.exists()


async def call_cli_process(*args, cwd, env=None, exit_code=0):
    environment = dict(os.environ)
    for name in (
        "ANDROIDCTL_DEVICE",
        "ANDROIDCTL_SESSION",
        "ANDROIDCTL_WORKSPACE_ROOT",
    ):
        environment.pop(name, None)
    response = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "androidctl", "--json", *args],
        cwd=cwd,
        env={**environment, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert response.returncode == exit_code, (args, response.stdout, response.stderr)
    return json.loads(response.stdout)


def assert_call_context(
    value, root, identifier, device_source, session, session_source
):
    assert value["data"]["context"] == {
        "workspaceRoot": str(root),
        "deviceId": identifier,
        "deviceSource": device_source,
        "session": session,
        "sessionSource": session_source,
    }


async def test_use_reads_updates_and_clears_locally(tmp_path, isolated_state):
    private_directory(isolated_state)
    path = workspace.config_path(tmp_path)
    shown = await call_cli_process("use", cwd=tmp_path)
    assert shown["data"] == {"path": str(path), "saved": False}
    await call_cli_process("-s", "unpaired-task", "use", cwd=tmp_path)
    before = path.read_bytes()
    assert json.loads(before) == {"session": "unpaired-task"}
    shown = await call_cli_process(
        "use",
        cwd=tmp_path,
        env={"ANDROIDCTL_DEVICE": "unpaired", "ANDROIDCTL_SESSION": "ignored"},
    )
    assert shown["data"] == {
        "session": "unpaired-task",
        "path": str(path),
        "saved": False,
    }
    assert path.read_bytes() == before
    await call_cli_process("-s", "updated", "use", cwd=tmp_path)
    assert json.loads(path.read_bytes()) == {"session": "updated"}
    path.write_text("{")
    for command in ("use", "apps"):
        malformed = await call_cli_process(command, cwd=tmp_path, exit_code=2)
        assert malformed["command"] == command
        assert malformed["error"]["code"] == "workspace"
        assert str(path) in malformed["reason"]
    await call_cli_process("use", "--clear", cwd=tmp_path)
    await call_cli_process("use", "--clear", cwd=tmp_path)
    assert not path.exists()
    assert not (isolated_state / "daemon.json").exists()
    assert local_call.daemon_free(isolated_state)


async def test_devices_empty_and_sole_defaults_across_cli_processes(
    tmp_path, isolated_state
):
    private_directory(isolated_state)
    state = HostState(isolated_state)
    try:
        unpaired = await call_cli_process("devices", cwd=tmp_path)
        assert unpaired["result"] == "done"
        assert "selectionSource" not in unpaired["data"]
        assert unpaired["data"]["selectionError"]["code"] == "deviceRequired"
        assert unpaired["data"]["context"] == {
            "workspaceRoot": str(tmp_path),
            "deviceSource": "default",
        }
        diagnostic = await call_cli_process("status", cwd=tmp_path, exit_code=1)
        assert diagnostic["error"]["code"] == "deviceRequired"
        assert [check["result"] for check in diagnostic["data"]["checks"]] == [
            "failed",
            "not checked",
            "not checked",
        ]
        material = new_device_identity()
        save_active(
            state,
            {
                **material,
                "alias": "first",
                "credential": "first",
            },
        )
        sole = await call_cli_process("devices", cwd=tmp_path)
        assert sole["data"]["selectionDevice"] == material["deviceId"]
        assert "selectionSource" not in sole["data"]
        assert sole["data"]["context"] == {
            "workspaceRoot": str(tmp_path),
            "deviceSource": "sole",
            "deviceId": material["deviceId"],
        }
        assert not workspace.config_path(tmp_path).exists()
    finally:
        await call_cli_process("daemon", "stop", cwd=tmp_path)
    assert local_call.daemon_free(isolated_state)


async def test_workspace_precedence_across_real_cli_processes(tmp_path, isolated_state):
    private_directory(isolated_state)
    state = HostState(isolated_state)
    first_root, second_root = tmp_path / "a", tmp_path / "b"
    first_root.mkdir()
    second_root.mkdir()
    received = []

    def dispatch_for(label):
        async def dispatch(method, args):
            received.append((label, method))
            return completed(method, {"apps": [], "fixture": label}).wire()

        return dispatch

    first = Gateway(tmp_path / "device-a", dispatch=dispatch_for("a"))
    second = Gateway(tmp_path / "device-b", dispatch=dispatch_for("b"))
    first.credentials["first"] = "pair-a"
    second.credentials["second"] = "pair-b"
    try:
        async with first.listening(), second.listening():
            save_active(
                state,
                {
                    **first.identity,
                    "alias": "first",
                    "credential": "first",
                    "pairingId": "pair-a",
                },
            )
            save_active(
                state,
                {
                    **second.identity,
                    "alias": "second",
                    "credential": "second",
                    "pairingId": "pair-b",
                },
            )
            await call_cli_process("device", "select", "first", cwd=first_root)
            shown = await call_cli_process("devices", cwd=first_root)
            assert shown["data"]["selectionDevice"] == first.identity["deviceId"]
            assert "selectionSource" not in shown["data"]
            assert shown["data"]["context"] == {
                "workspaceRoot": str(first_root),
                "deviceSource": "global",
                "deviceId": first.identity["deviceId"],
            }
            assert_call_context(
                await call_cli_process("apps", cwd=first_root),
                first_root,
                first.identity["deviceId"],
                "global",
                "default",
                "default",
            )
            await call_cli_process(
                "--device", "first", "-s", "task-a", "use", cwd=first_root
            )
            await call_cli_process(
                "--device", "second", "-s", "task-b", "use", cwd=second_root
            )
            listed = await call_cli_process("devices", cwd=first_root)
            assert "selectionSource" not in listed["data"]
            assert listed["data"]["context"] == {
                "workspaceRoot": str(first_root),
                "deviceSource": "config",
                "deviceId": first.identity["deviceId"],
            }
            environment_list = await call_cli_process(
                "devices", cwd=first_root, env={"ANDROIDCTL_DEVICE": "second"}
            )
            assert environment_list["data"]["context"] == {
                "workspaceRoot": str(first_root),
                "deviceSource": "env",
                "deviceId": second.identity["deviceId"],
            }
            explicit_list = await call_cli_process(
                "--device",
                "first",
                "devices",
                cwd=first_root,
                env={"ANDROIDCTL_DEVICE": "second"},
            )
            assert explicit_list["data"]["context"] == {
                "workspaceRoot": str(first_root),
                "deviceSource": "args",
                "deviceId": first.identity["deviceId"],
            }
            assert received == [("a", "apps")]
            a = await call_cli_process("session", "open", cwd=first_root)
            b = await call_cli_process("apps", cwd=second_root)
            assert_call_context(
                a, first_root, first.identity["deviceId"], "config", "task-a", "config"
            )
            assert_call_context(
                b,
                second_root,
                second.identity["deviceId"],
                "config",
                "task-b",
                "config",
            )
            assert [item[0] for item in received[-2:]] == ["a", "b"]
            mixed = await call_cli_process(
                "--device",
                "second",
                "-s",
                "default",
                "apps",
                cwd=first_root,
                env={"ANDROIDCTL_DEVICE": "first", "ANDROIDCTL_SESSION": "environment"},
            )
            assert_call_context(
                mixed,
                first_root,
                second.identity["deviceId"],
                "args",
                "default",
                "args",
            )
            mixed = await call_cli_process(
                "--device",
                "second",
                "apps",
                cwd=first_root,
                env={"ANDROIDCTL_SESSION": "environment"},
            )
            assert_call_context(
                mixed,
                first_root,
                second.identity["deviceId"],
                "args",
                "environment",
                "env",
            )
            mixed = await call_cli_process(
                "apps", cwd=first_root, env={"ANDROIDCTL_DEVICE": "second"}
            )
            assert_call_context(
                mixed,
                first_root,
                second.identity["deviceId"],
                "env",
                "task-a",
                "config",
            )
            await call_cli_process("-s", "updated", "use", cwd=first_root)
            await call_cli_process(
                "--device",
                "second",
                "use",
                cwd=first_root,
                env={"ANDROIDCTL_SESSION": "ignored"},
            )
            assert workspace.read(first_root) == {
                "device": second.identity["deviceId"],
                "session": "updated",
            }
            assert state.selected_id() == first.identity["deviceId"]
            assert_call_context(
                await call_cli_process("apps", cwd=first_root),
                first_root,
                second.identity["deviceId"],
                "config",
                "updated",
                "config",
            )
    finally:
        await call_cli_process("daemon", "stop", cwd=tmp_path)
    assert local_call.daemon_free(isolated_state)
    assert not (isolated_state / "daemon.json").exists()


async def test_forgotten_workspace_default_stays_local_and_blocks_device_rpc(
    tmp_path, isolated_state
):
    private_directory(isolated_state)
    state = HostState(isolated_state)
    received = []

    async def dispatch(method, args):
        received.append(method)
        return completed(method, {"apps": []}).wire()

    device = Gateway(tmp_path / "device", dispatch=dispatch)
    device.credentials["saved"] = "pair"
    workspace.save(
        tmp_path, {"device": device.identity["deviceId"], "session": "saved-task"}
    )
    path = workspace.config_path(tmp_path)
    before = path.read_bytes()
    try:
        async with device.listening():
            save_active(
                state, {**device.identity, "alias": "saved", "credential": "saved"}
            )
            await call_cli_process("device", "forget", "saved", cwd=tmp_path)
            assert path.read_bytes() == before
            failed = await call_cli_process("apps", cwd=tmp_path, exit_code=2)
            assert failed["error"]["code"] == "deviceNotFound"
            stale = await call_cli_process("devices", cwd=tmp_path)
            assert stale["data"]["selectionDevice"] == device.identity["deviceId"]
            assert stale["data"]["selectionError"]["code"] == "deviceNotFound"
            assert "deviceId" not in stale["data"]["context"]
            shown = await call_cli_process("use", cwd=tmp_path)
            assert shown["data"] == {
                "device": device.identity["deviceId"],
                "session": "saved-task",
                "path": str(path),
                "saved": False,
            }
            await call_cli_process("-s", "retained", "use", cwd=tmp_path)
            assert workspace.read(tmp_path) == {
                "device": device.identity["deviceId"],
                "session": "retained",
            }
            assert not received
    finally:
        await call_cli_process("daemon", "stop", cwd=tmp_path)
    assert local_call.daemon_free(isolated_state)
