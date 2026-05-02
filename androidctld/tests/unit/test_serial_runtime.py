from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from androidctld.commands.command_models import ObserveCommand
from androidctld.commands.executor import CommandExecutor
from androidctld.commands.service import CommandService
from androidctld.daemon.service import DaemonService
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import RuntimeStatus
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


def _observe_result_payload() -> dict[str, object]:
    return {
        "ok": True,
        "command": "observe",
        "category": "observe",
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


def _assert_retained_busy(
    payload: dict[str, Any],
    *,
    command: str,
    envelope: str,
) -> None:
    assert payload["ok"] is False
    assert payload["command"] == command
    assert payload["envelope"] == envelope
    assert payload["code"] == "RUNTIME_BUSY"
    assert payload["message"] == "overlapping control requests are not allowed"
    assert payload["details"] == {"reason": "overlapping_control_request"}
    assert_retained_omits_semantic_fields(payload)


def test_overlapping_control_requests_are_rejected(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)

    with (
        runtime_store.begin_serial_command("observe"),
        pytest.raises(
            RuntimeSerialCommandBusyError,
            match="overlapping control requests",
        ),
        runtime_store.begin_serial_command("tap"),
    ):
        pass


def test_daemon_service_rejects_overlapping_public_commands(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    outcomes: dict[str, Any] = {}

    def observe(*, command: ObserveCommand) -> dict[str, Any]:
        del command
        entered.set()
        assert release.wait(
            timeout=2.0
        ), "first public command was not released before timeout"
        return _observe_result_payload()

    command_service = CommandService(
        runtime_store,
        executor=CommandExecutor(handlers={"observe": observe}),
    )

    service = DaemonService(
        runtime_store=runtime_store,
        command_service=command_service,
    )
    request_body = json.dumps({"command": {"kind": "observe"}}).encode("utf-8")

    def _run_first() -> None:
        try:
            outcomes["first"] = service.handle(
                "POST",
                "/commands/run",
                {},
                request_body,
            )
        except BaseException as exc:
            outcomes["first_error"] = exc

    thread = threading.Thread(target=_run_first)
    thread.start()

    try:
        assert entered.wait(
            timeout=2.0
        ), "first public command did not enter blocking section before timeout"
        with pytest.raises(DaemonError) as error:
            service.handle("POST", "/commands/run", {}, request_body)
        assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
    finally:
        release.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "first public command thread did not stop"
        if error := outcomes.get("first_error"):
            raise error
        assert outcomes.get("first") == (200, _observe_result_payload())


@pytest.mark.parametrize(
    ("request_payload", "command", "envelope"),
    [
        (
            {
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "lan",
                        "token": "device-token",
                        "host": "127.0.0.1",
                        "port": 8123,
                    },
                }
            },
            "connect",
            "bootstrap",
        ),
        ({"command": {"kind": "screenshot"}}, "screenshot", "artifact"),
    ],
)
def test_retained_public_overlap_returns_retained_busy_envelope(
    tmp_path: Path,
    request_payload: dict[str, object],
    command: str,
    envelope: str,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )

    with runtime_store.begin_serial_command("observe"):
        status, payload = service.handle(
            "POST",
            "/commands/run",
            {},
            json.dumps(request_payload).encode("utf-8"),
        )

    assert status == 200
    _assert_retained_busy(payload, command=command, envelope=envelope)


def test_semantic_public_overlap_still_returns_outer_daemon_busy(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )
    request_body = json.dumps({"command": {"kind": "observe"}}).encode("utf-8")

    with (
        runtime_store.begin_serial_command("observe"),
        pytest.raises(DaemonError) as error,
    ):
        service.handle("POST", "/commands/run", {}, request_body)

    assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
    assert error.value.details == {"reason": "overlapping_control_request"}


def test_runtime_close_overlap_returns_lifecycle_busy_without_closing(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.current_screen_id = "screen-before-close"
    runtime_store.persist_runtime(runtime)
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )

    with runtime_store.begin_serial_command("observe"):
        status, payload = service.handle("POST", "/runtime/close", {}, b"{}")

    assert status == 200
    _assert_retained_busy(payload, command="close", envelope="lifecycle")
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.current_screen_id == "screen-before-close"


def test_runtime_close_success_holds_public_serial_lane_until_side_effects_finish(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    service = CommandService(runtime_store)
    close_entered = threading.Event()
    release_close = threading.Event()
    outcomes: dict[str, Any] = {}

    def close_runtime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        close_entered.set()
        assert release_close.wait(timeout=2.0), "close side effect did not unblock"

    service._runtime_kernel.close_runtime = close_runtime  # type: ignore[method-assign]  # noqa: SLF001

    def run_close() -> None:
        try:
            outcomes["close"] = service.close_runtime()
        except BaseException as exc:
            outcomes["close_error"] = exc

    thread = threading.Thread(target=run_close)
    thread.start()

    try:
        assert close_entered.wait(timeout=2.0), "close did not enter side effect"
        with pytest.raises(DaemonError) as error:
            service.run(command=ObserveCommand())
        assert error.value.code == DaemonErrorCode.RUNTIME_BUSY
        assert error.value.details == {"reason": "overlapping_control_request"}
    finally:
        release_close.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "close thread did not stop"
        if error := outcomes.get("close_error"):
            raise error

    assert outcomes["close"]["ok"] is True
    assert outcomes["close"]["command"] == "close"


@pytest.mark.parametrize(
    ("request_payload", "command", "envelope"),
    [
        ({"command": {"kind": "screenshot"}}, "screenshot", "artifact"),
    ],
)
def test_secondary_lane_busy_returns_retained_failure(
    tmp_path: Path,
    request_payload: dict[str, object],
    command: str,
    envelope: str,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    service = DaemonService(
        runtime_store=runtime_store,
        command_service=CommandService(runtime_store),
    )

    assert runtime.progress_lock.acquire(blocking=False) is True
    try:
        status, payload = service.handle(
            "POST",
            "/commands/run",
            {},
            json.dumps(request_payload).encode("utf-8"),
        )
    finally:
        runtime.progress_lock.release()

    assert status == 200
    assert payload["ok"] is False
    assert payload["command"] == command
    assert payload["envelope"] == envelope
    assert payload["code"] == "RUNTIME_BUSY"
    assert payload["details"]["reason"] == "runtime_progress_busy"
    assert_retained_omits_semantic_fields(payload)
