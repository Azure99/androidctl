from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from copy import copy
from importlib.resources import files
from io import BufferedWriter
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from typer.main import get_command

from . import workspace
from .cli_options import (
    CliCommand,
    CliOptions,
    CommandFailure,
    call_context,
    command_name,
    device_context,
    idle_duration,
    load_workspace_options,
    options,
    parse_duration,
)
from .identity import device_endpoint, pairing_endpoint
from .job_commands import register as register_jobs
from .local_call import LocalCall, confirm_stop
from .output import Output, display_name
from .protocol import (
    VERSION,
    Fault,
    Result,
    TransportFailure,
    completed,
    rejected,
    uncertain,
)
from .ui_commands import register as register_ui

LOCAL_REQUEST_SECONDS = 20

app = typer.Typer(
    no_args_is_help=True,
    help="Control Android apps through Shizuku. "
    "Durations use integer ms/s/m/h; only 0 may omit the unit.",
)
session_app = typer.Typer(no_args_is_help=True, help="Own this device for a task.")
device_app = typer.Typer(help="Select, forget or update a local device pairing.")
daemon_app = typer.Typer(help="Inspect or stop the local daemon.")
app.add_typer(session_app, name="session")
app.add_typer(device_app, name="device")
app.add_typer(daemon_app, name="daemon")
register_ui(app)
register_jobs(app)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(VERSION)
        raise typer.Exit()


@app.callback()
def global_options(
    ctx: typer.Context,
    device: Annotated[
        str | None,
        typer.Option(
            "--device",
            envvar="ANDROIDCTL_DEVICE",
            help="Default device for devices/status, sessions, UI, shell "
            "and file jobs. Saved by use only when explicit.",
        ),
    ] = None,
    session: Annotated[
        str,
        typer.Option(
            "-s",
            "--session",
            envvar="ANDROIDCTL_SESSION",
            help="Default session for status, session, UI, shell and file jobs. "
            "Saved by use only when explicit.",
        ),
    ] = "default",
    workspace_root: Annotated[
        Path | None,
        typer.Option(
            "--workspace-root",
            envvar="ANDROIDCTL_WORKSPACE_ROOT",
            readable=False,
            help=(
                "Existing root for workspace defaults and automatic artifacts; "
                "defaults to the working directory."
            ),
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print detailed JSON.")
    ] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = False,
) -> None:
    del version
    root = workspace_root.absolute() if workspace_root is not None else Path.cwd()
    sources = {}
    for name in ("device", "session"):
        source = ctx.get_parameter_source(name)
        sources[name] = {"COMMANDLINE": "args", "ENVIRONMENT": "env"}.get(
            source.name if source is not None else "", "default"
        )
    ctx.obj = CliOptions(
        device, session, as_json, root, sources["device"], sources["session"]
    )


def emit_fields(values: dict[str, Any], output: Output) -> None:
    for key, value in values.items():
        if value is not None:
            text = (
                display_name(value)
                if key in ("name", "pairingName") and isinstance(value, str)
                else (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (bool, dict, list))
                    else str(value)
                )
            )
            output.echo(f"{key}={text}")


def emit_status(facts: dict[str, Any], output: Output) -> None:
    held = facts["session"]
    output.echo(
        f"device={display_name(facts['device'], quoted=False)} "
        f"connected worker={facts['worker']}"
    )
    if held.get("name"):
        output.echo(
            f"session={display_name(held['name'])} "
            f"pairing={display_name(held['pairingName'])} idle={held['idleSeconds']}s"
        )
    else:
        output.echo("session=none")
    if held.get("callerState"):
        output.echo("callerSession=" + held["callerState"])
    output.echo(f"ui={held['ui']} shell={held['shell']} file={held['file']}")
    helpers = facts.get("helpers", {})
    if helpers.get("pids"):
        output.echo("helper pids=" + ",".join(map(str, helpers["pids"])))
    if helpers.get("active"):
        output.echo("jobs=" + ",".join(helpers["active"]))
    if helpers.get("error"):
        output.echo("Shell unavailable: " + helpers["error"])
    emit_fields(facts.get("capabilities", {}), output)
    emit_fields(facts.get("diagnostics", {}), output)
    emit_fields({key: facts.get(key) for key in ("workerId", "gateway")}, output)


def emit_diagnostics(result: Result, output: Output) -> None:
    output.error(result)
    emit_fields({"cleanupError": result.data.get("cleanupError")}, output)
    output.unknown(result)


def render_summary(result: Result, output: Output) -> None:
    parts = [f"{result.command}: {result.result}"]
    if result.reason:
        parts.append(display_name(result.reason, quoted=False))
    if result.error is not None:
        parts.append(f"code={result.error.code}")
        if result.error.stage:
            parts.append(f"stage={result.error.stage}")
    output.echo("; ".join(parts) + output.context(result))
    emit_fields({"cleanupError": result.data.get("cleanupError")}, output)
    output.unknown(result)


