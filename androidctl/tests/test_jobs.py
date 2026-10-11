import asyncio
import base64
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import job_commands
from androidctl.cli_options import CliOptions
from androidctl.job_commands import JobClient, query_failed
from androidctl.output import Output
from androidctl.protocol import (
    CHUNK_BYTES,
    Fault,
    Problem,
    Result,
    TransportFailure,
)


@pytest.mark.parametrize(("job", "budget"), [("jdevice", 50), ("tlocal", 10)])
def test_job_wait_short_budget_keeps_last_fact_without_query(job, budget, monkeypatch):
    async def unexpected(*_, **__):
        raise AssertionError("insufficient budget must not send a query")

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", unexpected)
    fact = Result(command="shell", result="still running", data={"job": job})
    assert asyncio.run(client.wait(fact, budget)) is fact


def output_chunk(body, metadata, offset):
    return Result(
        command="output.read",
        result="done",
        data={
            "job": "j1",
            "offset": offset,
            **metadata,
            "bytes": base64.b64encode(body[offset : offset + CHUNK_BYTES]).decode(),
        },
    )


async def test_output_reads_multiple_chunks(monkeypatch):
    body = bytes(range(256)) * 128 + b"tail\x00\xff\n"
    metadata = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    calls = []

    async def read(command, args, timeout):
        assert command == "output.read"
        assert args["job"] == "j1"
        assert args["stream"] == "stdout"
        assert 0 < timeout <= 5
        calls.append(args["offset"])
        return output_chunk(body, metadata, args["offset"])

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    assert await client.output("j1", "stdout", metadata) == body
    assert calls == [0, CHUNK_BYTES]


@pytest.mark.parametrize(
    ("changed_offset", "changed_fields", "expected_offsets"),
    [
        (0, {"job": "other"}, [0]),
        (0, {"offset": 1}, [0]),
        (CHUNK_BYTES, {"size": CHUNK_BYTES + 2}, [0, CHUNK_BYTES]),
        (CHUNK_BYTES, {"sha256": "0" * 64}, [0, CHUNK_BYTES]),
    ],
    ids=["job", "offset", "size", "sha256"],
)
async def test_output_rejects_changed_identity_or_metadata(
    monkeypatch, changed_offset, changed_fields, expected_offsets
):
    body = b"x" * CHUNK_BYTES + b"y"
    metadata = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    calls = []

    async def read(command, args, timeout):
        offset = args["offset"]
        calls.append(offset)
        value = output_chunk(body, metadata, offset)
        if offset == changed_offset:
            value.data.update(changed_fields)
        return value

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault, match="Output identity or metadata changed") as caught:
        await client.output("j1", "stdout", metadata)
    assert caught.value.problem.code == "output"
    assert calls == expected_offsets


@pytest.mark.parametrize(
    ("chunks", "declared_size", "expected_offsets"),
    [
        ({0: b"x" * (CHUNK_BYTES + 1)}, CHUNK_BYTES + 1, [0]),
        ({0: b""}, 1, [0]),
        (
            {0: b"x" * CHUNK_BYTES, CHUNK_BYTES: b"xx"},
            CHUNK_BYTES + 1,
            [0, CHUNK_BYTES],
        ),
    ],
    ids=["oversized-block", "empty-block", "exceeds-declared-size"],
)
async def test_output_rejects_invalid_chunk_lengths(
    monkeypatch, chunks, declared_size, expected_offsets
):
    metadata = {"size": declared_size, "sha256": "0" * 64}
    calls = []

    async def read(command, args, timeout):
        offset = args["offset"]
        calls.append(offset)
        value = output_chunk(b"", metadata, offset)
        value.data["bytes"] = base64.b64encode(chunks[offset]).decode()
        return value

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault, match="Invalid chunk length") as caught:
        await client.output("j1", "stdout", metadata)
    assert caught.value.problem.code == "output"
    assert calls == expected_offsets


