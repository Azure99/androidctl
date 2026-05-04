from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol

import click
import httpx
from pydantic import ValidationError

from androidctl.command_payloads import (
    CliCommandPayload,
    LateBoundActionCommand,
    LateBoundGlobalActionCommand,
    LateBoundWaitCommand,
)
from androidctl.daemon.client import (
    DaemonApiError,
    DaemonProtocolError,
    IncompatibleDaemonError,
)
from androidctl.daemon.discovery import (
    discover_existing_daemon_client,
    resolve_daemon_client,
)
from androidctl.errors.models import ErrorTier
from androidctl.workspace.resolve import resolve_workspace_root
from androidctl_contracts.command_catalog import runtime_close_entry
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
    dump_canonical_command_result,
)
from androidctl_contracts.daemon_api import (
    CommandRunRequest,
    RuntimePayload,
)

CommandResultPayload = CommandResultCore | RetainedResultEnvelope | ListAppsResult


class RuntimeCommandClient(Protocol):
    def get_runtime(self) -> RuntimePayload: ...

    def run_command(
        self,
        *,
        request: CommandRunRequest,
    ) -> CommandResultPayload: ...

    def close_runtime(self) -> RetainedResultEnvelope: ...


class PreDispatchCommandError(Exception):
    def __init__(
        self,
        cause: Exception,
        *,
        execution_outcome: str | None = None,
        error_tier: ErrorTier | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.execution_outcome = execution_outcome
        self.error_tier = error_tier


@dataclass(frozen=True)
class CliCommandRequest:
    public_command: str
    command: CliCommandPayload
    workspace_root: Path | None = None


@dataclass(frozen=True)
class CommandOutcome:
    payload: dict[str, object]


@dataclass(frozen=True)
class AppContext:
    daemon: RuntimeCommandClient | None
    cwd: Path
    env: Mapping[str, str]
    daemon_discovery: Callable[[Path], RuntimeCommandClient] | None = None


def build_context() -> AppContext:
    return AppContext(
        daemon=None,
        cwd=Path.cwd(),
        env=os.environ,
        daemon_discovery=lambda workspace_root: resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=Path.cwd(),
            env=os.environ,
        ),
    )


def run_command(cli_request: CliCommandRequest, ctx: AppContext) -> CommandOutcome:
    try:
        workspace_root = resolve_runtime_paths(cli_request.workspace_root, ctx)
    except (OSError, ValueError) as error:
        _raise_pre_dispatch_error(error, cli_request.command)

    try:
        daemon = _resolve_command_daemon(ctx, workspace_root)
    except (DaemonApiError, click.ClickException, OSError, ValidationError) as error:
        _raise_pre_dispatch_error(error, cli_request.command)

    try:
        runtime_payload = daemon.get_runtime()
    except (
        DaemonApiError,
        DaemonProtocolError,
        OSError,
        ValidationError,
        httpx.HTTPStatusError,
        httpx.RequestError,
    ) as error:
        _raise_pre_dispatch_error(error, cli_request.command)

    try:
        prepared_request = _prepare_ref_bound_request(cli_request, runtime_payload)
    except DaemonApiError as error:
        _raise_pre_dispatch_error(
            error,
            cli_request.command,
            error_tier=_late_bind_error_tier(error),
        )

    try:
        command_request = _build_command_run_request(prepared_request.command)
    except ValidationError as error:
        _raise_pre_dispatch_error(error, cli_request.command)

    result = daemon.run_command(request=command_request)
    return CommandOutcome(payload=_dump_command_result(result))


def run_close_command(
    ctx: AppContext,
    workspace_root_override: Path | None,
) -> CommandOutcome:
    workspace_root = resolve_runtime_paths(workspace_root_override, ctx)
    daemon = ctx.daemon
    if daemon is None:
        daemon = discover_existing_daemon_client(
            workspace_root=workspace_root,
            env=ctx.env,
        )
    result = _close_result(daemon)
    return CommandOutcome(payload=_dump_command_result(result))


def resolve_runtime_paths(
    workspace_root_override: Path | None,
    ctx: AppContext,
) -> Path:
    return resolve_workspace_root(
        flag_value=workspace_root_override,
        env_value=ctx.env.get("ANDROIDCTL_WORKSPACE_ROOT"),
        cwd=ctx.cwd,
    )


