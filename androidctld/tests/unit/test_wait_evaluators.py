from pathlib import Path

from androidctld.commands.command_models import (
    AppWaitPredicate,
    GoneWaitPredicate,
    IdleWaitPredicate,
    ScreenChangeWaitPredicate,
    TextWaitPredicate,
    WaitCommand,
)
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot
from androidctld.waits.evaluators import (
    WaitIdleEvaluationState,
    WaitIdleTracking,
    WaitMatched,
    WaitNoMatch,
    WaitReadyContext,
    evaluate_ready_wait_match,
)

from .support.runtime import build_runtime
from .support.semantic_screen import (
    install_snapshot_screen,
    make_public_screen,
    make_snapshot,
)


def test_evaluate_ready_wait_match_returns_wait_result_for_text_match(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, public_screen, compiled_screen = _make_ready_state(tmp_path)

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi")),
        ready=_ready_context(snapshot, public_screen, compiled_screen),
        idle_state=WaitIdleEvaluationState(),
        now=0.0,
    )

    assert isinstance(outcome, WaitMatched)
    assert outcome.match.snapshot is snapshot
    assert outcome.match.app_match is None


def test_evaluate_ready_wait_match_returns_app_match_data_for_app_alias(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, public_screen, compiled_screen = _make_ready_state(
        tmp_path,
        package_name="com.google.android.settings.intelligence",
    )

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(
            predicate=AppWaitPredicate(package_name="com.android.settings"),
        ),
        ready=_ready_context(snapshot, public_screen, compiled_screen),
        idle_state=WaitIdleEvaluationState(),
        now=0.0,
    )

    assert isinstance(outcome, WaitMatched)
    assert outcome.match.snapshot is snapshot
    assert outcome.match.app_match is not None
    assert outcome.match.app_match.requested_package_name == "com.android.settings"
    assert (
        outcome.match.app_match.resolved_package_name
        == "com.google.android.settings.intelligence"
    )
    assert outcome.match.app_match.match_type == "alias"


def test_evaluate_ready_wait_match_waits_for_compiled_screen_before_text_match(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, public_screen, _compiled_screen = _make_ready_state(tmp_path)

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(predicate=TextWaitPredicate(text="Wi-Fi")),
        ready=_ready_context(snapshot, public_screen, None),
        idle_state=WaitIdleEvaluationState(),
        now=0.0,
    )

    assert isinstance(outcome, WaitNoMatch)


def test_evaluate_ready_wait_match_tracks_idle_signature_until_stable(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, public_screen, compiled_screen = _make_ready_state(tmp_path)

    initial_outcome = evaluate_ready_wait_match(
        command=WaitCommand(predicate=IdleWaitPredicate()),
        ready=_ready_context(snapshot, public_screen, compiled_screen),
        idle_state=WaitIdleEvaluationState(),
        now=1.0,
    )

    assert isinstance(initial_outcome, WaitIdleTracking)
    assert initial_outcome.idle_state.idle_signature is not None
    assert initial_outcome.idle_state.idle_stable_since == 1.0

    stable_outcome = evaluate_ready_wait_match(
        command=WaitCommand(predicate=IdleWaitPredicate()),
        ready=_ready_context(snapshot, public_screen, compiled_screen),
        idle_state=initial_outcome.idle_state,
        now=3.0,
    )

    assert isinstance(stable_outcome, WaitMatched)
    assert stable_outcome.match.snapshot is snapshot
    assert stable_outcome.match.app_match is None


def test_evaluate_ready_wait_match_clears_idle_tracking_without_compiled_screen(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, public_screen, _compiled_screen = _make_ready_state(tmp_path)

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(predicate=IdleWaitPredicate()),
        ready=_ready_context(snapshot, public_screen, None),
        idle_state=WaitIdleEvaluationState(
            idle_signature=("old",),
            idle_stable_since=1.0,
        ),
        now=2.0,
    )

    assert isinstance(outcome, WaitIdleTracking)
    assert outcome.idle_state == WaitIdleEvaluationState()


def test_evaluate_ready_wait_match_returns_wait_result_for_screen_change(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, _public_screen, compiled_screen = _make_ready_state(tmp_path)
    changed_screen = make_public_screen("screen-2")

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(
            predicate=ScreenChangeWaitPredicate(source_screen_id="screen-1"),
        ),
        ready=_ready_context(snapshot, changed_screen, compiled_screen),
        idle_state=WaitIdleEvaluationState(),
        now=0.0,
    )

    assert isinstance(outcome, WaitMatched)
    assert outcome.match.snapshot is snapshot
    assert outcome.match.app_match is None


def test_evaluate_ready_wait_match_returns_wait_result_for_gone(
    tmp_path: Path,
) -> None:
    _runtime, snapshot, _public_screen, compiled_screen = _make_ready_state(tmp_path)
    gone_screen = make_public_screen("screen-1", refs=())

    outcome = evaluate_ready_wait_match(
        command=WaitCommand(
            predicate=GoneWaitPredicate(source_screen_id="screen-1", ref="n7"),
        ),
        ready=_ready_context(snapshot, gone_screen, compiled_screen),
        idle_state=WaitIdleEvaluationState(),
        now=0.0,
    )

    assert isinstance(outcome, WaitMatched)
    assert outcome.match.snapshot is snapshot
    assert outcome.match.app_match is None


def _make_ready_state(
    tmp_path: Path,
    *,
    package_name: str | None = "com.android.settings",
) -> tuple[WorkspaceRuntime, RawSnapshot, PublicScreen, CompiledScreen]:
    runtime = build_runtime(tmp_path)
    snapshot = make_snapshot(package_name=package_name)
    compiled_screen = install_snapshot_screen(
        runtime,
        snapshot,
        include_artifacts=False,
    )
    public_screen = runtime.screen_state.public_screen
    return runtime, snapshot, public_screen, compiled_screen


def _ready_context(
    snapshot: RawSnapshot,
    public_screen: PublicScreen,
    compiled_screen: CompiledScreen | None,
) -> WaitReadyContext:
    return WaitReadyContext(
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=compiled_screen,
    )
