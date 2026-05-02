from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Generic, TypeVar

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.device.types import EventsPollResult
from androidctld.runtime.models import ScreenState

RuntimeT = TypeVar("RuntimeT")
_ECHO_REFRESHED_SNAPSHOT = object()
_USE_RUNTIME_RESULT = object()


def _refresh_call(
    runtime: object,
    refreshed_snapshot: object,
    **kwargs: object,
) -> dict[str, object]:
    return {
        "runtime": runtime,
        "refreshed_snapshot": refreshed_snapshot,
        "kwargs": kwargs,
    }


def _runtime_state_result(
    runtime: object,
) -> tuple[object, object, object]:
    screen_state = runtime.screen_state
    return (
        runtime.latest_snapshot,
        None if screen_state is None else screen_state.public_screen,
        None if screen_state is None else screen_state.artifacts,
    )


class _RefreshCallRecorder:
    refresh_calls: list[dict[str, object]]

    def _record_refresh_call(
        self,
        runtime: object,
        refreshed_snapshot: object,
        **kwargs: object,
    ) -> None:
        self.refresh_calls.append(_refresh_call(runtime, refreshed_snapshot, **kwargs))


class AlwaysCurrentLifecycleLease:
    def is_current(self, runtime: object) -> bool:
        del runtime
        return True


class PassiveRuntimeKernel(Generic[RuntimeT]):
    def __init__(
        self,
        runtime: RuntimeT,
        *,
        lifecycle_lease_factory: Callable[[RuntimeT], object] | None = None,
    ) -> None:
        self._runtime = runtime
        self._lifecycle_lease_factory = lifecycle_lease_factory or (
            lambda runtime: AlwaysCurrentLifecycleLease()
        )

    def ensure_runtime(self) -> RuntimeT:
        return self._runtime

    def capture_lifecycle_lease(self, runtime: RuntimeT) -> object:
        return self._lifecycle_lease_factory(runtime)

    def acquire_progress_lane(self, runtime: RuntimeT, occupant_kind: str) -> None:
        del runtime, occupant_kind

    def acquire_query_lane(self, runtime: RuntimeT) -> None:
        del runtime

    def release_progress_lane(self, runtime: RuntimeT) -> None:
        del runtime

    def normalize_stale_ready_runtime(self, runtime: RuntimeT) -> None:
        del runtime

    def attach_screenshot_artifact(
        self,
        runtime: RuntimeT,
        lease: object,
        *,
        screenshot_png: str,
    ) -> object | None:
        with runtime.lock:
            if not lease.is_current(runtime):
                return None
            screen_state = runtime.screen_state
            artifacts = (
                ScreenArtifacts(screen_json=None)
                if screen_state is None or screen_state.artifacts is None
                else screen_state.artifacts
            ).with_screenshot(screenshot_png)
            if screen_state is None:
                runtime.screen_state = ScreenState(
                    public_screen=None,
                    compiled_screen=None,
                    artifacts=artifacts,
                )
                current_screen = None
            else:
                screen_state.artifacts = artifacts
                current_screen = screen_state.public_screen
            return SimpleNamespace(current_screen=current_screen, artifacts=artifacts)


@dataclass
class StaticSnapshotService:
    snapshot: object
    fetch_calls: list[tuple[object, bool, object | None]] = field(default_factory=list)

    def fetch(
        self,
        runtime: object,
        force_refresh: bool = False,
        *,
        lifecycle_lease: object | None = None,
    ) -> object:
        self.fetch_calls.append((runtime, force_refresh, lifecycle_lease))
        return self.snapshot


@dataclass
class RuntimeStateScreenRefresh(_RefreshCallRecorder):
    refresh_calls: list[dict[str, object]] = field(default_factory=list)

    def refresh(
        self,
        runtime: object,
        refreshed_snapshot: object,
        **kwargs: object,
    ) -> tuple[object, object, object]:
        self._record_refresh_call(runtime, refreshed_snapshot, **kwargs)
        return _runtime_state_result(runtime)


@dataclass
class StaticScreenRefresh(_RefreshCallRecorder):
    snapshot: object = _ECHO_REFRESHED_SNAPSHOT
    public_screen: object = None
    artifacts: object = None
    refresh_calls: list[dict[str, object]] = field(default_factory=list)

    def refresh(
        self,
        runtime: object,
        refreshed_snapshot: object,
        **kwargs: object,
    ) -> tuple[object, object, object]:
        self._record_refresh_call(runtime, refreshed_snapshot, **kwargs)
        snapshot = (
            refreshed_snapshot
            if self.snapshot is _ECHO_REFRESHED_SNAPSHOT
            else self.snapshot
        )
        return snapshot, self.public_screen, self.artifacts


@dataclass
class CallbackScreenRefresh(_RefreshCallRecorder):
    callback: Callable[..., object | None]
    result: object = _USE_RUNTIME_RESULT
    refresh_calls: list[dict[str, object]] = field(default_factory=list)

    def refresh(
        self,
        runtime: object,
        refreshed_snapshot: object,
        **kwargs: object,
    ) -> tuple[object, object, object]:
        self._record_refresh_call(runtime, refreshed_snapshot, **kwargs)
        callback_result = self.callback(
            runtime,
            refreshed_snapshot,
            **kwargs,
        )
        if callback_result is not None:
            return callback_result
        if self.result is not _USE_RUNTIME_RESULT:
            return self.result
        return _runtime_state_result(runtime)


@dataclass
class NoPollDeviceClient:
    events: tuple[object, ...] = ()
    latest_seq: int = 0
    need_resync: bool = False
    timed_out: bool = False
    poll_calls: list[dict[str, object]] = field(default_factory=list)

    def events_poll(
        self,
        *,
        after_seq: int,
        wait_ms: int,
        limit: int,
        request_id: str,
    ) -> EventsPollResult:
        self.poll_calls.append(
            {
                "after_seq": after_seq,
                "wait_ms": wait_ms,
                "limit": limit,
                "request_id": request_id,
            }
        )
        return EventsPollResult(
            events=self.events,
            latest_seq=self.latest_seq,
            need_resync=self.need_resync,
            timed_out=self.timed_out,
        )
