from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from androidctld.commands.command_models import (
    GlobalCommand,
    IdleWaitPredicate,
    ObserveCommand,
    WaitCommand,
)
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.models import CommandRecord, CommandStatus
from androidctld.commands.orchestration import (
    CommandRunOrchestrator,
    current_command_record,
)
from androidctld.commands.service import CommandService
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import CommandKind
from androidctld.runtime.store import RuntimeSerialCommandBusyError

from ..support.runtime_store import runtime_store_for_workspace
from .support.retained import assert_retained_omits_semantic_fields


def _public_screen_payload(screen_id: str) -> dict[str, object]:
    return {
        "screenId": screen_id,
        "app": {"packageName": "com.android.settings"},
        "surface": {
            "keyboardVisible": False,
            "focus": {},
        },
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {"name": "context", "nodes": []},
            {"name": "dialog", "nodes": []},
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [],
    }


def _result_payload(
    *,
    screen_id: str = "screen-1",
    command: str = "observe",
    category: str = "observe",
    extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "command": command,
        "category": category,
        "payloadMode": "full",
        "nextScreenId": screen_id,
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "authoritative",
        },
        "screen": _public_screen_payload(screen_id),
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }
    if extra is not None:
        payload.update(extra)
    return payload


def test_command_service_run_rejects_removed_debug_toggle(tmp_path: Path) -> None:
    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        return {"command": "observe", "warnings": [], "debug": None}

    service = CommandService(
        runtime_store_for_workspace(tmp_path),
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    with pytest.raises(TypeError):
        service.run(None, ObserveCommand(), debug=False)


def test_command_service_run_admits_and_releases_serial_lane_on_success(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    entered = False

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        nonlocal entered
        del command
        entered = True
        with (
            pytest.raises(RuntimeSerialCommandBusyError),
            runtime_store.begin_serial_command("tap"),
        ):
            pass
        return _result_payload()

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    payload = service.run(command=ObserveCommand())

    assert entered is True
    assert payload["command"] == "observe"
    with runtime_store.begin_serial_command("tap"):
        pass


def test_direct_command_service_run_rejects_overlapping_command(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    outcomes: dict[str, Any] = {}

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        entered.set()
        assert release.wait(
            timeout=2.0
        ), "first command was not released before timeout"
        return _result_payload()

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    def run_first() -> None:
        try:
            outcomes["first"] = service.run(command=ObserveCommand())
        except BaseException as exc:
            outcomes["first_error"] = exc

    thread = threading.Thread(target=run_first)
    thread.start()

    try:
        assert entered.wait(timeout=2.0), "first command did not enter handler"
        with pytest.raises(DaemonError) as error:
            service.run(command=ObserveCommand())
        assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
        assert error.value.details == {"reason": "overlapping_control_request"}
        assert error.value.http_status == 200
    finally:
        release.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "first command thread did not stop"
        if first_error := outcomes.get("first_error"):
            raise first_error
        assert outcomes.get("first") == _result_payload()


def test_command_service_run_releases_serial_lane_after_daemon_error(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        raise DaemonError(
            code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
            message="runtime is not connected",
            retryable=True,
            details={},
            http_status=200,
        )

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    with pytest.raises(DaemonError):
        service.run(command=ObserveCommand())
    with runtime_store.begin_serial_command("tap"):
        pass


def test_command_service_run_releases_serial_lane_after_unexpected_exception(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        raise RuntimeError("boom")

    service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    with pytest.raises(RuntimeError, match="boom"):
        service.run(command=ObserveCommand())
    with runtime_store.begin_serial_command("tap"):
        pass


def test_command_orchestrator_rejects_wrong_result_command(
    tmp_path: Path,
) -> None:
    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        return _result_payload(command="wait", category="wait")

    service = CommandService(
        runtime_store_for_workspace(tmp_path),
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    with pytest.raises(ValueError, match="result.command must match"):
        service.run(command=ObserveCommand())


def test_command_orchestrator_omits_canonical_null_fields(
    tmp_path: Path,
) -> None:
    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        return _result_payload(
            extra={
                "sourceScreenId": None,
                "code": None,
                "message": None,
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                    "changed": None,
                },
                "artifacts": {"screenshotPng": None},
            }
        )

    service = CommandService(
        runtime_store_for_workspace(tmp_path),
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    payload = service.run(command=ObserveCommand())

    assert "sourceScreenId" not in payload
    assert "code" not in payload
    assert "message" not in payload
    assert "changed" not in payload["truth"]
    assert payload["artifacts"] == {}


def test_command_orchestrator_exposes_context_record_and_completes_success(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(time_fn=_sequence_time(10.0, 10.125))
    seen_records: list[CommandRecord] = []

    def execute() -> dict[str, object]:
        record = current_command_record(
            kind=CommandKind.OBSERVE,
            result_command="observe",
        )
        seen_records.append(record)
        assert {status.value for status in CommandStatus} == {
            "running",
            "succeeded",
            "failed",
        }
        assert record.status is CommandStatus.RUNNING
        assert record.result_command == "observe"
        assert record.started_at != "1970-01-01T00:00:00Z"
        assert record.completed_at is None
        assert record.result is None
        assert record.error is None
        return _result_payload()

    payload = orchestrator.run(
        runtime=runtime_store.ensure_runtime(),
        command=ObserveCommand(),
        execute=execute,
    )

    assert payload["command"] == "observe"
    assert len(seen_records) == 1
    record = seen_records[0]
    assert record.status is CommandStatus.SUCCEEDED
    assert record.completed_at is not None
    assert record.result is not None
    assert record.result.command == "observe"
    assert record.error is None


def test_command_orchestrator_context_record_matches_wait_command(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(time_fn=_sequence_time(12.0, 12.125))
    seen_records: list[CommandRecord] = []

    def execute() -> dict[str, object]:
        seen_records.append(
            current_command_record(
                kind=CommandKind.WAIT,
                result_command="wait",
            )
        )
        return _result_payload(command="wait", category="wait")

    payload = orchestrator.run(
        runtime=runtime_store.ensure_runtime(),
        command=WaitCommand(predicate=IdleWaitPredicate()),
        execute=execute,
    )

    assert payload["command"] == "wait"
    assert len(seen_records) == 1
    record = seen_records[0]
    assert record.kind is CommandKind.WAIT
    assert record.result_command == "wait"
    assert record.status is CommandStatus.SUCCEEDED
    assert record.result is not None
    assert record.result.command == "wait"


def test_command_orchestrator_context_record_matches_global_action_command(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(time_fn=_sequence_time(14.0, 14.125))
    seen_records: list[CommandRecord] = []

    def execute() -> dict[str, object]:
        seen_records.append(
            current_command_record(
                kind=CommandKind.GLOBAL,
                result_command="home",
            )
        )
        return _result_payload(command="home", category="transition")

    payload = orchestrator.run(
        runtime=runtime_store.ensure_runtime(),
        command=GlobalCommand(action="home", source_screen_id="screen-1"),
        execute=execute,
    )

    assert payload["command"] == "home"
    assert len(seen_records) == 1
    record = seen_records[0]
    assert record.kind is CommandKind.GLOBAL
    assert record.result_command == "home"
    assert record.status is CommandStatus.SUCCEEDED
    assert record.result is not None
    assert record.result.command == "home"


def test_command_orchestrator_completes_context_record_after_daemon_error(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(time_fn=_sequence_time(20.0, 20.25))
    seen_records: list[CommandRecord] = []
    daemon_error = DaemonError(
        code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
        message="runtime is not connected",
        retryable=True,
        details={"reason": "unit-test"},
        http_status=200,
    )

    def execute() -> dict[str, object]:
        seen_records.append(
            current_command_record(
                kind=CommandKind.OBSERVE,
                result_command="observe",
            )
        )
        raise daemon_error

    with pytest.raises(DaemonError) as error:
        orchestrator.run(
            runtime=runtime_store.ensure_runtime(),
            command=ObserveCommand(),
            execute=execute,
        )

    assert error.value is daemon_error
    assert len(seen_records) == 1
    record = seen_records[0]
    assert record.status is CommandStatus.FAILED
    assert record.completed_at is not None
    assert record.result is None
    assert record.error is not None
    assert record.error.code is DaemonErrorCode.RUNTIME_NOT_CONNECTED
    assert record.error.details == {"reason": "unit-test"}


def test_command_orchestrator_marks_context_record_failed_on_validation_error(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    orchestrator = CommandRunOrchestrator(time_fn=_sequence_time(30.0, 30.25))
    seen_records: list[CommandRecord] = []

    def execute() -> dict[str, object]:
        seen_records.append(
            current_command_record(
                kind=CommandKind.OBSERVE,
                result_command="observe",
            )
        )
        return _result_payload(command="wait", category="wait")

    with pytest.raises(ValueError, match="result.command must match"):
        orchestrator.run(
            runtime=runtime_store.ensure_runtime(),
            command=ObserveCommand(),
            execute=execute,
        )

    assert len(seen_records) == 1
    record = seen_records[0]
    assert record.status is CommandStatus.FAILED
    assert record.completed_at is not None
    assert record.result is None
    assert record.error is not None
    assert record.error.code is DaemonErrorCode.INTERNAL_COMMAND_FAILURE
    assert record.error.details == {"exceptionType": "ValueError"}


def test_close_runtime_uses_orchestrator_finalizer(tmp_path: Path) -> None:
    service = CommandService(runtime_store_for_workspace(tmp_path))
    original = service._orchestrator._finalize_result
    expected_commands: list[str] = []

    def finalize(payload: Any, *, expected_result_command: str) -> dict[str, Any]:
        expected_commands.append(expected_result_command)
        return original(payload, expected_result_command=expected_result_command)

    service._orchestrator._finalize_result = finalize  # type: ignore[method-assign]

    payload = service.close_runtime()

    assert expected_commands == ["close"]
    assert payload["command"] == "close"
    assert payload["envelope"] == "lifecycle"
    assert_retained_omits_semantic_fields(payload)


def test_close_runtime_returns_lifecycle_envelope_after_retained_flip(
    tmp_path: Path,
) -> None:
    service = CommandService(runtime_store_for_workspace(tmp_path))

    payload = service.close_runtime()

    assert payload["command"] == "close"
    assert payload["envelope"] == "lifecycle"
    assert_retained_omits_semantic_fields(payload)


def test_close_runtime_uses_serial_command_lane(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = CommandService(runtime_store)

    with runtime_store.begin_serial_command("observe"):
        payload = service.close_runtime()

    assert payload["command"] == "close"
    assert payload["envelope"] == "lifecycle"
    assert payload["ok"] is False
    assert payload["code"] == "RUNTIME_BUSY"
    assert payload["details"] == {"reason": "overlapping_control_request"}
    assert_retained_omits_semantic_fields(payload)


def _sequence_time(*values: float) -> Callable[[], float]:
    pending = list(values)
    last = pending[-1]

    def time_fn() -> float:
        if pending:
            return pending.pop(0)
        return last

    return time_fn
