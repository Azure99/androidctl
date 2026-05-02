from __future__ import annotations

from pathlib import Path
from typing import Any

from androidctl_contracts.daemon_api import (
    OpenAppTargetPayload,
    OpenCommandPayload,
)
from androidctld.commands.from_boundary import compile_open_command
from androidctld.commands.handlers.action import ActionCommandHandler
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import ScreenState
from androidctld.runtime.screen_state import current_public_screen
from androidctld.semantics.public_models import (
    PublicApp,
    PublicFocus,
    PublicGroup,
    PublicScreen,
    PublicSurface,
)

from .support.runtime import build_runtime, install_screen_state
from .support.semantic_screen import (
    make_compiled_screen,
    make_snapshot,
)


class _FakeRuntimeKernel:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def ensure_runtime(self) -> Any:
        return self._runtime

    def capture_lifecycle_lease(self, runtime: Any) -> object:
        del runtime
        return object()


class _NoOpActionExecutor:
    def execute(
        self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
    ) -> None:
        del record, command, lifecycle_lease
        if current_public_screen(runtime) is None:
            runtime.current_screen_id = "screen-opened"
            runtime.screen_state = ScreenState(
                public_screen=_make_screen("screen-opened")
            )
        return None


class _SwitchingActionExecutor:
    def __init__(self, next_screen_id: str, *, fingerprint: str) -> None:
        self._next_screen_id = next_screen_id
        self._fingerprint = fingerprint

    def execute(
        self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
    ) -> None:
        del record, command, lifecycle_lease
        _install_authoritative_screen(
            runtime,
            screen_id=self._next_screen_id,
            fingerprint=self._fingerprint,
            ref="n7",
            sequence=runtime.screen_sequence + 1,
            snapshot_id=runtime.latest_snapshot.snapshot_id + 1,
        )


class _PublicOnlyPostOpenExecutor:
    def execute(
        self, runtime: Any, record: Any, command: Any, lifecycle_lease: Any
    ) -> None:
        del record, command, lifecycle_lease
        runtime.screen_state = ScreenState(public_screen=_make_screen("screen-opened"))
        runtime.current_screen_id = "screen-opened"


def _make_screen(screen_id: str) -> PublicScreen:
    return PublicScreen(
        screen_id=screen_id,
        app=PublicApp(
            package_name="com.android.settings",
            activity_name="SettingsActivity",
        ),
        surface=PublicSurface(
            keyboard_visible=False,
            focus=PublicFocus(),
        ),
        groups=(
            PublicGroup(name="targets"),
            PublicGroup(name="keyboard"),
            PublicGroup(name="system"),
            PublicGroup(name="context"),
            PublicGroup(name="dialog"),
        ),
        omitted=(),
        visible_windows=(),
        transient=(),
    )


def _make_handler(tmp_path: Path) -> tuple[ActionCommandHandler, Any]:
    runtime = build_runtime(
        tmp_path,
        status=RuntimeStatus.READY,
        screen_sequence=1,
    )
    handler = ActionCommandHandler(
        runtime_kernel=_FakeRuntimeKernel(runtime),
        action_executor=_NoOpActionExecutor(),
    )
    return handler, runtime


def _install_authoritative_screen(
    runtime: Any,
    *,
    screen_id: str,
    fingerprint: str,
    sequence: int = 1,
    snapshot_id: int = 1,
    ref: str = "n1",
) -> None:
    snapshot = make_snapshot(snapshot_id=snapshot_id)
    compiled_screen = make_compiled_screen(
        screen_id,
        sequence=sequence,
        source_snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        package_name=snapshot.package_name,
        activity_name=snapshot.activity_name,
        fingerprint=fingerprint,
        ref=ref,
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=compiled_screen.to_public_screen(),
        compiled_screen=compiled_screen,
        artifacts=None,
    )


def test_bootstrap_open_omits_source_screen_id_and_changed(tmp_path: Path) -> None:
    handler, runtime = _make_handler(tmp_path)

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        )
    )

    assert current_public_screen(runtime) is not None
    assert payload.get("sourceScreenId") is None
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None


def test_open_with_existing_live_screen_keeps_basis_but_resets_continuity(
    tmp_path: Path,
) -> None:
    handler, runtime = _make_handler(tmp_path)
    runtime.current_screen_id = "screen-settings"
    runtime.screen_state = ScreenState(public_screen=_make_screen("screen-settings"))

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        )
    )

    assert payload["sourceScreenId"] == "screen-settings"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"].get("changed") is None


def test_open_same_screen_reports_changed_false_after_p2_5(tmp_path: Path) -> None:
    handler, runtime = _make_handler(tmp_path)
    _install_authoritative_screen(
        runtime,
        screen_id="screen-settings",
        fingerprint="settings",
        ref="n7",
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        )
    )

    assert payload["sourceScreenId"] == "screen-settings"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"]["changed"] is False


def test_open_changed_screen_reports_changed_true_after_p2_5(tmp_path: Path) -> None:
    runtime = build_runtime(
        tmp_path,
        status=RuntimeStatus.READY,
        screen_sequence=1,
    )
    _install_authoritative_screen(
        runtime,
        screen_id="screen-settings",
        fingerprint="settings",
        ref="n7",
    )
    handler = ActionCommandHandler(
        runtime_kernel=_FakeRuntimeKernel(runtime),
        action_executor=_SwitchingActionExecutor(
            "screen-messages",
            fingerprint="messages",
        ),
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        )
    )

    assert payload["sourceScreenId"] == "screen-settings"
    assert payload["truth"]["continuityStatus"] == "none"
    assert payload["truth"]["changed"] is True


def test_open_authoritative_source_then_non_authoritative_post_open_omits_changed(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        tmp_path,
        status=RuntimeStatus.READY,
        screen_sequence=1,
    )
    _install_authoritative_screen(
        runtime,
        screen_id="screen-settings",
        fingerprint="settings",
        ref="n7",
    )
    handler = ActionCommandHandler(
        runtime_kernel=_FakeRuntimeKernel(runtime),
        action_executor=_PublicOnlyPostOpenExecutor(),
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        )
    )

    assert payload["sourceScreenId"] == "screen-settings"
    assert payload["truth"]["continuityStatus"] == "none"
    assert "changed" not in payload["truth"]


def test_open_explicit_mismatched_source_preserves_context_without_changed(
    tmp_path: Path,
) -> None:
    handler, runtime = _make_handler(tmp_path)
    _install_authoritative_screen(
        runtime,
        screen_id="screen-settings",
        fingerprint="settings",
        ref="n7",
    )

    payload = handler.handle_open(
        command=compile_open_command(
            OpenCommandPayload(
                kind="open",
                target=OpenAppTargetPayload(
                    kind="app",
                    value="com.google.android.apps.messaging",
                ),
            )
        ),
        source_screen_id="screen-explicit",
    )

    assert payload["sourceScreenId"] == "screen-explicit"
    assert payload["truth"]["continuityStatus"] == "none"
    assert "changed" not in payload["truth"]