async def test_output_rejects_invalid_base64(monkeypatch):
    metadata = {"size": 1, "sha256": "0" * 64}
    calls = []

    async def read(command, args, timeout):
        calls.append(args["offset"])
        value = output_chunk(b"x", metadata, args["offset"])
        value.data["bytes"] = "***"
        return value

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault, match="Invalid base64 chunk") as caught:
        await client.output("j1", "stdout", metadata)
    assert caught.value.problem.code == "output"
    assert calls == [0]


async def test_output_checks_the_final_digest(monkeypatch):
    body = b"x" * CHUNK_BYTES + b"y"
    metadata = {"size": len(body), "sha256": "0" * 64}
    calls = []

    async def read(command, args, timeout):
        calls.append(args["offset"])
        return output_chunk(body, metadata, args["offset"])

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault, match="Output hash mismatch") as caught:
        await client.output("j1", "stdout", metadata)
    assert caught.value.problem.code == "output"
    assert calls == [0, CHUNK_BYTES]


async def test_output_preserves_a_remote_problem(monkeypatch):
    problem = Problem(
        code="outputGone",
        message="Expired",
        stage="device.output",
        diagnostic_truncated=True,
    )
    calls = []

    async def read(command, args, timeout):
        calls.append(args["offset"])
        return Result(command=command, result="failed", error=problem)

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault) as caught:
        await client.output("j1", "stdout", {"size": 1, "sha256": "0" * 64})
    assert caught.value.problem == problem
    assert calls == [0]