def render_fields(result: Result, output: Output) -> None:
    output.heading(result)
    emit_fields(
        {
            key: value
            for key, value in result.data.items()
            if key not in ("cleanupError", "context")
        },
        output,
    )
    emit_diagnostics(result, output)


def render_forget(result: Result, output: Output) -> None:
    output.heading(result)
    emit_fields(
        {
            key: value
            for key, value in result.data.items()
            if key not in ("cleanupError", "context", "selectionError")
        },
        output,
    )
    if result.data.get("selectionError"):
        output.echo("Selection: " + result.data["selectionError"]["message"])
    emit_diagnostics(result, output)


def render_devices(result: Result, output: Output) -> None:
    if result.result != "done":
        render_summary(result, output)
        return
    facts = result.data
    output.heading(
        result, after_reason="; no paired devices" if not facts["devices"] else ""
    )
    for device in facts["devices"]:
        selected = " selected" if device.get("selected") else ""
        confirmation = " unconfirmed" if device.get("confirmed") is False else ""
        pending = " pending confirmation" if device.get("pending") else ""
        pairing = f" pairingId={device['pairingId']}" if "pairingId" in device else ""
        if device.get("error"):
            output.echo(f"{device['path']}: unavailable; {device['error']['message']}")
        else:
            output.echo(
                f"{display_name(device['alias'])} [{device['deviceId']}] "
                f"{device['endpoint']}{pairing}{selected}{confirmation}{pending}"
            )
    if facts.get("selectionError"):
        output.echo("Selection: " + facts["selectionError"]["message"])


def render_apps(result: Result, output: Output) -> None:
    if result.result != "done":
        render_summary(result, output)
        return
    facts = result.data
    output.heading(
        result,
        after_reason="; no matching launchable applications"
        if not facts["apps"]
        else "",
    )
    for item in facts["apps"]:
        output.echo(f"{item['package']} {display_name(item['name'])}")


def render_status(result: Result, output: Output) -> None:
    output.heading(result)
    if result.result == "done":
        emit_status(result.data, output)
    for check in result.data.get("checks", []):
        detail = check.get("reason") or check.get("detail")
        output.echo(
            f"{check['check']}: {check['result']}" + (f"; {detail}" if detail else "")
        )

    emit_diagnostics(result, output)


def render_selection(result: Result, output: Output) -> None:
    render_summary(result, output)
    if result.result == "done" and result.error is not None:
        output.echo(result.error.message)


def emit(
    result: Result,
    *,
    as_json: bool = False,
    render: Callable[[Result, Output], None] = render_fields,
) -> NoReturn:
    output = Output()
    if as_json:
        output.json(result.wire())
    else:
        render(result, output)
    output.finish(result)
    raise typer.Exit(result.exit_code())


def request_local(
    command: str,
    local: LocalCall,
    operation: Callable[[float], Awaitable[Result]],
    *,
    deadline: float | None = None,
) -> Result:
    if deadline is None:
        deadline = time.monotonic() + LOCAL_REQUEST_SECONDS

    async def request() -> Result:
        try:
            result = await operation(deadline)
        finally:
            await local.close(deadline=deadline)
        return result

    try:
        result = asyncio.run(request())
    except TransportFailure as error:
        result = uncertain(command, error)
    except Fault as error:
        result = rejected(command, error)
    result.command = command
    return result


def device_call(
    ctx: typer.Context,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    render: Callable[[Result, Output], None] = render_summary,
) -> NoReturn:
    command = command_name(ctx)
    context = load_workspace_options(options(ctx))
    local = LocalCall(context.device, context.session)
    result = request_local(
        command,
        local,
        lambda deadline: local.call(name, args or {}, deadline=deadline),
    )
    result.data["context"] = call_context(context, local.binding)
    emit(result, as_json=context.as_json, render=render)


@app.command(
    cls=CliCommand,
    help="Register and confirm a device using its current six-digit pairing code.",
)
def pair(
    ctx: typer.Context,
    address: Annotated[str, typer.Argument(metavar="HOST:PORT")],
    code: Annotated[str, typer.Argument(metavar="CODE")],
    name: Annotated[str, typer.Option("--name", help="Alias for this local pairing.")],
) -> None:
    address = pairing_endpoint(address, code, name)
    context = options(ctx)
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "pair", {"endpoint": address, "code": code, "name": name}, deadline=deadline
        ),
    )
    emit(result, as_json=context.as_json, render=render_fields)


