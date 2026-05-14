from __future__ import annotations

import json
from pathlib import Path

from androidctld.commands.command_models import ObserveCommand
from androidctld.commands.handlers.observe import ObserveCommandHandler
from androidctld.device.types import DeviceEvent, EventsPollResult
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.observation import ObservationLoop, ObservationPolicy
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import ScreenState
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.models import parse_raw_snapshot

from .support.doubles import PassiveRuntimeKernel, StaticSnapshotService
from .support.runtime import build_connected_runtime
from .support.semantic_screen import install_snapshot_screen, make_snapshot


def test_apply_poll_result_advances_cursor() -> None:
    loop = ObservationLoop.begin(
        ObservationPolicy(
            min_grace_ms=0,
            snapshot_max_interval_ms=250,
            stable_window_ms=300,
            max_total_ms=1200,
            poll_slice_ms=100,
        ),
        started_at=10.0,
    )
    outcome = loop.apply_poll_result(
        EventsPollResult(
            events=(
                DeviceEvent(
                    seq=3,
                    type="window.changed",
                    timestamp="2026-03-18T00:00:00Z",
                    data={},
                ),
            ),
            latest_seq=3,
            need_resync=False,
            timed_out=False,
        )
    )

    assert outcome.saw_events is True
    assert outcome.need_resync is False
    assert outcome.latest_seq == 3
    assert loop.after_seq == 3


def test_need_resync_resets_cursor_and_forces_refresh() -> None:
    loop = ObservationLoop.begin(
        ObservationPolicy(
            min_grace_ms=0,
            snapshot_max_interval_ms=250,
            stable_window_ms=300,
            max_total_ms=1200,
            poll_slice_ms=100,
        ),
        started_at=10.0,
    )
    loop.after_seq = 5
    loop.mark_refreshed(10.2)
    outcome = loop.apply_poll_result(
        EventsPollResult(
            events=(),
            latest_seq=7,
            need_resync=True,
            timed_out=False,
        )
    )

    assert outcome.saw_events is False
    assert outcome.need_resync is True
    assert outcome.latest_seq == 7
    assert loop.after_seq == 0
    assert loop.should_refresh(10.3, saw_events=False, need_resync=True) is True


def test_refresh_and_stability_follow_policy_windows() -> None:
    loop = ObservationLoop.begin(
        ObservationPolicy(
            min_grace_ms=200,
            snapshot_max_interval_ms=250,
            stable_window_ms=300,
            max_total_ms=1200,
            poll_slice_ms=100,
        ),
        started_at=10.0,
    )

    assert loop.should_refresh(10.0, saw_events=False, need_resync=False) is True

    loop.mark_refreshed(10.0)
    assert loop.should_refresh(10.1, saw_events=False, need_resync=False) is False
    assert loop.should_refresh(10.3, saw_events=False, need_resync=False) is True

    assert loop.observe_stability(10.0, changed=False) is False
    assert loop.observe_stability(10.2, changed=False) is False
    assert loop.observe_stability(10.5, changed=False) is True
    assert loop.observe_stability(10.6, changed=True) is False


class _CompilerRefreshService:
    def __init__(self) -> None:
        self._compiler = SemanticCompiler()

    def refresh(
        self,
        runtime: object,
        snapshot: object,
        *,
        lifecycle_lease: object | None = None,
        command_kind: object | None = None,
    ) -> tuple[object, object, None]:
        del lifecycle_lease, command_kind
        sequence = runtime.screen_sequence + 1
        compiled = self._compiler.compile(sequence, snapshot)
        public = compiled.to_public_screen()
        runtime.screen_sequence = sequence
        runtime.current_screen_id = public.screen_id
        runtime.screen_state = ScreenState(
            public_screen=public,
            compiled_screen=compiled,
            artifacts=None,
        )
        return snapshot, public, None


def _load_settings_snapshot() -> object:
    fixtures_dir = Path(__file__).resolve().parents[1] / "golden" / "fixtures"
    return parse_raw_snapshot(
        json.loads((fixtures_dir / "settings_snapshot.json").read_text("utf-8"))
    )


