from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import androidctld.commands.handlers.wait as wait_handler_module
from androidctl_contracts.daemon_api import WaitCommandPayload
from androidctld.commands.command_models import (
    IdleWaitPredicate,
    TextWaitPredicate,
    WaitCommand,
)
from androidctld.commands.from_boundary import compile_service_wait_command
from androidctld.commands.handlers.wait import WaitCommandHandler
from androidctld.commands.models import CommandRecord, CommandStatus
from androidctld.device.types import DeviceEndpoint, RuntimeTransport
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import CommandKind, RuntimeStatus
from androidctld.runtime import RuntimeKernel, capture_lifecycle_lease
from androidctld.semantics.compiler import CompiledScreen
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import ScreenRefreshService
from androidctld.waits.evaluators import WaitMatchData
from androidctld.waits.loop import (
    WaitLoopOutcome,
    WaitLoopTimedOut,
    WaitRuntimeLoop,
)

from ..support.runtime_store import runtime_store_for_workspace
from .support.doubles import (
    CallbackScreenRefresh,
    RuntimeStateScreenRefresh,
    StaticScreenRefresh,
    StaticSnapshotService,
)
from .support.doubles import (
    NoPollDeviceClient as _NoPollDeviceClient,
)
from .support.doubles import (
    PassiveRuntimeKernel as _RuntimeKernel,
)
from .support.runtime import build_connected_runtime as _make_runtime
from .support.runtime import install_screen_state as _install_screen_state
from .support.semantic_screen import (
    install_snapshot_screen as _set_current_screen,
)
from .support.semantic_screen import (
    make_compiled_screen as _make_compiled_screen,
)
from .support.semantic_screen import (
    make_snapshot as _make_snapshot,
)


def _make_wait_handler(
    *,
    runtime: Any,
    snapshot_service: Any,
    screen_refresh: Any,
    sleep_fn: Any,
    time_fn: Any,
) -> WaitCommandHandler:
    wait_runtime_loop = WaitRuntimeLoop(
        snapshot_service=snapshot_service,
        screen_refresh=screen_refresh,
        device_client_factory=lambda runtime, *, lifecycle_lease=None: (
            _NoPollDeviceClient()
        ),
        sleep_fn=sleep_fn,
        time_fn=time_fn,
    )
    return WaitCommandHandler(
        runtime_kernel=_RuntimeKernel(runtime),
        wait_runtime_loop=wait_runtime_loop,
    )


class _FailIfRunWaitLoop:
    def __init__(self) -> None:
        self.run_calls = 0

    def run(
        self,
        *,
        session: Any,
        record: Any,
        command: Any,
        lifecycle_lease: Any,
    ) -> None:
        del session, record, command, lifecycle_lease
        self.run_calls += 1
        raise AssertionError("relative wait basis failure must not enter wait loop")


def _make_entry_compiled_screen(
    screen_id: str,
    *,
    snapshot: RawSnapshot,
    fingerprint: str,
    ref: str = "n1",
) -> CompiledScreen:
    return _make_compiled_screen(
        screen_id,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="" if snapshot.package_name is None else snapshot.package_name,
        activity_name=snapshot.activity_name,
        fingerprint=fingerprint,
        ref=ref,
    )


def test_semantic_screen_snapshot_timestamps_stay_stable_for_two_digit_ids() -> None:
    assert _make_snapshot(snapshot_id=10).captured_at == "2026-04-13T00:00:10Z"


