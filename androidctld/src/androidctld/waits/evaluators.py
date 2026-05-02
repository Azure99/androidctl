"""Wait evaluation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from androidctld.app_targets import AppTargetMatch, match_app_target
from androidctld.commands.command_models import (
    AppWaitPredicate,
    GoneWaitPredicate,
    IdleWaitPredicate,
    ScreenChangeWaitPredicate,
    TextWaitPredicate,
    WaitCommand,
)
from androidctld.protocol import CommandKind
from androidctld.runtime_policy import WAIT_IDLE_STABLE_WINDOW_MS
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import (
    PublicNode,
    PublicScreen,
    public_group_nodes,
)
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import compiled_screen_signature
from androidctld.waits.matcher import matches_text


@dataclass(frozen=True, slots=True)
class WaitReadyContext:
    snapshot: RawSnapshot
    public_screen: PublicScreen
    compiled_screen: CompiledScreen | None


@dataclass(frozen=True, slots=True)
class WaitIdleEvaluationState:
    idle_signature: tuple[Any, ...] | None = None
    idle_stable_since: float | None = None


@dataclass(frozen=True, slots=True)
class WaitMatchData:
    snapshot: RawSnapshot
    app_match: AppTargetMatch | None = None


@dataclass(frozen=True, slots=True)
class WaitMatched:
    match: WaitMatchData


@dataclass(frozen=True, slots=True)
class WaitNoMatch:
    pass


@dataclass(frozen=True, slots=True)
class WaitIdleTracking:
    idle_state: WaitIdleEvaluationState


WaitEvaluationOutcome: TypeAlias = WaitMatched | WaitNoMatch | WaitIdleTracking


def _evaluate_text_wait(
    *,
    text: str,
    snapshot: RawSnapshot,
    compiled_screen: CompiledScreen | None,
) -> WaitEvaluationOutcome:
    if compiled_screen is None:
        return WaitNoMatch()
    if matches_text(snapshot, text):
        return WaitMatched(WaitMatchData(snapshot=snapshot))
    return WaitNoMatch()


def _evaluate_screen_change_wait(
    *,
    source_screen_id: str,
    snapshot: RawSnapshot,
    public_screen: PublicScreen,
) -> WaitEvaluationOutcome:
    if public_screen.screen_id != source_screen_id:
        return WaitMatched(WaitMatchData(snapshot=snapshot))
    return WaitNoMatch()


def _screen_contains_ref(screen: PublicScreen, ref: str) -> bool:
    nodes = (
        *public_group_nodes(screen, "targets"),
        *public_group_nodes(screen, "context"),
        *public_group_nodes(screen, "dialog"),
        *public_group_nodes(screen, "keyboard"),
        *public_group_nodes(screen, "system"),
    )
    return any(_node_has_ref(node, ref) for node in nodes)


def _node_has_ref(node: PublicNode, ref: str) -> bool:
    return node.ref == ref


def _evaluate_gone_wait(
    *,
    ref: str,
    snapshot: RawSnapshot,
    public_screen: PublicScreen,
) -> WaitEvaluationOutcome:
    if not _screen_contains_ref(public_screen, ref):
        return WaitMatched(WaitMatchData(snapshot=snapshot))
    return WaitNoMatch()


def _evaluate_app_wait(
    *,
    package_name: str,
    snapshot: RawSnapshot,
) -> WaitEvaluationOutcome:
    app_match = match_app_target(package_name, snapshot.package_name)
    if app_match is not None:
        return WaitMatched(WaitMatchData(snapshot=snapshot, app_match=app_match))
    return WaitNoMatch()


def _evaluate_idle_wait(
    *,
    snapshot: RawSnapshot,
    compiled_screen: CompiledScreen | None,
    idle_state: WaitIdleEvaluationState,
    now: float,
) -> WaitEvaluationOutcome:
    if compiled_screen is None:
        return WaitIdleTracking(WaitIdleEvaluationState())
    current_signature = compiled_screen_signature(compiled_screen, snapshot)
    if current_signature == idle_state.idle_signature:
        stable_since = (
            idle_state.idle_stable_since
            if idle_state.idle_stable_since is not None
            else now
        )
        if (now - stable_since) * 1000 >= WAIT_IDLE_STABLE_WINDOW_MS:
            return WaitMatched(
                WaitMatchData(
                    snapshot=snapshot,
                )
            )
        return WaitIdleTracking(
            WaitIdleEvaluationState(
                idle_signature=current_signature,
                idle_stable_since=stable_since,
            )
        )
    return WaitIdleTracking(
        WaitIdleEvaluationState(
            idle_signature=current_signature,
            idle_stable_since=now,
        )
    )


def evaluate_ready_wait_match(
    *,
    command: WaitCommand,
    ready: WaitReadyContext,
    idle_state: WaitIdleEvaluationState,
    now: float,
) -> WaitEvaluationOutcome:
    if command.kind is not CommandKind.WAIT:
        raise TypeError("wait evaluation kind must be canonical wait")
    predicate = command.predicate
    if isinstance(predicate, TextWaitPredicate):
        return _evaluate_text_wait(
            text=predicate.text,
            snapshot=ready.snapshot,
            compiled_screen=ready.compiled_screen,
        )
    if isinstance(predicate, ScreenChangeWaitPredicate):
        return _evaluate_screen_change_wait(
            source_screen_id=predicate.source_screen_id,
            snapshot=ready.snapshot,
            public_screen=ready.public_screen,
        )
    if isinstance(predicate, GoneWaitPredicate):
        return _evaluate_gone_wait(
            ref=predicate.ref,
            snapshot=ready.snapshot,
            public_screen=ready.public_screen,
        )
    if isinstance(predicate, AppWaitPredicate):
        return _evaluate_app_wait(
            package_name=predicate.package_name,
            snapshot=ready.snapshot,
        )
    if isinstance(predicate, IdleWaitPredicate):
        return _evaluate_idle_wait(
            snapshot=ready.snapshot,
            compiled_screen=ready.compiled_screen,
            idle_state=idle_state,
            now=now,
        )
    raise TypeError(f"unsupported wait evaluator kind: {command.wait_kind.value!r}")


__all__ = [
    "WaitEvaluationOutcome",
    "WaitIdleEvaluationState",
    "WaitIdleTracking",
    "WaitMatchData",
    "WaitMatched",
    "WaitNoMatch",
    "WaitReadyContext",
    "evaluate_ready_wait_match",
]