def _observe_handler(
    tmp_path: Path, *, with_current_screen: bool
) -> tuple[ObserveCommandHandler, StaticSnapshotService]:
    runtime = build_connected_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = _load_settings_snapshot()
    if with_current_screen:
        install_snapshot_screen(runtime, snapshot, include_artifacts=False)
    snapshot_service = StaticSnapshotService(snapshot)
    return (
        ObserveCommandHandler(
            runtime_kernel=PassiveRuntimeKernel(runtime),
            snapshot_service=snapshot_service,
            screen_refresh=_CompilerRefreshService(),
        ),
        snapshot_service,
    )


def test_bootstrap_observe_omits_source_screen_id(tmp_path: Path) -> None:
    handler, snapshot_service = _observe_handler(tmp_path, with_current_screen=False)

    payload = handler.handle(command=ObserveCommand())

    assert snapshot_service.fetch_calls[0][1] is True
    assert payload.get("sourceScreenId") is None
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None


def test_repeat_observe_same_screen_is_stable_and_unchanged(tmp_path: Path) -> None:
    handler, snapshot_service = _observe_handler(tmp_path, with_current_screen=True)

    payload = handler.handle(command=ObserveCommand())

    assert snapshot_service.fetch_calls[0][1] is True
    assert payload["sourceScreenId"] == payload["nextScreenId"]
    assert payload["truth"]["continuityStatus"] == "stable"
    assert payload["truth"]["changed"] is False


def test_observe_with_authoritative_current_still_fetches_live_snapshot(
    tmp_path: Path,
) -> None:
    runtime = build_connected_runtime(tmp_path, status=RuntimeStatus.READY)
    old_snapshot = make_snapshot(
        snapshot_id=1,
        package_name="com.taobao.taobao",
        activity_name="TaobaoActivity",
        label="Taobao",
    )
    old_compiled = install_snapshot_screen(
        runtime, old_snapshot, include_artifacts=True
    )
    new_snapshot = make_snapshot(
        snapshot_id=2,
        package_name="com.jingdong.app.mall",
        activity_name="MainFrameActivity",
        label="JD",
    )
    snapshot_service = StaticSnapshotService(new_snapshot)
    handler = ObserveCommandHandler(
        runtime_kernel=PassiveRuntimeKernel(runtime),
        snapshot_service=snapshot_service,
        screen_refresh=_CompilerRefreshService(),
    )

    payload = handler.handle(command=ObserveCommand())

    assert snapshot_service.fetch_calls[0][1] is True
    assert payload["sourceScreenId"] == old_compiled.screen_id
    assert payload["screen"]["app"]["packageName"] == "com.jingdong.app.mall"
    assert payload["screen"]["app"]["packageName"] != "com.taobao.taobao"


def test_observe_with_authoritative_current_and_fetch_failure_omits_old_screen(
    tmp_path: Path,
) -> None:
    runtime = build_connected_runtime(tmp_path, status=RuntimeStatus.READY)
    old_snapshot = make_snapshot(
        snapshot_id=1,
        package_name="com.taobao.taobao",
        activity_name="TaobaoActivity",
        label="Taobao",
    )
    install_snapshot_screen(runtime, old_snapshot, include_artifacts=True)
    fetch_calls: list[bool] = []

    class _FailingSnapshotService:
        def fetch(
            self,
            runtime: object,
            force_refresh: bool = False,
            *,
            lifecycle_lease: object | None = None,
        ) -> object:
            del runtime, lifecycle_lease
            fetch_calls.append(force_refresh)
            raise DaemonError(
                code=DaemonErrorCode.DEVICE_RPC_FAILED,
                message="snapshot failed",
                retryable=True,
                details={},
                http_status=200,
            )

    handler = ObserveCommandHandler(
        runtime_kernel=PassiveRuntimeKernel(runtime),
        snapshot_service=_FailingSnapshotService(),
        screen_refresh=_CompilerRefreshService(),
    )

    payload = handler.handle(command=ObserveCommand())

    assert fetch_calls == [True]
    assert payload["ok"] is False
    assert payload["payloadMode"] == "none"
    assert "screen" not in payload
    assert "nextScreenId" not in payload
    assert payload["truth"]["observationQuality"] == "none"
    assert payload["truth"]["continuityStatus"] == "none"
    assert "changed" not in payload["truth"]