@app.command(
    cls=CliCommand,
    help="List local pairing records, selection and pending confirmations.",
)
def devices(ctx: typer.Context) -> None:
    context = load_workspace_options(options(ctx))
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "devices", {"device": context.device}, deadline=deadline
        ),
    )
    selection = result.data["context"] if result.result == "done" else {}
    result.data["context"] = device_context(
        context, selection.get("deviceId"), selection.get("deviceSource")
    )
    emit(result, as_json=context.as_json, render=render_devices)


@app.command(
    cls=CliCommand,
    help="Show saved defaults, update explicit device/session fields, or clear them.",
)
def use(
    ctx: typer.Context,
    clear: Annotated[
        bool, typer.Option("--clear", help="Remove this workspace's configuration.")
    ] = False,
) -> None:
    context = options(ctx)
    workspace.require_root(context.workspace_root)
    if clear:
        result = workspace.clear(context.workspace_root)
    else:
        saved = workspace.read(context.workspace_root)
        if "args" not in (context.device_source, context.session_source):
            result = completed(
                "use",
                {
                    **saved,
                    "path": str(workspace.config_path(context.workspace_root)),
                    "saved": False,
                },
            )
        else:
            if context.session_source == "args":
                if not context.session:
                    raise Fault(
                        "arguments", "Workspace session must be a nonempty string"
                    )
                saved["session"] = context.session
            if context.device_source == "args":
                local = LocalCall(context.device)
                deadline = time.monotonic() + LOCAL_REQUEST_SECONDS

                async def bind() -> None:
                    try:
                        binding = await local.bind(deadline)
                        saved["device"] = binding.device_id
                    finally:
                        await local.close(deadline=deadline)

                asyncio.run(bind())
            result = workspace.save(context.workspace_root, saved)
    emit(result, as_json=context.as_json, render=render_fields)


@app.command(
    cls=CliCommand,
    help="Inspect the selected device, worker, session and active resources.",
)
def status(ctx: typer.Context) -> None:
    device_call(ctx, "status", render=render_status)


@app.command(
    cls=CliCommand,
    help="List launchable apps, optionally filtering name/package by substring.",
)
def apps(
    ctx: typer.Context, query: Annotated[str | None, typer.Argument()] = None
) -> None:
    device_call(ctx, "apps", {"query": query or ""}, render=render_apps)


@app.command(
    cls=CliCommand,
    help="Extract this package's top-level skill Markdown files to a new directory.",
)
def skill(
    ctx: typer.Context,
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            metavar="NEW_DIRECTORY",
            readable=False,
            help="New directory in an existing parent directory.",
        ),
    ],
) -> None:
    out = out.absolute()
    resource = files("androidctl.agent_skill")
    created = False
    try:
        out.mkdir()
        created = True
        exported = []
        entries = sorted(
            (
                entry
                for entry in resource.iterdir()
                if entry.is_file() and entry.name.endswith(".md")
            ),
            key=lambda entry: entry.name,
        )
        for entry in entries:
            name = entry.name
            payload = entry.read_bytes()
            path = out / name
            with path.open("xb") as target:
                digest = write_resource(target, (payload,))
            exported.append(
                {
                    "name": name,
                    "path": str(path),
                    "sha256": digest,
                }
            )
        result = completed(
            "skill",
            {"path": str(out), "version": VERSION, "files": exported},
        )
    except OSError as error:
        result = resource_failure("skill", out, error, created)
    emit(result, as_json=options(ctx).as_json)


def write_resource(target: BufferedWriter, chunks: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        target.write(chunk)
        digest.update(chunk)
    return digest.hexdigest()


def resource_failure(command: str, out: Path, error: OSError, created: bool) -> Result:
    return Result(
        command=command,
        result="failed" if created else "not sent",
        reason=f"{out}: {error}" + ("; partial output retained" if created else ""),
    )


@device_app.command(
    "select", cls=CliCommand, help="Save DEVICE as the user-wide default device."
)
def device_select(
    ctx: typer.Context, device: Annotated[str, typer.Argument(metavar="DEVICE")]
) -> None:
    context = options(ctx)
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "device.select", {"device": device}, deadline=deadline
        ),
    )
    emit(result, as_json=context.as_json, render=render_selection)


@device_app.command(
    "forget",
    cls=CliCommand,
    help="Remove one local pairing; does not revoke or stop device work.",
)
def device_forget(
    ctx: typer.Context, device: Annotated[str, typer.Argument(metavar="DEVICE")]
) -> None:
    context = options(ctx)
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "device.forget", {"device": device}, deadline=deadline
        ),
    )
    emit(result, as_json=context.as_json, render=render_forget)


@device_app.command(
    "address",
    cls=CliCommand,
    help="Update DEVICE's local pairing address without changing certificate trust.",
)
def device_address(
    ctx: typer.Context,
    device: Annotated[str, typer.Argument(metavar="DEVICE")],
    address: Annotated[str, typer.Argument(metavar="HOST:PORT")],
) -> None:
    address = device_endpoint(address)
    context = options(ctx)
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "device.address", {"device": device, "endpoint": address}, deadline=deadline
        ),
    )
    emit(result, as_json=context.as_json, render=render_summary)