def _prepare_ref_bound_request(
    cli_request: CliCommandRequest,
    runtime_payload: RuntimePayload,
) -> CliCommandRequest:
    bound_command = bind_screen_relative_command(cli_request.command, runtime_payload)
    if bound_command is cli_request.command:
        return cli_request
    return CliCommandRequest(
        public_command=cli_request.public_command,
        command=bound_command,
        workspace_root=cli_request.workspace_root,
    )


def bind_screen_relative_command(
    command: CliCommandPayload,
    runtime_payload: RuntimePayload,
) -> CliCommandPayload:
    if not isinstance(
        command,
        (
            LateBoundActionCommand,
            LateBoundGlobalActionCommand,
            LateBoundWaitCommand,
        ),
    ):
        return command

    if isinstance(command, LateBoundGlobalActionCommand):
        return command.bind(_live_screen_id(runtime_payload))

    return command.bind(_required_live_screen_id(runtime_payload))


def _required_live_screen_id(runtime_payload: RuntimePayload) -> str:
    live_screen_id = _live_screen_id(runtime_payload)
    if live_screen_id is not None:
        return live_screen_id
    status = runtime_payload.status.strip().lower()
    if status in {"ready", "connected"}:
        raise DaemonApiError(
            code="SCREEN_NOT_READY",
            message="screen is not ready yet",
            details={},
        )
    raise DaemonApiError(
        code="RUNTIME_NOT_CONNECTED",
        message="runtime is not connected to a device",
        details={},
    )


def _live_screen_id(runtime_payload: RuntimePayload) -> str | None:
    status = runtime_payload.status.strip().lower()
    current_screen_id = runtime_payload.current_screen_id
    if status != "ready" or not isinstance(current_screen_id, str):
        return None
    normalized_screen_id = current_screen_id.strip()
    return normalized_screen_id or None


def _resolve_command_daemon(
    ctx: AppContext,
    workspace_root: Path,
) -> RuntimeCommandClient:
    if ctx.daemon is not None:
        return ctx.daemon
    if ctx.daemon_discovery is None:
        raise click.ClickException("unable to start or discover androidctld daemon")
    try:
        return ctx.daemon_discovery(workspace_root)
    except (DaemonApiError, IncompatibleDaemonError):
        raise
    except (FileNotFoundError, OSError, RuntimeError, ValidationError) as error:
        raise click.ClickException(
            f"unable to start or discover androidctld daemon: {error}"
        ) from error


def _build_command_run_request(command: CliCommandPayload) -> CommandRunRequest:
    if isinstance(
        command,
        (
            LateBoundActionCommand,
            LateBoundGlobalActionCommand,
            LateBoundWaitCommand,
        ),
    ):
        raise RuntimeError("prepared command was not bound to a live screen")
    return CommandRunRequest.model_validate(
        {"command": command.model_dump(exclude_none=True, exclude_defaults=True)}
    )


def _raise_pre_dispatch_error(
    error: Exception,
    command: CliCommandPayload,
    *,
    error_tier: ErrorTier | None = None,
) -> NoReturn:
    del command
    raise PreDispatchCommandError(
        error,
        execution_outcome=None,
        error_tier=error_tier,
    ) from error


def _late_bind_error_tier(error: DaemonApiError) -> ErrorTier | None:
    if error.code in {"RUNTIME_NOT_CONNECTED", "SCREEN_NOT_READY"}:
        return "preDispatch"
    return None


def _dump_command_result(result: CommandResultPayload) -> dict[str, object]:
    if isinstance(result, CommandResultCore):
        return dump_canonical_command_result(result)
    return result.model_dump(by_alias=True, mode="json", exclude_none=True)


def _close_result(
    daemon: RuntimeCommandClient | None,
) -> RetainedResultEnvelope:
    if daemon is not None:
        return daemon.close_runtime()
    close_entry = runtime_close_entry()
    if close_entry.retained_envelope_kind is None:
        raise RuntimeError("runtime close command must use a retained envelope")
    return RetainedResultEnvelope.model_validate(
        {
            "ok": True,
            "command": close_entry.result_command,
            "envelope": close_entry.retained_envelope_kind.value,
            "artifacts": {},
            "details": {},
        }
    )
