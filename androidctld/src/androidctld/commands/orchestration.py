"""Command run orchestration helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeAlias

from androidctl_contracts.command_catalog import (
    CommandCatalogEntry,
    entry_for_daemon_kind,
    entry_for_result_command,
    runtime_close_entry,
)
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
    dump_canonical_command_result,
)
from androidctl_contracts.vocabulary import PublicResultFamily
from androidctld.commands.command_models import InternalCommand
from androidctld.commands.models import CommandRecord, CommandStatus
from androidctld.commands.registry import CommandSpec, resolve_command_spec
from androidctld.commands.result_models import (
    build_projected_retained_failure_result,
    build_retained_success_result,
    dump_retained_result_envelope,
)
from androidctld.commands.results import (
    complete_record_with_error,
    complete_record_with_result,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.logging import configure_logging
from androidctld.protocol import CommandKind
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.store import RuntimeSerialCommandBusyError

SerialAdmission: TypeAlias = Callable[[str], AbstractContextManager[None]]
TimeFn: TypeAlias = Callable[[], float]


@dataclass(frozen=True, slots=True)
class CommandRunContext:
    runtime: WorkspaceRuntime
    command: InternalCommand
    spec: CommandSpec
    catalog_entry: CommandCatalogEntry
    expected_result_command: str
    record: CommandRecord
    started_at: str
    started_monotonic: float


_CURRENT_CONTEXT: ContextVar[CommandRunContext | None] = ContextVar(
    "androidctld_command_run_context",
    default=None,
)


class CommandRunOrchestrator:
    def __init__(
        self,
        *,
        serial_admission: SerialAdmission | None = None,
        time_fn: TimeFn | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._serial_admission = serial_admission
        self._time_fn = time_fn or (lambda: 0.0)
        self._logger = logger or configure_logging()

    def run(
        self,
        *,
        runtime: WorkspaceRuntime,
        command: InternalCommand,
        execute: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        context = self._build_context(runtime=runtime, command=command)
        self._log_command_started(context)
        try:
            with self._admit_serial_command(context):
                token = _CURRENT_CONTEXT.set(context)
                try:
                    result = execute()
                finally:
                    _CURRENT_CONTEXT.reset(token)
            finalized = self._finalize_result(
                result,
                expected_result_command=context.expected_result_command,
            )
            complete_record_with_result(context.record, finalized)
        except RuntimeSerialCommandBusyError as error:
            if context.catalog_entry.result_family is PublicResultFamily.RETAINED:
                finalized = _retained_busy_result(
                    command=context.expected_result_command,
                    message=str(error),
                )
                complete_record_with_result(context.record, finalized)
                self._log_command_finished(context)
                return finalized
            daemon_error = _serial_busy_daemon_error(error)
            complete_record_with_error(context.record, daemon_error)
            self._log_command_finished(context)
            raise daemon_error from error
        except DaemonError as error:
            complete_record_with_error(context.record, error)
            self._log_command_finished(context)
            raise
        except Exception as error:
            complete_record_with_error(
                context.record,
                _internal_record_error(error),
            )
            self._log_command_finished(context)
            raise
        self._log_command_finished(context)
        return finalized

    def close_runtime(
        self,
        *,
        runtime: WorkspaceRuntime,
        close: Callable[[], None],
    ) -> dict[str, Any]:
        del runtime
        entry = runtime_close_entry()
        started_at = _now_isoformat()
        record = CommandRecord(
            command_id="semantic-boundary",
            kind=CommandKind.CLOSE,
            status=CommandStatus.RUNNING,
            started_at=started_at,
            result_command=entry.result_command,
        )
        try:
            with self._admit_runtime_close(entry):
                close()
                finalized = self._finalize_result(
                    build_retained_success_result(command=entry.result_command),
                    expected_result_command=entry.result_command,
                )
            complete_record_with_result(record, finalized)
            return finalized
        except RuntimeSerialCommandBusyError as error:
            finalized = _retained_busy_result(
                command=entry.result_command,
                message=str(error),
            )
            complete_record_with_result(record, finalized)
            return finalized
        except DaemonError as error:
            complete_record_with_error(record, error)
            raise
        except Exception as error:
            complete_record_with_error(record, _internal_record_error(error))
            raise

    def _build_context(
        self,
        *,
        runtime: WorkspaceRuntime,
        command: InternalCommand,
    ) -> CommandRunContext:
        spec = resolve_command_spec(command)
        catalog_entry = entry_for_daemon_kind(spec.daemon_kind)
        if catalog_entry is None:
            raise ValueError(f"unknown daemon command kind: {spec.daemon_kind!r}")
        started_at = _now_isoformat()
        return CommandRunContext(
            runtime=runtime,
            command=command,
            spec=spec,
            catalog_entry=catalog_entry,
            expected_result_command=catalog_entry.result_command,
            record=CommandRecord(
                command_id="semantic-boundary",
                kind=_record_kind_for_context(command=command, spec=spec),
                status=CommandStatus.RUNNING,
                started_at=started_at,
                result_command=catalog_entry.result_command,
            ),
            started_at=started_at,
            started_monotonic=self._time_fn(),
        )

    def _admit_serial_command(
        self,
        context: CommandRunContext,
    ) -> AbstractContextManager[None]:
        if self._serial_admission is None:
            return nullcontext()
        return self._serial_admission(context.spec.daemon_kind)

    def _admit_runtime_close(
        self,
        entry: CommandCatalogEntry,
    ) -> AbstractContextManager[None]:
        if self._serial_admission is None:
            return nullcontext()
        return self._serial_admission(entry.result_command)

    def _finalize_result(
        self,
        payload: (
            CommandResultCore | RetainedResultEnvelope | ListAppsResult | dict[str, Any]
        ),
        *,
        expected_result_command: str,
    ) -> dict[str, Any]:
        catalog_entry = entry_for_result_command(expected_result_command)
        if catalog_entry is None:
            raise ValueError(f"unknown result command: {expected_result_command!r}")
        result: CommandResultCore | RetainedResultEnvelope | ListAppsResult
        if catalog_entry.result_family is PublicResultFamily.SEMANTIC:
            result = (
                payload
                if isinstance(payload, CommandResultCore)
                else CommandResultCore.model_validate(payload)
            )
        elif catalog_entry.result_family is PublicResultFamily.RETAINED:
            result = (
                payload
                if isinstance(payload, RetainedResultEnvelope)
                else RetainedResultEnvelope.model_validate(payload)
            )
        elif catalog_entry.result_family is PublicResultFamily.LIST_APPS:
            result = (
                payload
                if isinstance(payload, ListAppsResult)
                else ListAppsResult.model_validate(payload)
            )
        else:
            raise ValueError(
                f"unsupported result family: {catalog_entry.result_family!r}"
            )
        if result.command != expected_result_command:
            raise ValueError(
                "result.command must match command catalog result command: "
                f"expected {expected_result_command!r}, got {result.command!r}"
            )
        if result.command != catalog_entry.result_command:
            raise ValueError(f"unknown result command: {result.command!r}")
        if isinstance(result, RetainedResultEnvelope):
            return dump_retained_result_envelope(result)
        if isinstance(result, ListAppsResult):
            return result.model_dump(by_alias=True, mode="json")
        return dump_canonical_command_result(result)

    def _log_command_started(self, context: CommandRunContext) -> None:
        self._logger.info(
            "command started kind=%s result_command=%s",
            context.record.kind.value,
            context.expected_result_command,
        )

    def _log_command_finished(self, context: CommandRunContext) -> None:
        elapsed_ms = max(
            0.0,
            (self._time_fn() - context.started_monotonic) * 1000.0,
        )
        self._logger.info(
            "command finished kind=%s result_command=%s status=%s elapsed_ms=%.3f",
            context.record.kind.value,
            context.expected_result_command,
            context.record.status.value,
            elapsed_ms,
        )


def current_command_record(
    *,
    kind: CommandKind,
    result_command: str,
) -> CommandRecord:
    context = _CURRENT_CONTEXT.get()
    if (
        context is not None
        and context.record.kind == kind
        and context.record.result_command == result_command
    ):
        return context.record
    return CommandRecord(
        command_id="semantic-boundary",
        kind=kind,
        status=CommandStatus.RUNNING,
        started_at=_now_isoformat(),
        result_command=result_command,
    )


def _record_kind_for_context(
    *,
    command: InternalCommand,
    spec: CommandSpec,
) -> CommandKind:
    if spec.family == "global_action":
        return CommandKind.GLOBAL
    return CommandKind(command.kind.value)


def _now_isoformat() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _internal_record_error(error: BaseException) -> DaemonError:
    message = str(error) or error.__class__.__name__
    return DaemonError(
        code=DaemonErrorCode.INTERNAL_COMMAND_FAILURE,
        message=message,
        retryable=False,
        details={"exceptionType": error.__class__.__name__},
        http_status=200,
    )


def _serial_busy_daemon_error(error: BaseException) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.RUNTIME_BUSY,
        message=str(error),
        retryable=True,
        details={"reason": "overlapping_control_request"},
        http_status=200,
    )


def _retained_busy_result(*, command: str, message: str) -> dict[str, Any]:
    return dump_retained_result_envelope(
        build_projected_retained_failure_result(
            command=command,
            code=DaemonErrorCode.RUNTIME_BUSY,
            message=message,
            details={"reason": "overlapping_control_request"},
        )
    )


__all__ = [
    "CommandRunContext",
    "CommandRunOrchestrator",
    "current_command_record",
]
