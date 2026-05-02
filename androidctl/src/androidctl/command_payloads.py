from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

from androidctl_contracts.daemon_api import (
    DaemonCommandPayload,
    GlobalActionCommandPayload,
    GonePredicatePayload,
    LiveScreenBoundCommandPayload,
    RefActionCommandPayload,
    ScreenChangePredicatePayload,
    ScreenRelativeWaitPredicatePayload,
    ScrollCommandPayload,
    TypeCommandPayload,
    WaitCommandPayload,
    WaitPredicatePayload,
)

ScrollDirection = Literal["up", "down", "left", "right", "backward"]
RefActionKind = Literal["tap", "longTap", "focus", "submit"]
GlobalActionKind = Literal["back", "home", "recents", "notifications"]
LateBoundActionKind = Literal["tap", "longTap", "focus", "submit", "type", "scroll"]
SCROLL_DIRECTIONS: Final = frozenset({"up", "down", "left", "right", "backward"})


def _normalize_optional_cli_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_source_screen_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("source_screen_id must be non-empty when provided")
    return normalized


def _required_source_screen_id(value: str) -> str:
    normalized = _normalize_optional_cli_string(value)
    if normalized is None:
        raise RuntimeError("prepared command is missing source_screen_id")
    return normalized


def _validate_timeout_ms(value: int | None) -> int | None:
    if isinstance(value, bool):
        raise TypeError("timeout_ms must be an int, not bool")
    if value is not None and not isinstance(value, int):
        raise TypeError("timeout_ms must be an int")
    if value is not None and value < 0:
        raise ValueError("timeout_ms must be >= 0")
    return value


@dataclass(frozen=True)
class LateBoundActionCommand:
    kind: LateBoundActionKind
    ref: str
    text: str | None = None
    direction: ScrollDirection | None = None

    def __post_init__(self) -> None:
        if _normalize_optional_cli_string(self.ref) is None:
            raise ValueError("late-bound action command requires ref")
        if self.kind == "type":
            if self.text is None:
                raise ValueError("late-bound type command requires text")
            if self.direction is not None:
                raise ValueError("late-bound type command does not accept direction")
            return
        if self.kind == "scroll":
            if self.direction is None:
                raise ValueError("late-bound scroll command requires direction")
            if self.direction not in SCROLL_DIRECTIONS:
                raise ValueError("late-bound scroll command requires a valid direction")
            if self.text is not None:
                raise ValueError("late-bound scroll command does not accept text")
            return
        if self.text is not None or self.direction is not None:
            raise ValueError(
                "late-bound ref action commands only accept kind and ref fields"
            )

    def bind(self, source_screen_id: str) -> LiveScreenBoundCommandPayload:
        resolved_source_screen_id = _required_source_screen_id(source_screen_id)
        if self.kind == "type":
            assert self.text is not None
            return TypeCommandPayload(
                kind="type",
                ref=self.ref,
                text=self.text,
                source_screen_id=resolved_source_screen_id,
            )
        if self.kind == "scroll":
            assert self.direction is not None
            return ScrollCommandPayload(
                kind="scroll",
                ref=self.ref,
                direction=self.direction,
                source_screen_id=resolved_source_screen_id,
            )
        return RefActionCommandPayload(
            kind=self.kind,
            ref=self.ref,
            source_screen_id=resolved_source_screen_id,
        )


@dataclass(frozen=True)
class LateBoundScreenRelativePredicate:
    kind: Literal["screen-change", "gone"]
    ref: str | None = None

    def __post_init__(self) -> None:
        normalized_ref = _normalize_optional_cli_string(self.ref)
        if self.kind == "gone" and normalized_ref is None:
            raise ValueError("late-bound gone predicate requires a non-empty ref")
        if self.kind == "screen-change" and self.ref is not None:
            raise ValueError("screen-change predicate does not accept ref")

    def bind(self, source_screen_id: str) -> ScreenRelativeWaitPredicatePayload:
        resolved_source_screen_id = _required_source_screen_id(source_screen_id)
        if self.kind == "screen-change":
            return ScreenChangePredicatePayload(
                kind="screen-change",
                source_screen_id=resolved_source_screen_id,
            )
        assert self.ref is not None
        return GonePredicatePayload(
            kind="gone",
            ref=self.ref,
            source_screen_id=resolved_source_screen_id,
        )