async def test_output_stops_when_the_stream_budget_is_exhausted(monkeypatch):
    body = b"x" * CHUNK_BYTES + b"y"
    metadata = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    calls = []
    now = [1000.0]
    monkeypatch.setattr(job_commands, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def read(command, args, timeout):
        calls.append((args["offset"], timeout))
        now[0] += 6
        return output_chunk(body, metadata, args["offset"])

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    with pytest.raises(Fault, match="Output retrieval budget exhausted") as caught:
        await client.output("j1", "stdout", metadata)
    assert caught.value.problem.code == "output"
    assert calls == [(0, 5)]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("execution_result", ["done", "failed", "UNKNOWN"])
def test_job_output_exports_selected_stream_to_real_file(
    tmp_path, monkeypatch, stream, execution_result
):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("ANDROIDCTL_WORKSPACE_ROOT", str(workspace))
    streams = {"stdout": b"out\x00\xff" * 9000, "stderr": b"err\r\n" * 7000}
    metadata = [
        {"stream": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in streams.items()
    ]
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append((command, args))
        if command == "job.get":
            return Result(
                command=command,
                result=execution_result,
                error=(
                    Problem(code="execution", message="Original execution problem")
                    if execution_result != "done"
                    else None
                ),
                exit=3,
                data={
                    "job": "j1",
                    "execution": {"state": execution_result},
                    "cleanup": "unknown",
                    "cleanupError": "Original cleanup unconfirmed",
                    "output": {"state": "available", "streams": metadata},
                },
            )
        assert command == "output.read"
        assert args["stream"] == stream
        offset = args["offset"]
        meta = next(item for item in metadata if item["stream"] == stream)
        return Result(
            command=command,
            result="done",
            data={
                "job": "j1",
                "offset": offset,
                **meta,
                "bytes": base64.b64encode(
                    streams[stream][offset : offset + CHUNK_BYTES]
                ).decode(),
            },
        )

    monkeypatch.setattr(JobClient, "call", call)
    destination = tmp_path / "output.bin"
    response = CliRunner().invoke(
        cli.app,
        [
            "--json",
            "job",
            "output",
            "j1",
            "--stream",
            stream,
            "--out",
            destination.name,
        ],
    )
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["command"] == "job output"
    assert result["result"] == "done"
    assert result["data"]["job"] == "j1"
    assert result["data"]["stream"] == stream
    assert "exit" not in result
    assert "error" not in result
    assert not {"execution", "cleanup", "cleanupError"} & result["data"].keys()
    assert result["data"]["output"] == {
        "state": "available",
        "streams": [next(item for item in metadata if item["stream"] == stream)],
    }
    assert result["reason"] == str(destination)
    assert not list(workspace.iterdir())
    assert destination.read_bytes() == streams[stream]
    assert result["data"]["size"] == destination.stat().st_size
    assert (
        result["data"]["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    )
    assert [args["offset"] for command, args in calls if command == "output.read"] == [
        0,
        CHUNK_BYTES,
    ]


def test_job_preview_keeps_stdout_when_stderr_retrieval_fails(monkeypatch):
    stdout = b"captured stdout\x00\xff" + b"x" * CHUNK_BYTES
    metadata = {
        "stdout": {
            "stream": "stdout",
            "size": len(stdout),
            "sha256": hashlib.sha256(stdout).hexdigest(),
        },
        "stderr": {"stream": "stderr", "size": 1, "sha256": "0" * 64},
    }
    problem = Problem(
        code="outputGone", message="Stderr expired", stage="device.output"
    )
    reads = []

    async def call(self, command, args, timeout=10):
        if command == "job.get":
            return Result(
                command="shell",
                result="done",
                exit=3,
                data={
                    "job": "j1",
                    "cleanupError": "original cleanup unconfirmed",
                    "output": {
                        "state": "available",
                        "streams": list(metadata.values()),
                    },
                },
            )
        assert command == "output.read"
        assert args["job"] == "j1"
        reads.append((args["stream"], args["offset"]))
        if args["stream"] == "stderr":
            return Result(command=command, result="failed", error=problem)
        offset = args["offset"]
        return Result(
            command=command,
            result="done",
            data={
                "job": "j1",
                "offset": offset,
                **metadata["stdout"],
                "bytes": base64.b64encode(
                    stdout[offset : offset + CHUNK_BYTES]
                ).decode(),
            },
        )

    monkeypatch.setattr(JobClient, "call", call)
    response = CliRunner().invoke(cli.app, ["--json", "job", "get", "j1"])
    assert response.exit_code == 3, response.output
    result = json.loads(response.stdout)
    assert result["result"] == "done"
    assert result["exit"] == 3
    assert result["data"]["cleanupError"] == "original cleanup unconfirmed"
    assert result["command"] == "job get"
    assert result["data"]["outputError"] == problem.wire()
    assert result["data"]["outputBytes"] == {
        "stdout": base64.b64encode(stdout).decode()
    }
    assert reads == [("stdout", 0), ("stdout", CHUNK_BYTES), ("stderr", 0)]


def test_push_wait_queries_local_facts_until_resources_end(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    requests = []
    request_owners = []
    closed_owners = []

    async def call(self, command, args, *, deadline):
        request_owners.append(self)
        requests.append(command)
        if command == "file.start":
            assert args["direction"] == "push"
            assert args["local"] == str(source)
            return Result(
                command="push",
                result="still running",
                data={"job": "t-transfer", "resourcesActive": True},
            )
        assert command == "job.get"
        assert args == {"job": "t-transfer"}
        if len(requests) == 2:
            return Result(
                command="push",
                result="done",
                data={
                    "job": "t-transfer",
                    "resourcesActive": True,
                },
            )
        return Result(
            command="push",
            result="done",
            data={"job": "t-transfer", "resourcesActive": False},
        )

    async def close(self):
        closed_owners.append(self)

    monkeypatch.setattr(job_commands.LocalCall, "call", call)
    monkeypatch.setattr(job_commands.LocalCall, "close", close)
    response = CliRunner().invoke(
        cli.app, ["--json", "push", str(source), "/file", "--wait", "3s"]
    )
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["result"] == "done"
    assert result["data"]["job"] == "t-transfer"
    assert result["command"] == "push"
    assert result["data"]["resourcesActive"] is False
    assert requests == ["file.start", "job.get", "job.get"]
    assert all(owner is request_owners[0] for owner in request_owners)
    assert closed_owners == [request_owners[0]]


@pytest.mark.parametrize(
    ("failure", "expected_problem"),
    [
        (
            OSError("controlled output write failure"),
            Problem(code="output", message="controlled output write failure"),
        ),
        (
            Fault.from_problem(
                Problem(
                    code="fileCleanup",
                    message="Output cleanup failed",
                    stage="local.file",
                    diagnostic_truncated=True,
                )
            ),
            Problem(
                code="fileCleanup",
                message="Output cleanup failed",
                stage="local.file",
                diagnostic_truncated=True,
            ),
        ),
    ],
    ids=["local-os-error", "classified-file-error"],
)
def test_job_output_write_failure_keeps_its_problem_and_job(
    tmp_path, monkeypatch, failure, expected_problem
):
    body = b"known output"
    metadata = {
        "stream": "stdout",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append(command)
        if command == "job.get":
            return Result(
                command=command,
                result="done",
                data={
                    "job": "j1",
                    "output": {"state": "available", "streams": [metadata]},
                },
            )
        return Result(
            command=command,
            result="done",
            data={
                "job": "j1",
                "offset": 0,
                **metadata,
                "bytes": base64.b64encode(body).decode(),
            },
        )

    def denied(*args, **kwargs):
        raise failure

    monkeypatch.setattr(JobClient, "call", call)
    monkeypatch.setattr(job_commands, "create", denied)
    destination = tmp_path / "output.bin"
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(destination)]
    )
    assert response.exit_code == 1, response.output
    result = json.loads(response.output)
    assert result["command"] == "job output"
    assert result["result"] == "failed"
    assert result["data"]["job"] == "j1"
    assert result["reason"] == expected_problem.message
    assert result["error"] == expected_problem.wire()
    assert not destination.exists()
    assert calls == ["job.get", "output.read"]


@pytest.mark.parametrize("failure_type", [Fault, TransportFailure])
def test_job_output_read_failure_is_an_export_failure(
    tmp_path, monkeypatch, failure_type
):
    problem = Problem(
        code="replyLost",
        message="Output read reply unavailable",
        stage="local.websocket",
        diagnostic_truncated=True,
    )
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append(command)
        if command == "job.get":
            return Result(
                command=command,
                result="UNKNOWN",
                exit=3,
                error=Problem(code="execution", message="Original execution unknown"),
                data={
                    "job": "j1",
                    "output": {
                        "state": "available",
                        "streams": [
                            {"stream": "stdout", "size": 1, "sha256": "0" * 64}
                        ],
                    },
                },
            )
        assert command == "output.read"
        raise failure_type.from_problem(problem)

    monkeypatch.setattr(JobClient, "call", call)
    destination = tmp_path / "output.bin"
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(destination)]
    )
    assert response.exit_code == 1, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == "failed"
    assert value["reason"] == problem.message
    assert value["error"] == problem.wire()
    assert value["data"]["job"] == "j1"
    assert "exit" not in value
    assert not destination.exists()
    assert calls == ["job.get", "output.read"]


def test_job_output_missing_stream_metadata_fails_before_read(tmp_path, monkeypatch):
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append(command)
        return Result(
            command=command,
            result="done",
            data={"job": "j1", "output": {"state": "available", "streams": []}},
        )

    monkeypatch.setattr(JobClient, "call", call)
    destination = tmp_path / "output.bin"
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(destination)]
    )
    assert response.exit_code == 1, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == "failed"
    assert value["error"]["code"] == "output"
    assert value["data"]["job"] == "j1"
    assert calls == ["job.get"]
    assert not destination.exists()


