from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from .cli_options import (
    CliCommand,
    CliOptions,
    command_name,
    options,
    parse_duration,
    run_duration,
    wait_duration,
)
from .local_call import LocalCall
from .output import Output, display_name, terminal_text
from .protocol import (
    OUTPUT_BYTES,
    RPC_SECONDS,
    UNCERTAIN_EXIT,
    Fault,
    Problem,
    Result,
    TransportFailure,
    decode_chunk,
    rejected,
    uncertain,
)

DISPLAY_BYTES = 4096
SCRIPT_BYTES = 64 * 1024


def script_from_file(path: Path) -> str:
    path = path.absolute()
    try:
        with path.open("rb") as stream:
            body = stream.read(SCRIPT_BYTES + 1)
    except OSError as error:
        raise Fault("scriptFile", f"Cannot read script file {path}: {error}") from error
    if len(body) > SCRIPT_BYTES:
        raise Fault("scriptFile", "Script file exceeds 64 KiB")
    try:
        script = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Fault("scriptFile", "Script file must be valid UTF-8") from error
    if "\0" in script:
        raise Fault("scriptFile", "Script file must not contain NUL")
    return script


class JobClient:
    def __init__(self, context: CliOptions):
        self.local = LocalCall(context.device, context.session)

    async def call(
        self, command: str, args: dict[str, Any], timeout: float = RPC_SECONDS
    ) -> Result:
        deadline = time.monotonic() + timeout
        return await self.local.call(command, args, deadline=deadline)

    async def wait(self, result: Result, milliseconds: int) -> Result:
        identifier = result.data["job"]
        until = time.monotonic() + milliseconds / 1000

        def pending(value: Result) -> bool:
            return (
                value.result == "still running"
                or value.data.get("output", {}).get("state") == "pending"
            )

        while pending(result):
            remaining = until - time.monotonic()
            if remaining < 0.1:
                return result
            try:
                current = await self.call("job.get", {"job": identifier}, remaining)
            except Fault as error:
                return query_failed(result, error.problem)
            if current.result in ("not sent", "UNKNOWN") and not current.data.get(
                "job"
            ):
                return query_failed(
                    result,
                    current.error
                    or Problem(code="query", message=current.reason or current.result),
                )
            result = current
            if pending(result):
                await asyncio.sleep(min(0.2, max(0, until - time.monotonic())))
        return result

    async def output(self, job: str, stream: str, meta: dict[str, Any]) -> bytes:
        expected_size = meta["size"]
        expected_sha256 = meta["sha256"]
        if not 0 <= expected_size <= OUTPUT_BYTES:
            raise Fault("output", "Output size exceeds its bound")
        if expected_size == 0:
            if expected_sha256 != hashlib.sha256(b"").hexdigest():
                raise Fault("output", "Empty output hash mismatch")
            return b""
        data = bytearray()
        until = time.monotonic() + 5
        while True:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise Fault("output", "Output retrieval budget exhausted")
            result = await self.call(
                "output.read",
                {"job": job, "stream": stream, "offset": len(data)},
                remaining,
            )
            if result.result != "done":
                if result.error is not None:
                    raise Fault.from_problem(result.error)
                raise Fault("output", result.reason or result.result)
            block = result.data
            if (
                block["job"] != job
                or block["offset"] != len(data)
                or block["size"] != expected_size
                or block["sha256"] != expected_sha256
            ):
                raise Fault("output", "Output identity or metadata changed")
            data.extend(
                decode_chunk(block["bytes"], len(data), expected_size, code="output")
            )
            if len(data) == expected_size:
                if hashlib.sha256(data).hexdigest() != expected_sha256:
                    raise Fault("output", "Output hash mismatch")
                return bytes(data)

    async def close(self) -> None:
        await self.local.close()


