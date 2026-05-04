from __future__ import annotations

from androidctl.command_payloads import (
    CliWaitPredicatePayload,
    LateBoundScreenRelativePredicate,
)
from androidctl.parsing.refs import parse_ref
from androidctl.parsing.screen_id import parse_screen_id_override
from androidctl_contracts.daemon_api import (
    AppPredicatePayload,
    GonePredicatePayload,
    IdlePredicatePayload,
    ScreenChangePredicatePayload,
    TextPresentPredicatePayload,
)

_WAIT_PREDICATES = {"app", "gone", "idle", "screen-change", "text-present"}


def parse_wait_predicate(
    until: str,
    *,
    ref: str | None = None,
    text: str | None = None,
    package_name: str | None = None,
    source_screen_id: str | None = None,
) -> CliWaitPredicatePayload:
    normalized_until = until.strip().lower()
    if normalized_until not in _WAIT_PREDICATES:
        allowed = ", ".join(sorted(_WAIT_PREDICATES))
        raise ValueError(f"wait predicate must be one of: {allowed}")

    if normalized_until == "screen-change":
        normalized_source_screen_id = parse_screen_id_override(source_screen_id)
        if normalized_source_screen_id is None:
            return LateBoundScreenRelativePredicate(kind="screen-change")
        return ScreenChangePredicatePayload(
            kind="screen-change",
            source_screen_id=normalized_source_screen_id,
        )

    if normalized_until == "gone":
        if ref is None or not ref.strip():
            raise ValueError("gone wait requires --ref")
        normalized_source_screen_id = parse_screen_id_override(source_screen_id)
        if normalized_source_screen_id is None:
            return LateBoundScreenRelativePredicate(
                kind="gone",
                ref=parse_ref(ref),
            )
        return GonePredicatePayload(
            kind="gone",
            ref=parse_ref(ref),
            source_screen_id=normalized_source_screen_id,
        )

    if normalized_until == "text-present":
        if text is None or not text.strip():
            raise ValueError("text-present wait requires --text")
        return TextPresentPredicatePayload(kind="text-present", text=text)

    if normalized_until == "app":
        if package_name is None or not package_name.strip():
            raise ValueError("app wait requires --app")
        return AppPredicatePayload(
            kind="app",
            package_name=package_name.strip(),
        )

    return IdlePredicatePayload(kind="idle")