def test_job_output_existing_file_is_rejected_before_connection(
    tmp_path, monkeypatch, capsys
):
    from androidctl import local_call

    destination = tmp_path / "output.bin"
    destination.write_bytes(b"preserve")

    async def forbidden(**kwargs):
        pytest.fail("existing output destination started a local connection")

    monkeypatch.setattr(local_call, "connect_local", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        ["androidctl", "--json", "job", "output", "j1", "--out", str(destination)],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "not sent"
    assert result["command"] == "job output"
    assert result["error"]["code"] == "fileExists"
    assert destination.read_bytes() == b"preserve"


def test_shell_pending_cleanup_is_visible_without_changing_execution(capsys):
    result = Result(
        command="shell",
        result="done",
        data={"job": "j1", "helperAlive": True, "cleanup": "pending"},
    )
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(result, {}, False, Output(), device=None, session=None)
    assert stopped.value.exit_code == 0
    output = capsys.readouterr().out
    assert "shell: done; job=j1" in output
    assert 'helperAlive=true; cleanup="pending"' in output


@pytest.mark.parametrize("export_empty", [False, True])
def test_metadata_only_for_presented_or_explicitly_exported_stream(
    capsys, export_empty
):
    data = b"printed\n"
    digest = hashlib.sha256(data).hexdigest()
    empty = hashlib.sha256(b"").hexdigest()
    result = Result(
        command="job output" if export_empty else "shell",
        result="done",
        data={
            "job": "j1",
            "output": {
                "state": "available",
                "streams": [
                    {"stream": "stdout", "size": len(data), "sha256": digest},
                    {"stream": "stderr", "size": 0, "sha256": empty},
                ],
            },
        },
    )
    if export_empty:
        result.data["stream"] = "stderr"
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(
            result,
            {} if export_empty else {"stdout": data, "stderr": b""},
            False,
            Output(),
            device=None,
            session=None,
        )
    assert stopped.value.exit_code == 0
    output = capsys.readouterr().out
    assert (digest in output) is not export_empty
    assert (empty in output) is export_empty


@pytest.mark.parametrize(
    ("options", "background", "run_ms", "wait_ms"),
    [
        ([], False, None, 10000),
        (["--background"], True, None, 0),
        (["--wait", "0"], False, None, 0),
        (["--background", "--run", "10s", "--wait", "500ms"], True, 10000, 500),
        (["--run", "1ms", "--wait", "1m"], False, 1, 60000),
        (["--run", "10m"], False, 600000, 10000),
    ],
)
def test_shell_omitted_and_explicit_budgets(
    monkeypatch, options, background, run_ms, wait_ms
):
    calls = []
    monkeypatch.setattr(
        job_commands, "run", lambda ctx, method, args, **kw: calls.append((args, kw))
    )
    result = CliRunner().invoke(cli.app, ["shell", *options, "--", "sleep 1"])
    assert result.exit_code == 0
    args, flags = calls[0]
    assert args == {
        "script": "sleep 1",
        "background": background,
        **({"runMs": run_ms} if run_ms is not None else {}),
    }
    assert flags["wait_ms"] == wait_ms


@pytest.mark.parametrize("from_file", [False, True])
@pytest.mark.parametrize("prefix", ["", "\ufeff", "\r"])
def test_cli_preserves_device_script_arguments(
    monkeypatch, tmp_path, from_file, prefix
):
    script = (
        prefix + "printf '%s' $$ $ANDROIDCTL_ARGUMENT_TEST %ANDROIDCTL_ARGUMENT_TEST%"
    )
    captured = []
    monkeypatch.setenv("ANDROIDCTL_ARGUMENT_TEST", "host-must-not-expand")
    path = tmp_path / "script.sh"
    path.write_bytes(script.encode("utf-8"))
    args = ["--script-file", str(path)] if from_file else ["--", script]
    monkeypatch.setattr(sys, "argv", ["androidctl", "shell", *args])
    monkeypatch.setattr(
        job_commands, "run", lambda ctx, method, args, **kw: captured.append(args)
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 0
    assert captured[0]["script"] == script


@pytest.mark.parametrize(
    "script", ["", "x" * 65536, "é" * 32768], ids=["empty", "ascii-limit", "utf8-limit"]
)
def test_file_script_empty_and_byte_limit_reach_existing_run(
    monkeypatch, tmp_path, script
):
    path = tmp_path / "script.sh"
    path.write_bytes(script.encode("utf-8"))
    captured = []
    monkeypatch.setattr(
        job_commands, "run", lambda ctx, method, args, **kw: captured.append((args, kw))
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "shell",
            "--script-file",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    args, _ = captured[0]
    assert args == {"script": script, "background": False}


@pytest.mark.parametrize(
    ("state", "expected"),
    [("still running", "UNKNOWN"), ("done", "done"), ("failed", "failed")],
)
def test_query_failure_retains_original_job_and_known_terminal_fact(state, expected):
    previous = Result(command="shell", result=state, data={"job": "original"})
    value = query_failed(previous, Problem(code="connection", message="offline"))
    assert value.data["job"] == "original"
    assert value.result == expected
    assert previous.data == {"job": "original"}


async def test_wait_only_uses_short_queries_and_retains_done_when_output_is_lost(
    capsys,
):
    client = JobClient(CliOptions(None, "default", False))
    calls = []

    async def query(command, args, timeout):
        calls.append((command, args, timeout))
        if len(calls) == 1:
            return Result(
                command="shell",
                result="done",
                exit=3,
                data={
                    "job": "original",
                    "cleanupError": "cleanup unconfirmed",
                    "output": {"state": "pending"},
                },
            )
        return Result(
            command="job.get",
            result="UNKNOWN",
            reason="connection lost",
            error=Problem(
                code="offline",
                message="connection lost",
                stage="device.query",
                diagnostic_truncated=True,
            ),
        )

    client.call = query
    value = await client.wait(
        Result(command="shell", result="still running", data={"job": "original"}),
        1000,
    )
    assert value.result == "done"
    assert value.data["job"] == "original"
    assert value.data["queryError"] == {
        "code": "offline",
        "message": "connection lost",
        "stage": "device.query",
        "diagnosticTruncated": True,
    }
    assert value.exit == 3
    assert value.data["cleanupError"] == "cleanup unconfirmed"
    assert value.error is None
    assert len(calls) == 2
    assert all(
        method == "job.get" and args == {"job": "original"} for method, args, _ in calls
    )
    assert 0 < calls[1][2] < calls[0][2] <= 1
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(value, {}, False, Output(), device=None, session=None)
    assert stopped.value.exit_code == 3
    text = capsys.readouterr().out
    assert "Query unavailable: connection lost\n" in text
    assert "device.query" not in text


@pytest.mark.parametrize(
    ("query_result", "query_problem", "exit_code"),
    [
        (
            "UNKNOWN",
            Problem(
                code="replyLost",
                message="Query reply unavailable",
                stage="device.websocket",
                diagnostic_truncated=True,
            ),
            3,
        ),
        (
            "not sent",
            Problem(
                code="jobUnavailable",
                message="Original job expired",
                stage="device.query",
                diagnostic_truncated=True,
            ),
            2,
        ),
        ("UNKNOWN", None, 3),
    ],
    ids=["uncertain-query", "rejected-query", "uncertain-without-problem"],
)
def test_job_output_preserves_a_query_without_job_facts(
    tmp_path, monkeypatch, query_result, query_problem, exit_code
):
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append((command, args))
        return Result(
            command=command,
            result=query_result,
            reason="Original query reason",
            data={"deviceConnectMs": 7},
            error=query_problem,
        )

    monkeypatch.setattr(JobClient, "call", call)
    destination = tmp_path / "out.bin"
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(destination)]
    )
    assert response.exit_code == exit_code, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["data"]["job"] == "j1"
    assert set(value["data"]) == {"job", "context"}
    assert value["result"] == query_result
    assert value["reason"] == "Original query reason"
    assert value.get("error") == (query_problem.wire() if query_problem else None)
    assert calls == [("job.get", {"job": "j1"})]
    assert not destination.exists()


@pytest.mark.parametrize(
    ("failure_type", "expected_result", "exit_code"),
    [(Fault, "not sent", 2), (TransportFailure, "UNKNOWN", 3)],
)
def test_job_output_query_failure_keeps_its_classification_and_problem(
    tmp_path, monkeypatch, failure_type, expected_result, exit_code
):
    problem = Problem(
        code="queryUnavailable",
        message="Original query failure",
        stage="local.websocket",
        diagnostic_truncated=True,
    )
    calls = []
    closed = []

    async def call(self, command, args, timeout=10):
        calls.append((command, args))
        raise failure_type.from_problem(problem)

    async def close(self):
        closed.append(self)

    monkeypatch.setattr(JobClient, "call", call)
    monkeypatch.setattr(JobClient, "close", close)
    destination = tmp_path / "out.bin"
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(destination)]
    )
    assert response.exit_code == exit_code, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["result"] == expected_result
    assert value["reason"] == problem.message
    assert value["error"] == problem.wire()
    assert value["data"]["job"] == "j1"
    assert calls == [("job.get", {"job": "j1"})]
    assert len(closed) == 1
    assert not destination.exists()


