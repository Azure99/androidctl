"""Shared observation-loop timing and cursor bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass

from androidctld.device.types import EventsPollResult


@dataclass(frozen=True)
class ObservationPolicy:
    min_grace_ms: int
    snapshot_max_interval_ms: int
    stable_window_ms: int
    max_total_ms: int
    poll_slice_ms: int


@dataclass(frozen=True)
class ObservationPollOutcome:
    saw_events: bool
    need_resync: bool
    latest_seq: int


@dataclass
class ObservationLoop:
    policy: ObservationPolicy
    started_at: float
    after_seq: int = 0
    last_refresh_at: float | None = None
    stable_since: float | None = None

    @classmethod
    def begin(cls, policy: ObservationPolicy, started_at: float) -> ObservationLoop:
        return cls(policy=policy, started_at=started_at)

    @property
    def grace_deadline_at(self) -> float:
        return self.started_at + (self.policy.min_grace_ms / 1000.0)

    @property
    def deadline_at(self) -> float:
        return self.started_at + (self.policy.max_total_ms / 1000.0)

    def timed_out(self, now: float) -> bool:
        return now >= self.deadline_at

    def remaining_ms(self, now: float) -> int:
        return max(int((self.deadline_at - now) * 1000), 0)

    def poll_wait_ms(self, now: float) -> int:
        return min(self.policy.poll_slice_ms, self.remaining_ms(now))

    def grace_elapsed(self, now: float) -> bool:
        return now >= self.grace_deadline_at

    def apply_poll_result(self, result: EventsPollResult) -> ObservationPollOutcome:
        saw_events = bool(result.events)
        need_resync = bool(result.need_resync)
        latest_seq = int(result.latest_seq)
        if need_resync:
            self.after_seq = 0
            self.stable_since = None
            self.last_refresh_at = None
        else:
            self.after_seq = latest_seq
        return ObservationPollOutcome(
            saw_events=saw_events,
            need_resync=need_resync,
            latest_seq=latest_seq,
        )

    def should_refresh(
        self, now: float, *, saw_events: bool, need_resync: bool
    ) -> bool:
        if need_resync:
            return True
        if self.last_refresh_at is None:
            return True
        if saw_events:
            return True
        return (
            now - self.last_refresh_at
        ) * 1000 >= self.policy.snapshot_max_interval_ms

    def mark_refreshed(self, now: float) -> None:
        self.last_refresh_at = now

    def observe_stability(self, now: float, *, changed: bool) -> bool:
        if changed:
            self.stable_since = None
            return False
        if self.stable_since is None:
            self.stable_since = now
        if not self.grace_elapsed(now):
            return False
        return (now - self.stable_since) * 1000 >= self.policy.stable_window_ms