def test_static_screen_refresh_echoes_refreshed_snapshot_when_unset(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _set_current_screen(runtime, _make_snapshot(label="Setup"), include_artifacts=False)
    refreshed_snapshot = _make_snapshot(snapshot_id=2, label="Wi-Fi")
    refresh = StaticScreenRefresh(
        public_screen=runtime.screen_state.public_screen,
    )

    snapshot, public_screen, artifacts = refresh.refresh(
        runtime,
        refreshed_snapshot,
        command_kind=CommandKind.WAIT,
    )

    assert snapshot is refreshed_snapshot
    assert public_screen is runtime.screen_state.public_screen
    assert artifacts is None
    assert refresh.refresh_calls == [
        {
            "runtime": runtime,
            "refreshed_snapshot": refreshed_snapshot,
            "kwargs": {"command_kind": CommandKind.WAIT},
        }
    ]


def test_callback_screen_refresh_records_calls_and_uses_runtime_state_by_default(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _set_current_screen(runtime, _make_snapshot(label="Setup"), include_artifacts=False)
    refreshed_snapshot = _make_snapshot(snapshot_id=3, label="Wi-Fi")

    def _callback(
        runtime: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        assert kwargs["command_kind"] == CommandKind.WAIT
        _set_current_screen(runtime, refreshed_snapshot, include_artifacts=False)

    refresh = CallbackScreenRefresh(callback=_callback)

    snapshot, public_screen, artifacts = refresh.refresh(
        runtime,
        refreshed_snapshot,
        command_kind=CommandKind.WAIT,
    )

    assert snapshot is runtime.latest_snapshot
    assert public_screen is runtime.screen_state.public_screen
    assert artifacts is runtime.screen_state.artifacts
    assert refresh.refresh_calls == [
        {
            "runtime": runtime,
            "refreshed_snapshot": refreshed_snapshot,
            "kwargs": {"command_kind": CommandKind.WAIT},
        }
    ]


def test_wait_handler_routes_wait_through_injected_runtime_loop(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    snapshot = _make_snapshot(label="Wi-Fi")
    _set_current_screen(runtime, snapshot)
    expected_match = WaitMatchData(snapshot=snapshot)
    seen: dict[str, Any] = {}

    class _WaitRuntimeLoop:
        def run(
            self,
            *,
            session: Any,
            record: Any,
            command: Any,
            lifecycle_lease: Any,
        ) -> Any:
            seen["session"] = session
            seen["record"] = record
            seen["command"] = command
            seen["lifecycle_lease"] = lifecycle_lease
            return expected_match

    command = WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi"))

    handler = WaitCommandHandler(
        runtime_kernel=_RuntimeKernel(runtime),
        wait_runtime_loop=_WaitRuntimeLoop(),
    )
    payload = handler.handle_service_wait(command=command)

    assert payload["ok"] is True
    assert payload["warnings"] == []
    assert seen["session"] is runtime
    assert seen["record"].kind is CommandKind.WAIT
    assert seen["record"].status is CommandStatus.RUNNING
    assert seen["command"] is command
    assert seen["lifecycle_lease"].is_current(runtime) is True


def test_wait_loop_outcome_uses_wait_match_data_directly() -> None:
    assert WaitLoopOutcome == WaitMatchData | WaitLoopTimedOut


def test_wait_handler_timeout_error_preserves_public_daemon_shape() -> None:
    error = wait_handler_module._wait_timeout_error(
        WaitCommand(predicate=IdleWaitPredicate())
    )

    assert error.code == DaemonErrorCode.WAIT_TIMEOUT
    assert error.message == "wait idle timed out"
    assert error.retryable is True
    assert error.details == {"kind": "wait", "waitKind": "idle"}
    assert error.http_status == 200


def test_wait_loop_rejects_stale_lease_before_cached_ready_success(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    lifecycle_lease = capture_lifecycle_lease(runtime)
    _set_current_screen(runtime, _make_snapshot(label="Setup"))
    runtime.lifecycle_revision += 1
    _set_current_screen(runtime, _make_snapshot(snapshot_id=2, label="Wi-Fi"))
    factory_calls: list[Any] = []

    def _device_client_factory(
        runtime: Any,
        *,
        lifecycle_lease: Any | None = None,
    ) -> _NoPollDeviceClient:
        factory_calls.append(runtime)
        return _NoPollDeviceClient()

    wait_loop = WaitRuntimeLoop(
        snapshot_service=object(),
        screen_refresh=object(),
        device_client_factory=_device_client_factory,
        sleep_fn=lambda seconds: None,
        time_fn=lambda: 0.0,
    )
    record = CommandRecord(
        command_id="command-1",
        kind=CommandKind.WAIT,
        status=CommandStatus.RUNNING,
        started_at="2026-04-16T00:00:00Z",
    )

    with pytest.raises(DaemonError) as error:
        wait_loop.run(
            session=runtime,
            record=record,
            command=WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi")),
            lifecycle_lease=lifecycle_lease,
        )

    assert error.value.code == DaemonErrorCode.COMMAND_CANCELLED
    assert error.value.message == "wait text was canceled"
    assert error.value.details["commandId"] == "command-1"
    assert error.value.details["kind"] == "wait"
    assert error.value.details["waitKind"] == "text"
    assert factory_calls == []


def test_text_present_wait_reuses_observe_style_truth_when_matched(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    _set_current_screen(runtime, _make_snapshot(label="Wi-Fi"))
    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=object(),
        screen_refresh=object(),
        sleep_fn=lambda seconds: None,
        time_fn=lambda: 0.0,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "text-present",
                        "text": "Wi-Fi",
                    },
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notApplicable"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None
    assert "sourceScreenId" not in payload


def test_app_wait_reuses_observe_style_truth_when_matched(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    _set_current_screen(
        runtime,
        _make_snapshot(package_name="com.google.android.settings.intelligence"),
    )
    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=object(),
        screen_refresh=object(),
        sleep_fn=lambda seconds: None,
        time_fn=lambda: 0.0,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "app",
                        "packageName": "com.android.settings",
                    },
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notApplicable"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None
    assert "sourceScreenId" not in payload
    assert payload["screen"]["app"]["requestedPackageName"] == "com.android.settings"
    assert (
        payload["screen"]["app"]["resolvedPackageName"]
        == "com.google.android.settings.intelligence"
    )
    assert payload["screen"]["app"]["matchType"] == "alias"


def test_text_present_wait_matches_with_current_truth_even_without_artifacts(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    snapshot = _make_snapshot(label="Wi-Fi")
    _set_current_screen(runtime, snapshot, include_artifacts=False)
    now = 0.0

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=StaticSnapshotService(snapshot),
        screen_refresh=RuntimeStateScreenRefresh(),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "text-present",
                        "text": "Wi-Fi",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notApplicable"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None
    assert "sourceScreenId" not in payload
    assert payload["artifacts"] == {}
    assert payload["screen"]["screenId"] == runtime.current_screen_id


def test_text_present_wait_refresh_path_uses_live_wait_runtime_loop_signature(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    now = 0.0
    refreshed_snapshot = _make_snapshot(label="Wi-Fi")
    refresh_calls: list[dict[str, object]] = []
    snapshot_service = StaticSnapshotService(refreshed_snapshot)

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        refresh_calls.append(kwargs)
        assert kwargs["wait_kind"] == "text"
        assert kwargs["command_kind"].value == "wait"
        assert kwargs["record"].kind.value == "wait"
        _set_current_screen(session, refreshed_snapshot, include_artifacts=False)

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "text-present",
                        "text": "Wi-Fi",
                    },
                    "timeoutMs": 500,
                }
            )
        )
    )

    assert len(refresh_calls) == 1
    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notApplicable"
    assert payload["truth"]["continuityStatus"] == "none"
    assert "sourceScreenId" not in payload
    assert payload["screen"]["screenId"] == runtime.current_screen_id


def test_screen_change_wait_stale_refresh_preserves_wait_kind_details(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.connection = _make_runtime(tmp_path).connection
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )
    runtime.status = RuntimeStatus.READY
    _set_current_screen(runtime, _make_snapshot(label="Setup"))
    source_screen_id = runtime.current_screen_id
    assert source_screen_id is not None

    class _StaleSnapshotService:
        def fetch(
            self,
            runtime: Any,
            force_refresh: bool = False,
            *,
            lifecycle_lease: Any = None,
        ) -> RawSnapshot:
            del force_refresh, lifecycle_lease
            runtime.lifecycle_revision += 1
            return _make_snapshot(snapshot_id=2, label="Next")

    runtime_kernel = RuntimeKernel(runtime_store)
    screen_refresh = ScreenRefreshService(
        runtime_kernel=runtime_kernel,
    )
    wait_runtime_loop = WaitRuntimeLoop(
        snapshot_service=_StaleSnapshotService(),
        screen_refresh=screen_refresh,
        device_client_factory=lambda runtime, *, lifecycle_lease=None: (
            _NoPollDeviceClient()
        ),
        sleep_fn=lambda seconds: None,
        time_fn=lambda: 0.0,
    )
    handler = WaitCommandHandler(
        runtime_kernel=runtime_kernel,
        wait_runtime_loop=wait_runtime_loop,
    )

    with pytest.raises(DaemonError) as error:
        handler.handle_service_wait(
            command=compile_service_wait_command(
                WaitCommandPayload.model_validate(
                    {
                        "kind": "wait",
                        "predicate": {
                            "kind": "screen-change",
                            "sourceScreenId": source_screen_id,
                        },
                        "timeoutMs": 500,
                    }
                )
            )
        )

    assert error.value.code == DaemonErrorCode.COMMAND_CANCELLED
    assert error.value.message == "wait screen-change was canceled"
    assert error.value.details["kind"] == "wait"
    assert error.value.details["waitKind"] == "screen-change"


def test_app_wait_does_not_capture_source_basis_before_refresh(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    source_compiled = _make_compiled_screen(
        "screen-source",
        fingerprint="source",
        ref="n7",
    )
    _install_screen_state(
        runtime,
        snapshot=_make_snapshot(snapshot_id=1, package_name="com.example.setup"),
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    next_compiled = _make_compiled_screen(
        "screen-next",
        fingerprint="next",
        targets=[],
    )
    now = 0.0
    snapshot_service = StaticSnapshotService(
        _make_snapshot(snapshot_id=2, package_name="com.android.settings")
    )

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del kwargs
        _install_screen_state(
            session,
            snapshot=refreshed_snapshot,
            public_screen=next_compiled.to_public_screen(),
            compiled_screen=next_compiled,
            artifacts=None,
        )

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "app",
                        "packageName": "com.android.settings",
                    },
                    "timeoutMs": 500,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert "sourceScreenId" not in payload
    assert payload["nextScreenId"] == "screen-next"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None


def test_idle_wait_reuses_observe_style_truth_when_matched(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    snapshot = _make_snapshot()
    _set_current_screen(runtime, snapshot)
    now = 0.0

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=StaticSnapshotService(snapshot),
        screen_refresh=RuntimeStateScreenRefresh(),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {"kind": "idle"},
                    "timeoutMs": 800,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["truth"]["executionOutcome"] == "notApplicable"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None
    assert "sourceScreenId" not in payload


def test_timeout_without_current_truth_uses_payload_mode_none(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    now = 0.0
    snapshot_service = StaticSnapshotService(_make_snapshot())

    def _clear_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del refreshed_snapshot, kwargs
        session.latest_snapshot = None
        session.current_screen_id = None
        session.screen_state = None

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_clear_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {"kind": "idle"},
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "WAIT_TIMEOUT"


def test_timeout_with_current_truth_keeps_full_screen(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    snapshot = _make_snapshot()
    _set_current_screen(runtime, snapshot)
    now = 0.0
    snapshot_service = StaticSnapshotService(snapshot)

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(
            callback=lambda session, refreshed_snapshot, **kwargs: None
        ),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {"kind": "idle"},
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "full"
    assert payload["nextScreenId"] == runtime.current_screen_id
    assert payload["code"] == "WAIT_TIMEOUT"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None
    assert "sourceScreenId" not in payload


def test_screen_change_wait_handle_uses_shared_wait_runtime_loop(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="source",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    next_snapshot = _make_snapshot(snapshot_id=2, label="Next")
    next_compiled = _make_compiled_screen(
        "screen-repaired",
        source_snapshot_id=next_snapshot.snapshot_id,
        captured_at=next_snapshot.captured_at,
        fingerprint="repaired",
    )
    now = 0.0
    snapshot_service = StaticSnapshotService(next_snapshot)

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del kwargs
        _install_screen_state(
            session,
            snapshot=refreshed_snapshot,
            public_screen=next_compiled.to_public_screen(),
            compiled_screen=next_compiled,
            artifacts=None,
        )

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-source",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-repaired"
    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is True


def test_gone_wait_handle_success_stays_stale_even_on_same_screen(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    now = 0.0
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_entry_compiled_screen(
        "screen-source",
        snapshot=source_snapshot,
        fingerprint="source",
        ref="n7",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    next_compiled = _make_compiled_screen(
        "screen-source",
        fingerprint="source",
        ref="n9",
    )
    snapshot_service = StaticSnapshotService(
        _make_snapshot(snapshot_id=2, label="Gone")
    )

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del kwargs
        _install_screen_state(
            session,
            snapshot=refreshed_snapshot,
            public_screen=next_compiled.to_public_screen(),
            compiled_screen=next_compiled,
            artifacts=None,
        )

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "gone",
                        "sourceScreenId": "screen-source",
                        "ref": "n7",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-source"
    assert payload["truth"]["continuityStatus"] == "stale"
    assert payload["truth"]["changed"] is True


def test_screen_change_wait_handle_timeout_stays_failure(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="source",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    now = 0.0

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=StaticSnapshotService(
            _make_snapshot(snapshot_id=2, label="Source")
        ),
        screen_refresh=CallbackScreenRefresh(
            callback=lambda session, refreshed_snapshot, **kwargs: None
        ),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-source",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["code"] == "WAIT_TIMEOUT"
    assert payload["truth"]["continuityStatus"] == "stable"


@pytest.mark.parametrize(
    "basis_case,predicate",
    [
        (
            "missing",
            {"kind": "screen-change", "sourceScreenId": "screen-source"},
        ),
        (
            "incomplete",
            {"kind": "screen-change", "sourceScreenId": "screen-source"},
        ),
        (
            "mismatched",
            {"kind": "screen-change", "sourceScreenId": "screen-source"},
        ),
        (
            "stale",
            {"kind": "screen-change", "sourceScreenId": "screen-source"},
        ),
        (
            "not-ready",
            {"kind": "screen-change", "sourceScreenId": "screen-source"},
        ),
        (
            "missing",
            {"kind": "gone", "sourceScreenId": "screen-source", "ref": "n7"},
        ),
        (
            "incomplete",
            {"kind": "gone", "sourceScreenId": "screen-source", "ref": "n7"},
        ),
        (
            "mismatched",
            {"kind": "gone", "sourceScreenId": "screen-source", "ref": "n7"},
        ),
        (
            "stale",
            {"kind": "gone", "sourceScreenId": "screen-source", "ref": "n7"},
        ),
        (
            "not-ready",
            {"kind": "gone", "sourceScreenId": "screen-source", "ref": "n7"},
        ),
    ],
)
def test_relative_wait_entry_basis_failures_fail_closed(
    tmp_path: Path,
    basis_case: str,
    predicate: dict[str, object],
) -> None:
    runtime = _make_runtime(tmp_path)

    if basis_case == "incomplete":
        source_public_screen = _make_compiled_screen(
            "screen-source",
            fingerprint="source",
            ref="n7",
        ).to_public_screen()
        _install_screen_state(
            runtime,
            snapshot=_make_snapshot(snapshot_id=1, label="Source"),
            public_screen=source_public_screen,
            compiled_screen=None,
            artifacts=None,
        )
    elif basis_case == "mismatched":
        other_snapshot = _make_snapshot(snapshot_id=1, label="Other")
        other_compiled = _make_entry_compiled_screen(
            "screen-other",
            snapshot=other_snapshot,
            fingerprint="other",
            ref="n7",
        )
        _install_screen_state(
            runtime,
            snapshot=other_snapshot,
            public_screen=other_compiled.to_public_screen(),
            compiled_screen=other_compiled,
            artifacts=None,
        )
    elif basis_case == "stale":
        stale_snapshot = _make_snapshot(snapshot_id=1, label="Source")
        stale_compiled = _make_compiled_screen(
            "screen-source",
            source_snapshot_id=999,
            captured_at=stale_snapshot.captured_at,
            fingerprint="source",
            ref="n7",
        )
        _install_screen_state(
            runtime,
            snapshot=stale_snapshot,
            public_screen=stale_compiled.to_public_screen(),
            compiled_screen=stale_compiled,
            artifacts=None,
        )
    elif basis_case == "not-ready":
        source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
        source_compiled = _make_entry_compiled_screen(
            "screen-source",
            snapshot=source_snapshot,
            fingerprint="source",
            ref="n7",
        )
        _install_screen_state(
            runtime,
            snapshot=source_snapshot,
            public_screen=source_compiled.to_public_screen(),
            compiled_screen=source_compiled,
            artifacts=None,
        )
        runtime.status = RuntimeStatus.CONNECTED

    wait_runtime_loop = _FailIfRunWaitLoop()
    handler = WaitCommandHandler(
        runtime_kernel=_RuntimeKernel(runtime),
        wait_runtime_loop=wait_runtime_loop,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": predicate,
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert wait_runtime_loop.run_calls == 0
    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"]["observationQuality"] == "none"
    assert "changed" not in payload["truth"]
    assert payload["sourceScreenId"] == predicate["sourceScreenId"]


def test_gone_wait_entry_ref_absent_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_entry_compiled_screen(
        "screen-source",
        snapshot=source_snapshot,
        fingerprint="source",
        ref="n9",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    wait_runtime_loop = _FailIfRunWaitLoop()
    handler = WaitCommandHandler(
        runtime_kernel=_RuntimeKernel(runtime),
        wait_runtime_loop=wait_runtime_loop,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "gone",
                        "sourceScreenId": "screen-source",
                        "ref": "n7",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert wait_runtime_loop.run_calls == 0
    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"]["observationQuality"] == "none"
    assert "changed" not in payload["truth"]


def test_screen_change_wait_succeeds_without_compiled_truth(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.status = RuntimeStatus.READY
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_compiled_screen(
        "screen-source",
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        fingerprint="source",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    now = 0.0
    next_public_screen = _make_compiled_screen(
        "screen-next",
        fingerprint="next",
    ).to_public_screen()
    snapshot_service = StaticSnapshotService(
        _make_snapshot(snapshot_id=2, label="Next")
    )

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del kwargs
        _install_screen_state(
            session,
            snapshot=refreshed_snapshot,
            public_screen=next_public_screen,
            compiled_screen=None,
            artifacts=None,
        )

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "screen-change",
                        "sourceScreenId": "screen-source",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-next"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"]["changed"] is True


def test_gone_wait_succeeds_without_compiled_truth(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    source_snapshot = _make_snapshot(snapshot_id=1, label="Source")
    source_compiled = _make_entry_compiled_screen(
        "screen-source",
        snapshot=source_snapshot,
        fingerprint="source",
        ref="n7",
    )
    _install_screen_state(
        runtime,
        snapshot=source_snapshot,
        public_screen=source_compiled.to_public_screen(),
        compiled_screen=source_compiled,
        artifacts=None,
    )
    now = 0.0
    next_public_screen = _make_compiled_screen(
        "screen-source",
        fingerprint="source",
        ref="n9",
    ).to_public_screen()
    snapshot_service = StaticSnapshotService(
        _make_snapshot(snapshot_id=2, label="Gone")
    )

    def _refresh_runtime(
        session: Any,
        refreshed_snapshot: RawSnapshot,
        **kwargs: Any,
    ) -> None:
        del kwargs
        _install_screen_state(
            session,
            snapshot=refreshed_snapshot,
            public_screen=next_public_screen,
            compiled_screen=None,
            artifacts=None,
        )

    def _advance_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    handler = _make_wait_handler(
        runtime=runtime,
        snapshot_service=snapshot_service,
        screen_refresh=CallbackScreenRefresh(callback=_refresh_runtime),
        sleep_fn=_advance_sleep,
        time_fn=lambda: now,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {
                        "kind": "gone",
                        "sourceScreenId": "screen-source",
                        "ref": "n7",
                    },
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is True
    assert payload["nextScreenId"] == "screen-source"
    assert payload["truth"]["continuityStatus"] == "stale"
    assert payload["truth"]["changed"] is True


def test_service_wait_maps_runtime_disconnect_to_device_unavailable(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.connection = None
    wait_runtime_loop = _FailIfRunWaitLoop()

    handler = WaitCommandHandler(
        runtime_kernel=_RuntimeKernel(runtime),
        wait_runtime_loop=wait_runtime_loop,
    )

    payload = handler.handle_service_wait(
        command=compile_service_wait_command(
            WaitCommandPayload.model_validate(
                {
                    "kind": "wait",
                    "predicate": {"kind": "idle"},
                    "timeoutMs": 100,
                }
            )
        )
    )

    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert payload["code"] == "DEVICE_UNAVAILABLE"
    assert wait_runtime_loop.run_calls == 0
