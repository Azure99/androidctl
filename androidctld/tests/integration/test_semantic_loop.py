from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from androidctld.commands.command_models import ObserveCommand, WaitCommand
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.service import CommandService
from androidctld.daemon.service import DaemonService
from androidctld.errors import DaemonError, DaemonErrorCode

from ..support.runtime_store import runtime_store_for_workspace


class _CaptureCommandService:
    def __init__(self) -> None:
        self.commands: list[Any] = []

    def run(
        self,
        *,
        command: Any,
    ) -> dict[str, Any]:
        self.commands.append(command)
        return {"ok": True}

    def close_runtime(self) -> dict[str, Any]:
        raise AssertionError("close_runtime should not be called")


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


def _command_result_payload(
    *,
    command: str,
    category: str,
) -> dict[str, object]:
    return {
        "ok": True,
        "command": command,
        "category": category,
        "payloadMode": "full",
        "nextScreenId": "screen-1",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "authoritative",
        },
        "screen": _public_screen_payload("screen-1"),
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


def test_daemon_loop_rejects_overlapping_wait_and_observe(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    thread_errors: list[BaseException] = []

    def wait(*, command: WaitCommand) -> dict[str, object]:
        del command
        entered.set()
        assert release.wait(
            timeout=2.0
        ), "blocking wait command was not released before timeout"
        return _command_result_payload(command="wait", category="wait")

    def observe(*, command: ObserveCommand) -> dict[str, object]:
        del command
        return _command_result_payload(command="observe", category="observe")

    command_service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"wait": wait, "observe": observe}),
    )

    service = DaemonService(
        runtime_store=runtime_store,
        command_service=command_service,
    )
    wait_body = json.dumps(
        {
            "command": {
                "kind": "wait",
                "predicate": {"kind": "idle"},
                "timeoutMs": 100,
            }
        }
    ).encode("utf-8")
    observe_body = json.dumps({"command": {"kind": "observe"}}).encode("utf-8")

    def _run_wait() -> None:
        try:
            service.handle("POST", "/commands/run", {}, wait_body)
        except BaseException as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=_run_wait)
    thread.start()

    try:
        assert entered.wait(
            timeout=2.0
        ), "first wait command did not enter blocking section before timeout"
        with pytest.raises(DaemonError) as error:
            service.handle("POST", "/commands/run", {}, observe_body)
        assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
    finally:
        release.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "first wait command thread did not stop"
        if thread_errors:
            raise thread_errors[0]


def test_daemon_loop_dispatches_source_less_global_action(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    command_service = _CaptureCommandService()
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=command_service,
    )

    request_body = json.dumps({"command": {"kind": "home"}}).encode("utf-8")

    status_code, payload = service.handle("POST", "/commands/run", {}, request_body)

    assert status_code == 200
    assert payload == {"ok": True}
    assert len(command_service.commands) == 1
    command = command_service.commands[0]
    assert command.kind.value == "global"
    assert command.action == "home"
    assert command.source_screen_id is None


def test_daemon_loop_allows_unbound_wait_without_source_screen_id(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    command_service = _CaptureCommandService()
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=command_service,
    )
    request_body = json.dumps(
        {
            "command": {
                "kind": "wait",
                "predicate": {"kind": "idle"},
                "timeoutMs": 100,
            }
        }
    ).encode("utf-8")

    status_code, payload = service.handle("POST", "/commands/run", {}, request_body)

    assert status_code == 200
    assert payload == {"ok": True}
    assert len(command_service.commands) == 1
    command = command_service.commands[0]
    assert command.kind.value == "wait"
    assert command.wait_kind.value == "idle"


def test_daemon_loop_screen_change_wait_fails_closed_without_authoritative_basis(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )
    request_body = json.dumps(
        {
            "command": {
                "kind": "wait",
                "predicate": {
                    "kind": "screen-change",
                    "sourceScreenId": "screen-source",
                },
                "timeoutMs": 100,
            }
        }
    ).encode("utf-8")

    status_code, payload = service.handle("POST", "/commands/run", {}, request_body)

    assert status_code == 200
    assert payload["ok"] is False
    assert payload["command"] == "wait"
    assert payload["category"] == "wait"
    assert payload["payloadMode"] == "none"
    assert payload["sourceScreenId"] == "screen-source"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert payload["code"] != "WAIT_TIMEOUT"
    assert payload["truth"] == {
        "executionOutcome": "notApplicable",
        "continuityStatus": "none",
        "observationQuality": "none",
    }
    assert "nextScreenId" not in payload
    assert "screen" not in payload