def query_failed(previous: Result, problem: Problem) -> Result:
    value = previous.model_copy(deep=True)
    value.data["queryError"] = problem.wire()
    if value.result == "still running":
        value.result = "UNKNOWN"
        value.reason = "Current job result is unavailable; query the original ID"
        value.error = problem
    return value


def show(
    result: Result,
    streams: dict[str, bytes],
    as_json: bool,
    delivery: Output,
) -> NoReturn:
    delivery.attach(result)
    if as_json:
        if streams:
            result.data["outputBytes"] = {
                name: base64.b64encode(body).decode() for name, body in streams.items()
            }
        delivery.json(result.wire())
    else:
        job = f"; job={result.data['job']}" if result.data.get("job") else ""
        delivery.heading(result, before_reason=job)
        delivery.error(result)
        if result.data.get("helperAlive") is True or result.data.get("cleanup") in (
            "pending",
            "unknown",
        ):
            facts = [
                f"{key}={json.dumps(result.data[key])}"
                for key in ("helperAlive", "cleanup")
                if key in result.data
            ]
            delivery.echo("; ".join(facts))
        if result.data.get("cleanupError"):
            delivery.echo(f"cleanupError: {result.data['cleanupError']}")
        for stream, body in streams.items():
            if not body:
                continue
            try:
                text = body.decode("utf-8")
            except UnicodeError:
                text = None
            if text is None or "\0" in text:
                delivery.echo(f"{stream}: {len(body)} binary bytes")
                continue
            delivery.echo(f"{stream}:")
            delivery.echo(
                terminal_text(
                    text.encode()[:DISPLAY_BYTES].decode("utf-8", errors="ignore")
                ),
                nl=False,
            )
            if not text.endswith("\n") or len(body) > DISPLAY_BYTES:
                delivery.echo()
                delivery.echo(
                    "[display truncated]"
                    if len(body) > DISPLAY_BYTES
                    else "[no final newline]"
                )
        output = result.data.get("output", {})
        for entry in output.get("streams", []):
            shown = streams.get(entry["stream"])
            if shown and "size" in entry and "sha256" in entry:
                delivery.echo(
                    f"{entry['stream']}: cached size={entry['size']}; "
                    f"sha256={entry['sha256']}"
                )
            if entry.get("truncated"):
                delivery.echo(f"{entry['stream']}: cached output truncated")
        if result.data.get("outputError"):
            delivery.echo(
                f"Output unavailable: {result.data['outputError']['message']}"
            )
        elif output.get("state") in ("expired", "unavailable"):
            delivery.echo(f"Output {output['state']}; the command was not repeated.")
        elif output.get("state") == "pending":
            delivery.echo("Output pending")
        if result.data.get("queryError"):
            delivery.echo(f"Query unavailable: {result.data['queryError']['message']}")
        for error in result.data.get("signalErrors", []):
            delivery.echo(
                f"pid={error['pid']} uid={error['uid']} errno={error['errno']} "
                + display_name(error.get("command", ""))
            )
        if result.exit == UNCERTAIN_EXIT and result.data.get("signalErrors"):
            delivery.echo(
                "Signal submission failed; those processes may still modify app data."
            )
        delivery.unknown(
            result, "Do not repeat automatically; inspect the current job or device."
        )
    delivery.finish(result)
    raise typer.Exit(result.exit_code())


def output_problem(error: Exception) -> Problem:
    return (
        error.problem
        if isinstance(error, Fault)
        else Problem(code="output", message=str(error))
    )


