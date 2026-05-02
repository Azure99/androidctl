"""Fresh-current evidence checks for post-dispatch global actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.semantics.compiler import CompiledScreen, SemanticNode
from androidctld.snapshots.models import RawSnapshot
from androidctld.text_equivalence import canonical_text_key, searchable_raw_node_texts

_SYSTEMUI_PACKAGE_PREFIX = "com.android.systemui"
_SYSTEM_EVIDENCE_REQUIRED_ACTIONS = frozenset({"recents", "notifications"})
_APP_SURFACE_GROUPS = ("targets", "context", "dialog", "keyboard")


@dataclass(frozen=True)
class GlobalFreshCurrentBaseline:
    action: str
    snapshot_identity: tuple[int, str] | None
    app_signature: tuple[Any, ...] | None


def capture_global_fresh_current_baseline(
    *,
    action: str,
    snapshot: RawSnapshot | None,
    compiled_screen: CompiledScreen | None,
) -> GlobalFreshCurrentBaseline:
    return GlobalFreshCurrentBaseline(
        action=action,
        snapshot_identity=None if snapshot is None else _snapshot_identity(snapshot),
        app_signature=(
            None
            if snapshot is None
            else _fresh_current_app_signature(compiled_screen, snapshot)
        ),
    )


def validate_global_fresh_current_evidence(
    baseline: GlobalFreshCurrentBaseline,
    *,
    snapshot: RawSnapshot,
    compiled_screen: CompiledScreen,
) -> None:
    if baseline.snapshot_identity is None:
        return
    if baseline.snapshot_identity == _snapshot_identity(snapshot):
        raise _fresh_current_error(
            baseline.action,
            reason="post_action_snapshot_identity_unchanged",
        )
    if baseline.action not in _SYSTEM_EVIDENCE_REQUIRED_ACTIONS:
        return
    candidate_signature = _fresh_current_app_signature(compiled_screen, snapshot)
    if (
        baseline.app_signature is not None
        and candidate_signature != baseline.app_signature
    ):
        return
    if _has_real_systemui_entry(snapshot, compiled_screen):
        return
    raise _fresh_current_error(
        baseline.action,
        reason="post_action_system_evidence_missing",
    )


def _snapshot_identity(snapshot: RawSnapshot) -> tuple[int, str]:
    return snapshot.snapshot_id, snapshot.captured_at


def _fresh_current_app_signature(
    compiled_screen: CompiledScreen | None,
    snapshot: RawSnapshot,
) -> tuple[Any, ...]:
    if compiled_screen is None:
        return _raw_app_surface_signature(snapshot)

    raw_actions_by_rid = {
        node.rid: tuple(node.actions)
        for node in snapshot.nodes
        if not _is_systemui_package(node.package_name)
    }
    return (
        compiled_screen.package_name,
        compiled_screen.activity_name,
        compiled_screen.keyboard_visible,
        tuple(
            _compiled_app_node_signature(
                group_name,
                node,
                raw_actions=raw_actions_by_rid.get(node.raw_rid),
            )
            for group_name in _APP_SURFACE_GROUPS
            for node in getattr(compiled_screen, group_name)
        ),
    )


def _compiled_app_node_signature(
    group_name: str,
    node: SemanticNode,
    *,
    raw_actions: tuple[str, ...] | None,
) -> tuple[Any, ...]:
    return (
        group_name,
        node.role,
        canonical_text_key(node.label),
        tuple(canonical_text_key(value) for value in node.state),
        tuple(node.actions) if raw_actions is None else raw_actions,
        None if node.bounds is None else tuple(node.bounds),
    )


def _raw_app_surface_signature(snapshot: RawSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.package_name,
        snapshot.activity_name,
        snapshot.ime.visible,
        tuple(
            (
                node.class_name,
                canonical_text_key(node.resource_id),
                tuple(
                    canonical_text_key(value)
                    for value in searchable_raw_node_texts(node)
                ),
                (
                    node.enabled,
                    node.editable,
                    node.focusable,
                    node.focused,
                    node.checkable,
                    node.checked,
                    node.selected,
                    node.scrollable,
                ),
                tuple(node.actions),
                tuple(node.bounds),
            )
            for node in snapshot.nodes
            if node.visible_to_user and not _is_systemui_package(node.package_name)
        ),
    )


def _has_real_systemui_entry(
    snapshot: RawSnapshot,
    compiled_screen: CompiledScreen,
) -> bool:
    if _is_systemui_package(compiled_screen.package_name):
        return True
    return _is_systemui_package(snapshot.package_name)


def _is_systemui_package(package_name: str | None) -> bool:
    return isinstance(package_name, str) and package_name.startswith(
        _SYSTEMUI_PACKAGE_PREFIX
    )


def _fresh_current_error(action: str, *, reason: str) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.SCREEN_NOT_READY,
        message="No fresh current screen observation is available after global action.",
        retryable=True,
        details={
            "reason": reason,
            "globalAction": action,
        },
        http_status=200,
    )