@pytest.mark.parametrize(
    ("execution_result", "output", "expected_reason"),
    [
        ("UNKNOWN", None, "Output is unavailable"),
        ("failed", {"state": "expired"}, "Output is expired"),
        ("not sent", {"state": "unavailable"}, "Output is unavailable"),
        ("still running", {"state": "pending"}, "Output is pending"),
    ],
)
def test_job_output_without_exportable_bytes_reports_an_export_failure(
    tmp_path, monkeypatch, execution_result, output, expected_reason
):
    calls = []

    async def call(self, command, args, timeout=10):
        calls.append(command)
        return Result(
            command="shell",
            result=execution_result,
            reason="Original execution reason",
            data={"job": "j1", **({"output": output} if output else {})},
            error=Problem(
                code="execution", message="Original execution problem", stage="shell"
            ),
            exit=3,
        )

    monkeypatch.setattr(JobClient, "call", call)
    response = CliRunner().invoke(
        cli.app, ["--json", "job", "output", "j1", "--out", str(tmp_path / "out.bin")]
    )
    assert response.exit_code == 1, response.output
    value = json.loads(response.stdout)
    assert value["command"] == "job output"
    assert value["data"]["job"] == "j1"
    assert value["result"] == "failed"
    assert value["reason"] == expected_reason
    assert "error" not in value
    assert "exit" not in value
    assert calls == ["job.get"]
    assert not (tmp_path / "out.bin").exists()