def run(
    ctx: typer.Context,
    method: str,
    args: dict[str, Any],
    *,
    wait_ms: int = 0,
) -> NoReturn:
    command = command_name(ctx)
    context = options(ctx)
    as_json = context.as_json
    delivery = Output()
    client = JobClient(context)

    async def request() -> tuple[Result, dict[str, bytes]]:
        content: dict[str, bytes] = {}
        try:
            try:
                result = await client.call(method, args)
                if method == "shell.start" and result.data.get("job"):
                    job = result.data["job"]
                    delivery.echo(f"Job: {job}", err=as_json)
                if result.data.get("job") and wait_ms:
                    result = await client.wait(result, wait_ms)
            except TransportFailure as error:
                result = uncertain(command, error)
            except Fault as error:
                result = rejected(command, error)
            if args.get("job"):
                result.data.setdefault("job", args["job"])
            output = result.data.get("output", {})
            if output.get("state") == "available":
                try:
                    metadata = {entry["stream"]: entry for entry in output["streams"]}
                    for name in ("stdout", "stderr"):
                        content[name] = await client.output(
                            result.data["job"], name, metadata[name]
                        )
                except (Fault, OSError, KeyError, TypeError, ValueError) as error:
                    result.data["outputError"] = output_problem(error).wire()
            return result, content
        finally:
            await client.close()

    result, streams = asyncio.run(request())
    result.command = command
    show(
        result,
        streams,
        as_json,
        delivery,
    )


def register(app: typer.Typer) -> None:
    job = typer.Typer(help="Query or cancel an in-memory shell job.")
    app.add_typer(job, name="job")

    @app.command(
        "shell",
        cls=CliCommand,
        help="Run one non-interactive script; no ADB executable is used. "
        "The complete JSON request must fit 96 KiB; file size alone is not sufficient.",
    )
    def shell(
        ctx: typer.Context,
        script: Annotated[
            str | None,
            typer.Argument(
                help="One complete script, normally after --; omit with --script-file."
            ),
        ] = None,
        script_file: Annotated[
            Path | None,
            typer.Option(
                "--script-file",
                readable=False,
                help="Read a UTF-8 file without NUL, at most 64 KiB. Use LF, not CRLF.",
            ),
        ] = None,
        run_ms: Annotated[
            int | None,
            typer.Option(
                "--run",
                parser=parse_duration,
                callback=run_duration,
                metavar="DURATION",
                help="Execution limit: 1ms–10m; default 10s, or 10m with --background.",
            ),
        ] = None,
        wait_ms: Annotated[
            int | None,
            typer.Option(
                "--wait",
                parser=parse_duration,
                callback=wait_duration,
                metavar="DURATION",
                help="Wait (0–60s) without cancelling the job; "
                "default 10s, or 0 with --background.",
            ),
        ] = None,
        background: Annotated[
            bool,
            typer.Option(
                "--background",
                help="Release this shell's session foreground resource after "
                "permission to execute. "
                "The job remains managed by its helper.",
            ),
        ] = False,
    ) -> None:
        if script is None and script_file is not None:
            script = script_from_file(script_file)
        elif script is None or script_file is not None:
            raise Fault("arguments", "Provide exactly one of SCRIPT or --script-file")
        if "\r\n" in script:
            raise Fault("script", "Script contains CRLF; convert line endings to LF")
        run(
            ctx,
            "shell.start",
            {
                "script": script,
                "background": background,
                **({"runMs": run_ms} if run_ms is not None else {}),
            },
            wait_ms=wait_ms if wait_ms is not None else (0 if background else 10000),
        )

    @job.command(
        "get",
        cls=CliCommand,
        help="Query the original job, optionally waiting with --wait.",
    )
    def get(
        ctx: typer.Context,
        identifier: str,
        wait_ms: Annotated[
            int,
            typer.Option(
                "--wait",
                parser=parse_duration,
                callback=wait_duration,
                metavar="DURATION",
                help="Wait (0–60s) without cancelling the job.",
            ),
        ] = 0,
    ) -> None:
        run(ctx, "job.get", {"job": identifier}, wait_ms=wait_ms)

    @job.command(
        "cancel",
        cls=CliCommand,
        help="Request cancellation and report the job's facts.",
    )
    def cancel(ctx: typer.Context, identifier: str) -> None:
        run(ctx, "job.cancel", {"job": identifier}, wait_ms=3000)