@device_app.command(
    "trust",
    cls=CliCommand,
    help="Import a certificate obtained through a trusted path.",
)
def device_trust(
    ctx: typer.Context,
    device: Annotated[str, typer.Argument(metavar="DEVICE")],
    file: Annotated[Path, typer.Argument(readable=False)],
) -> None:
    file = file.absolute()
    try:
        if not file.exists() or file.is_dir():
            raise Fault("arguments", f"Certificate must be an existing file: {file}")
        pem = file.read_text(encoding="ascii")
    except (OSError, ValueError) as error:
        raise Fault("certificate", f"{file}: {error}") from error
    context = options(ctx)
    local = LocalCall()
    result = request_local(
        command_name(ctx),
        local,
        lambda deadline: local.request(
            "device.trust", {"device": device, "pem": pem}, deadline=deadline
        ),
    )
    emit(result, as_json=context.as_json, render=render_summary)


@session_app.command(
    "open", cls=CliCommand, help="Open a session and clear its old refs and images."
)
def session_open(
    ctx: typer.Context,
    idle: Annotated[
        int,
        typer.Option(
            "--idle",
            parser=parse_duration,
            callback=idle_duration,
            metavar="DURATION",
            show_default="10m",
            help="Idle lease: whole seconds, 1s–24h.",
        ),
    ] = 600000,
) -> None:
    device_call(ctx, "session.open", {"idleSeconds": idle})


@session_app.command(
    "close",
    cls=CliCommand,
    help="Close the caller's session and clear its refs and images.",
)
def session_close(ctx: typer.Context) -> None:
    device_call(ctx, "session.close")


@session_app.command(
    "takeover",
    cls=CliCommand,
    help="Take over a session of the same pairing when it has no foreground resources.",
)
def session_takeover(ctx: typer.Context) -> None:
    device_call(ctx, "session.takeover")


@daemon_app.command(
    "status", cls=CliCommand, help="Inspect the local daemon without starting it."
)
def daemon_status(ctx: typer.Context) -> None:
    context = options(ctx)
    command = command_name(ctx)
    local = LocalCall()
    result = request_local(
        command,
        local,
        lambda deadline: local.request("daemon.status", deadline=deadline, start=False),
    )
    if result.error is not None and result.error.code == "daemonStopped":
        result = completed(command, reason="stopped")
    emit(result, as_json=context.as_json, render=render_fields)


@daemon_app.command(
    "stop",
    cls=CliCommand,
    help="Request shutdown and confirm the local daemon instance has stopped.",
)
def daemon_stop(ctx: typer.Context) -> None:
    context = options(ctx)
    command = command_name(ctx)
    deadline = time.monotonic() + LOCAL_REQUEST_SECONDS
    local = LocalCall()
    result = request_local(
        command,
        local,
        lambda deadline: local.request("daemon.stop", deadline=deadline, start=False),
        deadline=deadline,
    )
    if result.error is not None and result.error.code == "daemonStopped":
        result = completed(command, reason="already stopped")
    elif result.result == "done":
        result = asyncio.run(confirm_stop(result, deadline))
    emit(result, as_json=context.as_json, render=render_summary)


def error_json(args: list[str]) -> bool:
    command = get_command(app)
    # Reuse root option arity and '--' handling without executing callbacks.
    probe = copy(command)
    probe.params = [copy(parameter) for parameter in command.params]
    for parameter in probe.params:
        parameter.callback = None
    with probe.make_context(
        None,
        list(args),
        resilient_parsing=True,
        ignore_unknown_options=True,
        allow_extra_args=True,
    ) as context:
        return context.params.get("as_json") is True


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    if not sys.stdout.isatty():
        app.rich_markup_mode = None
    try:
        code = app(standalone_mode=False, windows_expand_args=False)
    except CommandFailure as error:
        result = error.result
        as_json = error.as_json
    except typer.TyperException as error:
        message = error.format_message()
        option = getattr(error, "option_name", None)
        context = getattr(error, "ctx", None)
        if (
            context is not None
            and any(
                option in parameter.opts + parameter.secondary_opts
                for parameter in get_command(app).params
            )
            and not any(
                option in parameter.opts + parameter.secondary_opts
                for parameter in context.command.params
            )
        ):
            message += f". Place global option '{option}' before the command."
        result = rejected(command_name(context), Fault("arguments", message))
        as_json = error_json(sys.argv[1:])
    else:
        sys.exit(code if isinstance(code, int) else 0)
    try:
        emit(result, as_json=as_json, render=render_summary)
    except typer.Exit as stopped:
        sys.exit(stopped.exit_code)
