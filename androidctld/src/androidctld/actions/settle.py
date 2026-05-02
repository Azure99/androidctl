"""Action settle loop for post-mutation stabilization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from androidctld.device.interfaces import EventPollingClient
from androidctld.errors import DaemonError
from androidctld.observation import (
    ObservationLoop,
    ObservationPolicy,
    ObservationPollOutcome,
)
from androidctld.protocol import CommandKind
from androidctld.runtime import RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime_policy import (
    DEVICE_RPC_REQUEST_ID_SETTLE,
    SETTLE_POLL_SLICE_MS,
    TRANSIENT_INVALID_SNAPSHOT_MAX_RETRIES,
    settle_max_total_ms,
    settle_min_grace_ms,
    settle_snapshot_max_interval_ms,
    settle_stable_window_ms,
)
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import settle_screen_signature
from androidctld.snapshots.service import (
    SnapshotService,
    fetch_with_transient_invalid_snapshot_retry,
)

SleepFn = Callable[[float], None]
TimeFn = Callable[[], float]


@dataclass(frozen=True)
class SettledSnapshot:
    snapshot: RawSnapshot
    timed_out: bool


class ActionSettler:
    def __init__(
        self,
        snapshot_service: SnapshotService,
        semantic_compiler: SemanticCompiler,
        sleep_fn: SleepFn,
        time_fn: TimeFn,
    ) -> None:
        self._snapshot_service = snapshot_service
        self._semantic_compiler = semantic_compiler
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn

    def settle(
        self,
        session: WorkspaceRuntime,
        client: EventPollingClient,
        kind: CommandKind,
        baseline_signature: tuple[object, ...],
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> SettledSnapshot:
        observation = ObservationLoop.begin(
            ObservationPolicy(
                min_grace_ms=settle_min_grace_ms(kind),
                snapshot_max_interval_ms=settle_snapshot_max_interval_ms(kind),
                stable_window_ms=settle_stable_window_ms(kind),
                max_total_ms=settle_max_total_ms(kind),
                poll_slice_ms=SETTLE_POLL_SLICE_MS,
            ),
            started_at=self._time_fn(),
        )
        latest_snapshot: RawSnapshot | None = None
        latest_signature = baseline_signature
        while True:
            now = self._time_fn()
            poll_wait_ms = observation.poll_wait_ms(now)
            poll_outcome = ObservationPollOutcome(
                saw_events=False,
                need_resync=False,
                latest_seq=observation.after_seq,
            )
            if poll_wait_ms > 0:
                polled_at = self._time_fn()
                try:
                    poll_result = client.events_poll(
                        after_seq=observation.after_seq,
                        wait_ms=poll_wait_ms,
                        limit=1,
                        request_id=DEVICE_RPC_REQUEST_ID_SETTLE,
                    )
                    poll_outcome = observation.apply_poll_result(poll_result)
                except DaemonError:
                    self._sleep_fn(poll_wait_ms / 1000.0)
                else:
                    if not poll_outcome.saw_events and self._time_fn() <= polled_at:
                        self._sleep_fn(poll_wait_ms / 1000.0)
            now = self._time_fn()
            if not observation.should_refresh(
                now,
                saw_events=poll_outcome.saw_events,
                need_resync=poll_outcome.need_resync,
            ):
                if observation.timed_out(now) and latest_snapshot is not None:
                    return SettledSnapshot(
                        snapshot=latest_snapshot,
                        timed_out=True,
                    )
                continue

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
            compiled_screen = self._semantic_compiler.compile(
                session.screen_sequence + 1,
                snapshot,
            )
            signature = settle_screen_signature(compiled_screen, snapshot)
            latest_snapshot = snapshot
            is_stable = observation.observe_stability(
                self._time_fn(),
                changed=signature != latest_signature,
            )
            latest_signature = signature
            if is_stable:
                return SettledSnapshot(
                    snapshot=snapshot,
                    timed_out=False,
                )
            if observation.timed_out(self._time_fn()):
                return SettledSnapshot(
                    snapshot=snapshot,
                    timed_out=True,
                )
