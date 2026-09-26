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
from androidctl.protocol import CHUNK_BYTES, Fault, Problem, Result


@pytest.mark.parametrize(("job", "budget"), [("jdevice", 50)])
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


def test_shell_pending_cleanup_is_visible_without_changing_execution(capsys):
    result = Result(
        command="shell",
        result="done",
        data={"job": "j1", "helperAlive": True, "cleanup": "pending"},
    )
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(result, {}, False, Output())
    assert stopped.value.exit_code == 0
    output = capsys.readouterr().out
    assert "shell: done; job=j1" in output
    assert 'helperAlive=true; cleanup="pending"' in output


@pytest.mark.parametrize("export_empty", [False])
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
        job_commands.show(value, {}, False, Output())
    assert stopped.value.exit_code == 3
    text = capsys.readouterr().out
    assert "Query unavailable: connection lost\n" in text
    assert "device.query" not in text


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
            delivery=Output(),
        )
    display = capsys.readouterr().out
    assert "\x1b" not in display
    assert "\\x1b" in display
    assert "\\x07" in display
    with pytest.raises(cli.typer.Exit):
        job_commands.show(result, {"stdout": original}, True, Output())
    assert (
        base64.b64decode(
            json.loads(capsys.readouterr().out)["data"]["outputBytes"]["stdout"]
        )
        == original
    )


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


@pytest.mark.parametrize("pending", [{"output": {"state": "pending"}}])
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