@pytest.mark.parametrize("parent_kind", ["missing", "file"])
def test_job_output_parent_is_rejected_without_rpc(
    tmp_path, monkeypatch, capsys, parent_kind
):
    parent = tmp_path / "parent"
    if parent_kind == "file":
        parent.write_bytes(b"preserve")

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid parent must not make an RPC")

    monkeypatch.setattr(JobClient, "call", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "androidctl",
            "--json",
            "job",
            "output",
            "j1",
            "--out",
            str(parent / "out.bin"),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    value = json.loads(capsys.readouterr().out)
    assert value["result"] == "not sent"
    assert value["error"]["code"] == "fileParent"
    if parent_kind == "file":
        assert parent.read_bytes() == b"preserve"
    else:
        assert not parent.exists()


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    ("state", "override", "exit_code"), [("done", None, 0), ("failed", 3, 3)]
)
def test_output_problem_preserves_execution_and_short_text(
    monkeypatch, as_json, state, override, exit_code
):
    execution = (
        Problem(code="scriptExit", message="script failed")
        if state == "failed"
        else None
    )
    issue = Problem(
        code="outputGone",
        message="Output expired",
        stage="device.output",
        diagnostic_truncated=True,
    )

    async def call(self, command, args, timeout=10):
        if command == "job.get":
            return Result(
                command="shell",
                result=state,
                error=execution,
                exit=override,
                data={
                    "job": "j1",
                    "output": {
                        "state": "available",
                        "streams": [
                            {"stream": "stdout", "size": 1, "sha256": "0" * 64}
                        ],
                    },
                },
            )
        assert command == "output.read"
        return Result(command=command, result="failed", error=issue)

    monkeypatch.setattr(JobClient, "call", call)
    response = CliRunner().invoke(
        cli.app, [*(["--json"] if as_json else []), "job", "get", "j1"]
    )
    assert response.exit_code == exit_code
    if as_json:
        value = json.loads(response.stdout)
        assert value["result"] == state
        assert value["data"]["outputError"] == issue.wire()
        assert value.get("error") == (execution.wire() if execution else None)
    else:
        assert "Output unavailable: Output expired\n" in response.stdout
        assert "device.output" not in response.stdout
        assert "diagnosticTruncated" not in response.stdout


