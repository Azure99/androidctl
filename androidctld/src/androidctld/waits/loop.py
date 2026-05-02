"""Shared wait-loop orchestration for canonical wait commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from androidctld.commands.command_models import WaitCommand, wait_timeout_ms
from androidctld.commands.models import CommandRecord
from androidctld.device.interfaces import (
    DeviceClientFactory,
    EventPollingClient,
)
from androidctld.observation import (
    ObservationLoop,
    ObservationPolicy,
    ObservationPollOutcome,
)
from androidctld.protocol import CommandKind
from androidctld.runtime import RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_compiled_screen,
    current_public_screen,
)
from androidctld.runtime_policy import (
    DEVICE_RPC_REQUEST_ID_WAIT,
    TRANSIENT_INVALID_SNAPSHOT_MAX_RETRIES,
    WAIT_EVENT_POLL_SLICE_MS,
    WAIT_IDLE_STABLE_WINDOW_MS,
    WAIT_LOOP_SLEEP_SECONDS,
    WAIT_SNAPSHOT_MAX_INTERVAL_MS,
    default_wait_timeout_ms,
)
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import ScreenRefreshService, raise_if_stale
from androidctld.snapshots.service import (
    SnapshotService,
    fetch_with_transient_invalid_snapshot_retry,
)
from androidctld.waits.evaluators import (
    WaitEvaluationOutcome,
    WaitIdleEvaluationState,
    WaitIdleTracking,
    WaitMatchData,
    WaitMatched,
    WaitReadyContext,
    evaluate_ready_wait_match,
)

SleepFn = Callable[[float], None]
TimeFn = Callable[[], float]
WaitReadyState: TypeAlias = WaitReadyContext


@dataclass(frozen=True)
class WaitLoopTimedOut:
    pass


WaitLoopOutcome: TypeAlias = WaitMatchData | WaitLoopTimedOut


def _wait_state(
    session: WorkspaceRuntime,
) -> tuple[
    RawSnapshot | None,
    PublicScreen | None,
    CompiledScreen | None,
]:
    return (
        session.latest_snapshot,
        current_public_screen(session),
        current_compiled_screen(session),
    )


def _ready_wait_state(
    snapshot: RawSnapshot | None,
    public_screen: PublicScreen | None,
    compiled_screen: CompiledScreen | None,
) -> WaitReadyState | None:
    if snapshot is None or public_screen is None:
        return None
    return WaitReadyContext(
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=compiled_screen,
    )


def _cached_ready_wait_state(
    session: WorkspaceRuntime,
    *,
    lifecycle_lease: RuntimeLifecycleLease,
    command: WaitCommand,
    kind: CommandKind,
    record: CommandRecord,
) -> WaitReadyState | None:
    with session.lock:
        ready_state = _ready_wait_state(*_wait_state(session))
        if ready_state is None:
            return None
        raise_if_stale(
            session,
            lifecycle_lease,
            kind=kind,
            wait_kind=command.wait_kind,
            record=record,
        )
        return ready_state


class WaitRuntimeLoop:
    def __init__(
        self,
        *,
        snapshot_service: SnapshotService,
        screen_refresh: ScreenRefreshService,
        device_client_factory: DeviceClientFactory,
        sleep_fn: SleepFn,
        time_fn: TimeFn,
    ) -> None:
        self._snapshot_service = snapshot_service
        self._screen_refresh = screen_refresh
        self._device_client_factory = device_client_factory
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn

    def run(
        self,
        *,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: WaitCommand,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> WaitLoopOutcome:
        kind = command.kind
        timeout_override = wait_timeout_ms(command)
        timeout_ms = (
            timeout_override
            if timeout_override is not None
            else default_wait_timeout_ms(command.wait_kind)
        )
        observation = ObservationLoop.begin(
            ObservationPolicy(
                min_grace_ms=0,
                snapshot_max_interval_ms=WAIT_SNAPSHOT_MAX_INTERVAL_MS,
                stable_window_ms=WAIT_IDLE_STABLE_WINDOW_MS,
                max_total_ms=timeout_ms,
                poll_slice_ms=WAIT_EVENT_POLL_SLICE_MS,
            ),
            started_at=self._time_fn(),
        )
        idle_state = WaitIdleEvaluationState()

        initial_state = _cached_ready_wait_state(
            session,
            lifecycle_lease=lifecycle_lease,
            command=command,
            kind=kind,
            record=record,
        )
        if initial_state is not None:
            outcome = self._evaluate_ready_state(
                command=command,
                ready_state=initial_state,
                idle_state=idle_state,
                now=observation.started_at,
            )
            if isinstance(outcome, WaitMatched):
                return outcome.match
            if isinstance(outcome, WaitIdleTracking):
                idle_state = outcome.idle_state

        client = self._device_client_factory(
            session,
            lifecycle_lease=lifecycle_lease,
        )
        while True:
            poll_outcome = self._poll(observation, client)

            current_state = self._current_ready_state(
                session=session,
                observation=observation,
                poll_outcome=poll_outcome,
                lifecycle_lease=lifecycle_lease,
                command=command,
                kind=kind,
                record=record,
            )
            if current_state is None:
                if observation.timed_out(self._time_fn()):
                    return WaitLoopTimedOut()
                self._sleep_fn(WAIT_LOOP_SLEEP_SECONDS)
                continue

            outcome = self._evaluate_ready_state(
                command=command,
                ready_state=current_state,
                idle_state=idle_state,
                now=self._time_fn(),
            )
            if isinstance(outcome, WaitMatched):
                return outcome.match
            if isinstance(outcome, WaitIdleTracking):
                idle_state = outcome.idle_state
            if observation.timed_out(self._time_fn()):
                return WaitLoopTimedOut()
            self._sleep_fn(WAIT_LOOP_SLEEP_SECONDS)

    def _poll(
        self,
        observation: ObservationLoop,
        client: EventPollingClient,
    ) -> ObservationPollOutcome:
        poll_wait_ms = observation.poll_wait_ms(self._time_fn())
        poll_outcome = ObservationPollOutcome(
            saw_events=False,
            need_resync=False,
            latest_seq=observation.after_seq,
        )
        if poll_wait_ms > 0:
            polled_at = self._time_fn()
            poll_result = client.events_poll(
                after_seq=observation.after_seq,
                wait_ms=poll_wait_ms,
                limit=1,
                request_id=DEVICE_RPC_REQUEST_ID_WAIT,
            )
            poll_outcome = observation.apply_poll_result(poll_result)
            if not poll_outcome.saw_events and self._time_fn() <= polled_at:
                self._sleep_fn(poll_wait_ms / 1000.0)
        return poll_outcome

    def _current_ready_state(
        self,
        *,
        session: WorkspaceRuntime,
        observation: ObservationLoop,
        poll_outcome: ObservationPollOutcome,
        lifecycle_lease: RuntimeLifecycleLease,
        command: WaitCommand,
        kind: CommandKind,
        record: CommandRecord,
    ) -> WaitReadyState | None:
        if observation.should_refresh(
            self._time_fn(),
            saw_events=poll_outcome.saw_events,
            need_resync=poll_outcome.need_resync,
        ):
            snapshot = fetch_with_transient_invalid_snapshot_retry(
                self._snapshot_service,
                session=session,
                force_refresh=True,
                lifecycle_lease=lifecycle_lease,
                deadline_at=observation.deadline_at,
                max_retries=TRANSIENT_INVALID_SNAPSHOT_MAX_RETRIES,
                sleep_fn=self._sleep_fn,
                time_fn=self._time_fn,
            )
            observation.mark_refreshed(self._time_fn())
            self._screen_refresh.refresh(
                session,
                snapshot,
                lifecycle_lease=lifecycle_lease,
                command_kind=kind,
                wait_kind=command.wait_kind,
                record=record,
            )
        return _cached_ready_wait_state(
            session,
            lifecycle_lease=lifecycle_lease,
            command=command,
            kind=kind,
            record=record,
        )

    def _evaluate_ready_state(
        self,
        *,
        command: WaitCommand,
        ready_state: WaitReadyState,
        idle_state: WaitIdleEvaluationState,
        now: float,
    ) -> WaitEvaluationOutcome:
        return evaluate_ready_wait_match(
            command=command,
            ready=ready_state,
            idle_state=idle_state,
            now=now,
        )


__all__ = [
    "SleepFn",
    "TimeFn",
    "WaitLoopOutcome",
    "WaitLoopTimedOut",
    "WaitReadyState",
    "WaitRuntimeLoop",
]
