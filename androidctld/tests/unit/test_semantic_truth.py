from __future__ import annotations

from pathlib import Path

from androidctld.commands.semantic_truth import (
    capture_runtime_source_basis,
    resolve_open_changed,
    resolve_runtime_continuity,
)
from androidctld.protocol import RuntimeStatus

from .support.runtime import build_runtime, install_screen_state
from .support.semantic_screen import (
    make_compiled_screen,
    make_public_screen,
    make_snapshot,
)


def test_capture_runtime_source_basis_uses_authoritative_current_basis(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = make_snapshot(snapshot_id=3)
    compiled_screen = make_compiled_screen(
        "screen-current",
        sequence=4,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_public_screen("screen-current"),
        compiled_screen=compiled_screen,
        artifacts=None,
    )

    source_basis = capture_runtime_source_basis(runtime=runtime)

    assert source_basis.source_screen_id == "screen-current"
    assert source_basis.source_compiled_screen == compiled_screen
    assert source_basis.source_compiled_screen is not compiled_screen


def test_capture_runtime_source_basis_rejects_mixed_generation_truth(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = make_snapshot(snapshot_id=3)
    compiled_screen = make_compiled_screen(
        "screen-current",
        sequence=4,
        source_snapshot_id=2,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_public_screen("screen-current"),
        compiled_screen=compiled_screen,
        artifacts=None,
    )

    source_basis = capture_runtime_source_basis(runtime=runtime)

    assert source_basis.source_screen_id is None
    assert source_basis.source_compiled_screen is None


def test_resolve_runtime_continuity_does_not_mix_non_authoritative_current_screen(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = make_snapshot(snapshot_id=3)
    source_compiled_screen = make_compiled_screen(
        "screen-current",
        sequence=4,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_public_screen("screen-current"),
        compiled_screen=source_compiled_screen,
        artifacts=None,
    )
    runtime.current_screen_id = "screen-other"

    continuity = resolve_runtime_continuity(
        runtime=runtime,
        source_screen_id="screen-current",
        source_compiled_screen=source_compiled_screen,
    )

    assert continuity.continuity_status == "none"
    assert continuity.changed is None


def test_resolve_open_changed_returns_false_for_same_action_surface(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = make_snapshot(snapshot_id=3)
    source_compiled_screen = make_compiled_screen(
        "screen-current",
        sequence=4,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=make_public_screen("screen-current"),
        compiled_screen=source_compiled_screen,
        artifacts=None,
    )

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id="screen-current",
        source_compiled_screen=source_compiled_screen,
    )

    assert changed is False


def test_resolve_open_changed_returns_true_for_changed_action_surface(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    source_snapshot = make_snapshot(snapshot_id=3)
    source_compiled_screen = make_compiled_screen(
        "screen-source",
        sequence=4,
        source_snapshot_id=source_snapshot.snapshot_id,
        captured_at=source_snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="source",
    )
    post_snapshot = make_snapshot(snapshot_id=4)
    post_compiled_screen = make_compiled_screen(
        "screen-post",
        sequence=5,
        source_snapshot_id=post_snapshot.snapshot_id,
        captured_at=post_snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="post",
    )
    install_screen_state(
        runtime,
        snapshot=post_snapshot,
        public_screen=make_public_screen("screen-post"),
        compiled_screen=post_compiled_screen,
        artifacts=None,
    )

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id="screen-source",
        source_compiled_screen=source_compiled_screen,
    )

    assert changed is True


def test_resolve_open_changed_omits_without_source_id(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    snapshot = make_snapshot(snapshot_id=3)
    source_compiled_screen = make_compiled_screen(
        "screen-current",
        sequence=4,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        fingerprint="current",
    )

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id=None,
        source_compiled_screen=source_compiled_screen,
    )

    assert changed is None


def test_resolve_open_changed_omits_without_source_compiled_screen(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id="screen-current",
        source_compiled_screen=None,
    )

    assert changed is None


def test_resolve_open_changed_omits_for_mismatched_source_id(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    source_compiled_screen = make_compiled_screen(
        "screen-current",
        fingerprint="current",
    )

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id="screen-other",
        source_compiled_screen=source_compiled_screen,
    )

    assert changed is None


def test_resolve_open_changed_omits_without_post_open_authoritative_basis(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path, status=RuntimeStatus.READY)
    source_compiled_screen = make_compiled_screen(
        "screen-current",
        fingerprint="current",
    )

    changed = resolve_open_changed(
        runtime=runtime,
        source_screen_id="screen-current",
        source_compiled_screen=source_compiled_screen,
    )

    assert changed is None
