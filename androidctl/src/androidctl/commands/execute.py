from __future__ import annotations

from contextlib import suppress
from typing import NoReturn

import click
import typer

from androidctl.command_views import pre_dispatch_execution_outcome_for_public_command
from androidctl.commands import run_pipeline
from androidctl.errors.mapping import map_exception
from androidctl.errors.models import ErrorTier
from androidctl.exit_codes import ExitCode
from androidctl.output import (
    CLI_OUTPUT_FAILED,
    CLI_RENDER_FAILED,
    CliOutputError,
    static_cli_failure_xml_bytes,
    write_stderr_bytes,
    write_stderr_xml,
    write_stdout_xml,
)
from androidctl.renderers.xml import render_error_text, render_success_text

_RETAINED_ENVIRONMENT_FAILURE_CODES = frozenset(
    {
        "WORKSPACE_STATE_UNWRITABLE",
        "DEVICE_AGENT_UNAUTHORIZED",
        "DEVICE_AGENT_VERSION_MISMATCH",
    }
)


def run_and_render(
    cli_request: run_pipeline.CliCommandRequest,
    *,
    public_command: str | None = None,
) -> None:
    render_command = public_command or cli_request.public_command
    try:
        outcome = run_pipeline.run_command(cli_request, run_pipeline.build_context())
    except typer.Exit:
        raise
    except Exception as error:
        render_exception(error, command=render_command)
    render_command_outcome(
        outcome=outcome,
        public_command=render_command,
    )


def render_command_outcome(
    *,
    outcome: run_pipeline.CommandOutcome,
    public_command: str,
) -> None:
    try:
        xml_text = render_outcome(payload=outcome.payload)
    except Exception:
        _emit_cli_render_failed(command=public_command)
    try:
        write_stdout_xml(xml_text)
    except CliOutputError:
        _emit_cli_output_failed(command=public_command)
    _exit_for_command_failure(outcome.payload)


def render_outcome(
    *,
    payload: dict[str, object],
) -> str:
    return render_success_text(payload=payload)


def render_exception(
    error: Exception,
    *,
    command: str | None = None,
    execution_outcome: str | None = None,
) -> NoReturn:
    resolved_command = command or _current_public_command()
    mapped_error = error
    if isinstance(error, run_pipeline.PreDispatchCommandError):
        mapped_error = error.cause
        if execution_outcome is None:
            execution_outcome = error.execution_outcome or (
                pre_dispatch_execution_outcome_for_public_command(resolved_command)
            )

    public_error = map_exception(mapped_error)
    tier = _error_tier(
        wrapper=error,
        mapped_error=mapped_error,
    )
    try:
        xml_text = render_error_text(
            public_error,
            command=resolved_command,
            tier=tier,
            execution_outcome=execution_outcome,
        )
    except Exception:
        _emit_cli_render_failed(command=resolved_command)
    try:
        write_stderr_xml(xml_text)
    except CliOutputError:
        _emit_cli_output_failed(command=resolved_command)
    raise typer.Exit(code=int(public_error.exit_code)) from error


def _emit_cli_render_failed(*, command: str | None) -> NoReturn:
    _emit_static_cli_failure(command=command, code=CLI_RENDER_FAILED)


def _emit_cli_output_failed(*, command: str | None) -> NoReturn:
    _emit_static_cli_failure(command=command, code=CLI_OUTPUT_FAILED)


def _emit_static_cli_failure(*, command: str | None, code: str) -> NoReturn:
    with suppress(CliOutputError):
        write_stderr_bytes(static_cli_failure_xml_bytes(command=command, code=code))
    raise typer.Exit(code=int(ExitCode.ENVIRONMENT))


def _exit_for_command_failure(payload: dict[str, object]) -> None:
    if payload.get("ok") is False:
        raise typer.Exit(code=int(_failure_exit_code(payload)))


def _failure_exit_code(payload: dict[str, object]) -> ExitCode:
    if "envelope" in payload:
        return _retained_failure_exit_code(payload)
    return ExitCode.ERROR


def _retained_failure_exit_code(payload: dict[str, object]) -> ExitCode:
    code = payload.get("code")
    if isinstance(code, str) and code in _RETAINED_ENVIRONMENT_FAILURE_CODES:
        return ExitCode.ENVIRONMENT
    return ExitCode.ERROR


def emit_usage_error(
    message: str,
    *,
    command: str | None = None,
    execution_outcome: str | None = None,
) -> NoReturn:
    resolved_command = command or _current_public_command()
    render_exception(
        click.UsageError(message),
        command=resolved_command,
        execution_outcome=execution_outcome
        or pre_dispatch_execution_outcome_for_public_command(resolved_command),
    )


def _error_tier(
    *,
    wrapper: Exception,
    mapped_error: Exception,
) -> ErrorTier:
    if isinstance(mapped_error, click.UsageError):
        return "usage"
    if not isinstance(wrapper, run_pipeline.PreDispatchCommandError):
        return "outer"
    if wrapper.error_tier == "preDispatch":
        return "preDispatch"
    return "outer"


def _current_public_command() -> str | None:
    context = click.get_current_context(silent=True)
    if context is None:
        return None
    command_name = context.info_name
    if isinstance(command_name, str) and command_name:
        return command_name
    return None