def test_empty_output_skips_rpc_and_preview_never_executes_terminal_control(
    monkeypatch, capsys
):
    async def run():
        client = job_commands.JobClient(CliOptions(None, "default", False))

        async def forbidden(*args):
            pytest.fail("empty stream read an RPC")

        client.call = forbidden
        assert (
            await client.output(
                "j1", "stdout", {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}
            )
            == b""
        )
        with pytest.raises(Fault):
            await client.output("j1", "stdout", {"size": 0, "sha256": "0" * 64})

    asyncio.run(run())
    original = b"\x1b]0;title\x07text\rreplace"
    result = Result(command="shell", result="done", data={"job": "j1"})
    with pytest.raises(cli.typer.Exit):
        job_commands.show(
            result,
            {"stdout": original},
            False,
            device="my phone",
            session="task",
            delivery=Output(),
        )
    display = capsys.readouterr().out
    assert "\x1b" not in display
    assert "\\x1b" in display
    assert "\\x07" in display
    with pytest.raises(cli.typer.Exit):
        job_commands.show(
            result, {"stdout": original}, True, Output(), device=None, session=None
        )
    assert (
        base64.b64decode(
            json.loads(capsys.readouterr().out)["data"]["outputBytes"]["stdout"]
        )
        == original
    )
    command = job_commands.output_command(result, "stderr", "my phone", "task")
    assert command.startswith("androidctl ")
    assert "my phone" in command
    assert "task" in command
    assert "--out" in command