CliWaitPredicatePayload: TypeAlias = (
    WaitPredicatePayload | LateBoundScreenRelativePredicate
)


@dataclass(frozen=True)
class LateBoundWaitCommand:
    predicate: LateBoundScreenRelativePredicate
    timeout_ms: int | None = None

    def __post_init__(self) -> None:
        _validate_timeout_ms(self.timeout_ms)

    def bind(self, source_screen_id: str) -> WaitCommandPayload:
        return WaitCommandPayload(
            kind="wait",
            predicate=self.predicate.bind(source_screen_id),
            timeout_ms=self.timeout_ms,
        )


@dataclass(frozen=True)
class LateBoundGlobalActionCommand:
    kind: GlobalActionKind

    def bind(self, source_screen_id: str | None) -> GlobalActionCommandPayload:
        normalized_source_screen_id = (
            None if source_screen_id is None else source_screen_id.strip() or None
        )
        return GlobalActionCommandPayload(
            kind=self.kind,
            source_screen_id=normalized_source_screen_id,
        )


LateBoundCommand: TypeAlias = (
    LateBoundActionCommand | LateBoundGlobalActionCommand | LateBoundWaitCommand
)
CliCommandPayload: TypeAlias = DaemonCommandPayload | LateBoundCommand


def build_ref_action_command(
    *,
    kind: RefActionKind,
    ref: str,
    source_screen_id: str | None,
) -> CliCommandPayload:
    normalized_source_screen_id = _normalize_source_screen_id(source_screen_id)
    if normalized_source_screen_id is None:
        return LateBoundActionCommand(kind=kind, ref=ref)
    return RefActionCommandPayload(
        kind=kind,
        ref=ref,
        source_screen_id=normalized_source_screen_id,
    )


def build_type_command(
    *,
    ref: str,
    text: str,
    source_screen_id: str | None,
) -> CliCommandPayload:
    normalized_source_screen_id = _normalize_source_screen_id(source_screen_id)
    if normalized_source_screen_id is None:
        return LateBoundActionCommand(kind="type", ref=ref, text=text)
    return TypeCommandPayload(
        kind="type",
        ref=ref,
        text=text,
        source_screen_id=normalized_source_screen_id,
    )


def build_scroll_command(
    *,
    ref: str,
    direction: ScrollDirection,
    source_screen_id: str | None,
) -> CliCommandPayload:
    normalized_source_screen_id = _normalize_source_screen_id(source_screen_id)
    if normalized_source_screen_id is None:
        return LateBoundActionCommand(kind="scroll", ref=ref, direction=direction)
    return ScrollCommandPayload(
        kind="scroll",
        ref=ref,
        direction=direction,
        source_screen_id=normalized_source_screen_id,
    )


def build_wait_command(
    *,
    predicate: CliWaitPredicatePayload,
    timeout_ms: int | None,
) -> CliCommandPayload:
    validated_timeout_ms = _validate_timeout_ms(timeout_ms)
    if isinstance(predicate, LateBoundScreenRelativePredicate):
        return LateBoundWaitCommand(
            predicate=predicate,
            timeout_ms=validated_timeout_ms,
        )
    return WaitCommandPayload(
        kind="wait",
        predicate=predicate,
        timeout_ms=validated_timeout_ms,
    )


def build_global_action_command(
    *,
    kind: GlobalActionKind,
    source_screen_id: str | None,
) -> CliCommandPayload:
    normalized_source_screen_id = _normalize_source_screen_id(source_screen_id)
    if normalized_source_screen_id is None:
        return LateBoundGlobalActionCommand(kind=kind)
    return GlobalActionCommandPayload(
        kind=kind,
        source_screen_id=normalized_source_screen_id,
    )