@pytest.mark.parametrize("state", ["done", "failed", "not sent", "UNKNOWN"])
def test_wait_returns_initial_qualified_fact_without_query_or_sleep(state, monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError(
            "A qualified snapshot must return without another query or sleep"
        )

    client = JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", unexpected)
    monkeypatch.setattr(asyncio, "sleep", unexpected)
    fact = Result(
        command="shell", result=state, data={"job": "original", "helperAlive": True}
    )
    assert asyncio.run(client.wait(fact, 1000)) is fact


@pytest.mark.parametrize(
    "pending", [{"output": {"state": "pending"}}, {"resourcesActive": True}]
)
def test_wait_returns_first_new_qualified_fact_without_tail_sleep(pending, monkeypatch):
    calls = []
    final = Result(
        command="shell", result="done", data={"job": "original", "helperAlive": True}
    )
    client = JobClient(CliOptions(None, "default", False))

    async def query(command, args, timeout):
        calls.append((command, args))
        return final

    async def unexpected(*args):
        raise AssertionError("The first qualified query result must not sleep")

    monkeypatch.setattr(client, "call", query)
    monkeypatch.setattr(asyncio, "sleep", unexpected)
    initial = Result(
        command="shell", result="done", data={"job": "original", **pending}
    )
    assert asyncio.run(client.wait(initial, 1000)) is final
    assert calls == [("job.get", {"job": "original"})]
