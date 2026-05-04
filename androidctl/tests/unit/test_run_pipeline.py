from __future__ import annotations

from pathlib import Path

import click
import pytest
from pydantic import ValidationError

from androidctl.command_payloads import (
    LateBoundActionCommand,
    LateBoundGlobalActionCommand,
    LateBoundScreenRelativePredicate,
    LateBoundWaitCommand,
    build_global_action_command,
    build_ref_action_command,
    build_scroll_command,
    build_type_command,
    build_wait_command,
)
from androidctl.commands.run_pipeline import (
    AppContext,
    CliCommandRequest,
    PreDispatchCommandError,
    _prepare_ref_bound_request,
    bind_screen_relative_command,
    build_context,
    resolve_runtime_paths,
    run_close_command,
    run_command,
)
from androidctl.daemon.client import DaemonApiError, IncompatibleDaemonVersionError
from androidctl_contracts.command_catalog import entry_for_public_command
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import (
    AppPredicatePayload,
    CommandRunRequest,
    GlobalActionCommandPayload,
    GonePredicatePayload,
    IdlePredicatePayload,
    ListAppsCommandPayload,
    ObserveCommandPayload,
    RefActionCommandPayload,
    RuntimePayload,
    ScreenChangePredicatePayload,
    ScreenshotCommandPayload,
    TextPresentPredicatePayload,
    WaitCommandPayload,
)
from tests.support.daemon_fakes import ScriptedRecordingDaemon
from tests.support.semantic_contract import semantic_result, semantic_screen


def _retained_result(
    *,
    command: str,
    envelope: str,
    ok: bool = True,
    code: str | None = None,
    message: str | None = None,
    artifacts: dict[str, object] | None = None,
    details: dict[str, object] | None = None,
) -> RetainedResultEnvelope:
    return RetainedResultEnvelope.model_validate(
        {
            "ok": ok,
            "command": command,
            "envelope": envelope,
            "code": code,
            "message": message,
            "artifacts": artifacts or {},
            "details": details or {},
        }
    )


def _run_pipeline_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del daemon, command
    catalog_entry = entry_for_public_command(request.command.kind)
    assert catalog_entry is not None
    assert catalog_entry.result_category is not None
    return CommandResultCore.model_validate(
        semantic_result(
            command=request.command.kind,
            category=catalog_entry.result_category.value,
            screen_id="screen-next",
            source_screen_id=None,
            continuity_status="none",
            changed=None,
            screen_kwargs={
                "focus_ref": "n3",
                "target_ref": "n3",
            },
        )
    )


def _make_daemon(root: Path) -> ScriptedRecordingDaemon:
    return ScriptedRecordingDaemon(
        root=root,
        current_screen_id="screen-live",
        command_handlers=dict.fromkeys(
            (
                "wait",
                "back",
                "recents",
                "notifications",
                "home",
                "type",
                "scroll",
                "tap",
            ),
            _run_pipeline_result,
        ),
    )


def test_resolve_runtime_paths_returns_explicit_workspace_root(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "cwd"
    workspace_root = tmp_path / "workspace"
    ctx = AppContext(
        daemon=None,
        cwd=cwd,
        env={},
    )

    resolved_workspace_root = resolve_runtime_paths(
        workspace_root,
        ctx,
    )

    assert resolved_workspace_root == workspace_root.resolve()


def test_resolve_runtime_paths_defaults_to_cwd_without_override(
    tmp_path: Path,
) -> None:
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
    )

    resolved_workspace_root = resolve_runtime_paths(None, ctx)

    assert resolved_workspace_root == tmp_path.resolve()


def test_resolve_runtime_paths_prefers_env_over_cwd(
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "env-workspace"
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path / "cwd",
        env={"ANDROIDCTL_WORKSPACE_ROOT": str(env_root)},
    )

    resolved_workspace_root = resolve_runtime_paths(None, ctx)

    assert resolved_workspace_root == env_root.resolve()


def test_build_context_defers_daemon_discovery_until_workspace_is_known(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, Path, dict[str, str]]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.resolve_daemon_client",
        lambda *, workspace_root, cwd, env: (
            calls.append((workspace_root, cwd, dict(env)))
            or _make_daemon(workspace_root)
        ),
    )

    ctx = build_context()
    assert ctx.daemon is None
    assert calls == []

    assert ctx.daemon_discovery is not None
    daemon = ctx.daemon_discovery(tmp_path)
    assert isinstance(daemon, ScriptedRecordingDaemon)
    assert daemon.get_runtime().workspace_root == tmp_path.resolve().as_posix()
    assert calls[0][0] == tmp_path.resolve()


@pytest.mark.parametrize(
    ("command", "expected_command"),
    [
        (
            LateBoundActionCommand(kind="tap", ref="n3"),
            {"kind": "tap", "ref": "n3", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundActionCommand(kind="longTap", ref="n3"),
            {"kind": "longTap", "ref": "n3", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundActionCommand(kind="focus", ref="n3"),
            {"kind": "focus", "ref": "n3", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundActionCommand(kind="submit", ref="n3"),
            {"kind": "submit", "ref": "n3", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundActionCommand(kind="type", ref="n3", text="hello"),
            {
                "kind": "type",
                "ref": "n3",
                "text": "hello",
                "sourceScreenId": "screen-00013",
            },
        ),
        (
            LateBoundActionCommand(kind="scroll", ref="n8", direction="down"),
            {
                "kind": "scroll",
                "ref": "n8",
                "direction": "down",
                "sourceScreenId": "screen-00013",
            },
        ),
        (
            LateBoundGlobalActionCommand(kind="back"),
            {"kind": "back", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundGlobalActionCommand(kind="home"),
            {"kind": "home", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundGlobalActionCommand(kind="recents"),
            {"kind": "recents", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundGlobalActionCommand(kind="notifications"),
            {"kind": "notifications", "sourceScreenId": "screen-00013"},
        ),
        (
            LateBoundWaitCommand(
                predicate=LateBoundScreenRelativePredicate(kind="screen-change")
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "screen-change",
                    "sourceScreenId": "screen-00013",
                },
            },
        ),
        (
            LateBoundWaitCommand(
                predicate=LateBoundScreenRelativePredicate(kind="gone", ref="n7"),
                timeout_ms=500,
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "ref": "n7",
                    "sourceScreenId": "screen-00013",
                },
                "timeoutMs": 500,
            },
        ),
    ],
)
def test_bind_screen_relative_command_uses_one_policy_for_screen_relative_families(
    command: object,
    expected_command: dict[str, object],
) -> None:
    bound = bind_screen_relative_command(
        command,  # type: ignore[arg-type]
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="ready",
            current_screen_id="screen-00013",
        ),
    )

    assert bound.model_dump(exclude_none=True) == expected_command


@pytest.mark.parametrize(
    "command",
    [
        LateBoundActionCommand(kind="tap", ref="n3"),
        LateBoundGlobalActionCommand(kind="home"),
        LateBoundWaitCommand(
            predicate=LateBoundScreenRelativePredicate(kind="screen-change")
        ),
    ],
)
def test_prepare_ref_bound_request_routes_late_bound_families_through_binder(
    monkeypatch: pytest.MonkeyPatch,
    command: object,
) -> None:
    calls: list[object] = []

    def bind_for_test(
        command_to_bind: object, _runtime_payload: RuntimePayload
    ) -> ObserveCommandPayload:
        calls.append(command_to_bind)
        return ObserveCommandPayload(kind="observe")

    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.bind_screen_relative_command",
        bind_for_test,
    )

    prepared = _prepare_ref_bound_request(
        CliCommandRequest(
            public_command="test",
            command=command,  # type: ignore[arg-type]
        ),
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="ready",
            current_screen_id="screen-00013",
        ),
    )

    assert calls == [command]
    assert prepared.command == ObserveCommandPayload(kind="observe")


def test_prepare_ref_bound_request_preserves_explicit_screen_id_override() -> None:
    prepared = _prepare_ref_bound_request(
        CliCommandRequest(
            public_command="wait",
            command=build_wait_command(
                predicate=ScreenChangePredicatePayload(
                    kind="screen-change",
                    source_screen_id="screen-override",
                ),
                timeout_ms=None,
            ),
        ),
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="connected",
        ),
    )

    assert prepared.command.predicate.source_screen_id == "screen-override"


def test_prepare_ref_bound_request_preserves_explicit_global_action_override() -> None:
    command = build_global_action_command(
        kind="home",
        source_screen_id="screen-override",
    )

    prepared = _prepare_ref_bound_request(
        CliCommandRequest(
            public_command="home",
            command=command,
        ),
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="connected",
        ),
    )

    assert prepared.command is command
    assert isinstance(prepared.command, GlobalActionCommandPayload)
    assert prepared.command.source_screen_id == "screen-override"


@pytest.mark.parametrize(
    ("command", "expected_command"),
    [
        (
            build_ref_action_command(
                kind="tap",
                ref="n3",
                source_screen_id="screen-override",
            ),
            {"kind": "tap", "ref": "n3", "sourceScreenId": "screen-override"},
        ),
        (
            build_type_command(
                ref="n3",
                text="hello",
                source_screen_id="screen-override",
            ),
            {
                "kind": "type",
                "ref": "n3",
                "text": "hello",
                "sourceScreenId": "screen-override",
            },
        ),
        (
            build_scroll_command(
                ref="n8",
                direction="down",
                source_screen_id="screen-override",
            ),
            {
                "kind": "scroll",
                "ref": "n8",
                "direction": "down",
                "sourceScreenId": "screen-override",
            },
        ),
        (
            build_global_action_command(
                kind="home",
                source_screen_id="screen-override",
            ),
            {"kind": "home", "sourceScreenId": "screen-override"},
        ),
        (
            build_wait_command(
                predicate=ScreenChangePredicatePayload(
                    kind="screen-change",
                    source_screen_id="screen-override",
                ),
                timeout_ms=None,
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "screen-change",
                    "sourceScreenId": "screen-override",
                },
            },
        ),
        (
            build_wait_command(
                predicate=GonePredicatePayload(
                    kind="gone",
                    ref="n7",
                    source_screen_id="screen-override",
                ),
                timeout_ms=500,
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "ref": "n7",
                    "sourceScreenId": "screen-override",
                },
                "timeoutMs": 500,
            },
        ),
    ],
)
def test_bind_screen_relative_command_preserves_explicit_screen_id_overrides(
    command: object,
    expected_command: dict[str, object],
) -> None:
    bound = bind_screen_relative_command(
        command,  # type: ignore[arg-type]
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="connected",
        ),
    )

    assert bound.model_dump(exclude_none=True) == expected_command


@pytest.mark.parametrize(
    ("command", "expected_command"),
    [
        (
            build_wait_command(
                predicate=TextPresentPredicatePayload(
                    kind="text-present",
                    text="Connected",
                ),
                timeout_ms=None,
            ),
            {
                "kind": "wait",
                "predicate": {"kind": "text-present", "text": "Connected"},
            },
        ),
        (
            build_wait_command(
                predicate=AppPredicatePayload(
                    kind="app",
                    package_name="com.example.settings",
                ),
                timeout_ms=250,
            ),
            {
                "kind": "wait",
                "predicate": {
                    "kind": "app",
                    "packageName": "com.example.settings",
                },
                "timeoutMs": 250,
            },
        ),
        (
            build_wait_command(
                predicate=IdlePredicatePayload(kind="idle"),
                timeout_ms=None,
            ),
            {"kind": "wait", "predicate": {"kind": "idle"}},
        ),
    ],
)
def test_bind_screen_relative_command_leaves_non_screen_relative_waits_unchanged(
    command: object,
    expected_command: dict[str, object],
) -> None:
    bound = bind_screen_relative_command(
        command,  # type: ignore[arg-type]
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="ready",
            current_screen_id="screen-00013",
        ),
    )

    assert bound is command
    assert bound.model_dump(exclude_none=True) == expected_command


@pytest.mark.parametrize("action", ["back", "home", "recents", "notifications"])
def test_prepare_global_action_without_live_screen_id_binds_source_less_payload(
    action: str,
) -> None:
    prepared = _prepare_ref_bound_request(
        CliCommandRequest(
            public_command=action,
            command=LateBoundGlobalActionCommand(kind=action),
        ),
        RuntimePayload(
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
            status="ready",
        ),
    )

    assert prepared.command.model_dump(exclude_none=True) == {"kind": action}
    assert isinstance(prepared.command, GlobalActionCommandPayload)
    assert prepared.command.source_screen_id is None


@pytest.mark.parametrize(
    "command",
    [
        LateBoundActionCommand(kind="tap", ref="n3"),
        LateBoundWaitCommand(
            predicate=LateBoundScreenRelativePredicate(kind="screen-change")
        ),
    ],
)
@pytest.mark.parametrize(
    ("status", "expected_code", "expected_message"),
    [
        ("ready", "SCREEN_NOT_READY", "screen is not ready yet"),
        ("connected", "SCREEN_NOT_READY", "screen is not ready yet"),
        (
            "closed",
            "RUNTIME_NOT_CONNECTED",
            "runtime is not connected to a device",
        ),
    ],
)
def test_bind_screen_relative_command_uses_consistent_error_mapping(
    command: object,
    status: str,
    expected_code: str,
    expected_message: str,
) -> None:
    with pytest.raises(DaemonApiError) as error:
        bind_screen_relative_command(
            command,  # type: ignore[arg-type]
            RuntimePayload(
                workspace_root="/repo",
                artifact_root="/repo/.androidctl",
                status=status,
            ),
        )

    assert error.value.code == expected_code
    assert error.value.message == expected_message


def test_prepare_request_ready_without_screen_id_raises_screen_not_ready() -> None:
    with pytest.raises(DaemonApiError) as error:
        _prepare_ref_bound_request(
            CliCommandRequest(
                public_command="wait",
                command=build_wait_command(
                    predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
                    timeout_ms=None,
                ),
            ),
            RuntimePayload(
                workspace_root="/repo",
                artifact_root="/repo/.androidctl",
                status="ready",
            ),
        )

    assert error.value.code == "SCREEN_NOT_READY"
    assert error.value.message == "screen is not ready yet"


def test_run_command_uses_workspace_root_to_discover_daemon_and_returns_payload(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    discovery_calls: list[Path] = []

    def discover_for_test(resolved_workspace_root: Path) -> ScriptedRecordingDaemon:
        discovery_calls.append(resolved_workspace_root)
        return daemon

    ctx = AppContext(
        daemon=None,
        cwd=workspace_root,
        env={},
        daemon_discovery=discover_for_test,
    )

    outcome = run_command(
        CliCommandRequest(
            public_command="wait",
            command=build_wait_command(
                predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
                timeout_ms=1_234,
            ),
        ),
        ctx,
    )

    assert discovery_calls == [workspace_root.resolve()]
    assert daemon.runtime_requests == 1
    assert daemon.runs == [
        {
            "command": {
                "kind": "wait",
                "predicate": {
                    "kind": "screen-change",
                    "sourceScreenId": "screen-live",
                },
                "timeoutMs": 1234,
            },
        }
    ]
    assert isinstance(daemon.run_requests[0], CommandRunRequest)
    assert isinstance(daemon.run_requests[0].command, WaitCommandPayload)
    assert daemon.run_requests[0].command.timeout_ms == 1234
    assert isinstance(
        daemon.run_requests[0].command.predicate,
        ScreenChangePredicatePayload,
    )
    assert daemon.run_requests[0].command.predicate.source_screen_id == "screen-live"
    assert outcome.payload["command"] == "wait"
    assert outcome.payload["nextScreenId"] == "screen-next"


def test_run_command_returns_canonical_payload_for_explicit_null_result(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"

    def run_with_nulls(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> CommandResultCore:
        del daemon, command
        return CommandResultCore.model_validate(
            {
                "ok": True,
                "command": request.command.kind,
                "category": "wait",
                "payloadMode": "full",
                "sourceScreenId": None,
                "nextScreenId": "screen-next",
                "code": None,
                "message": None,
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                    "changed": None,
                },
                "screen": semantic_screen("screen-next"),
                "uncertainty": [],
                "warnings": [],
                "artifacts": {"screenshotPng": None},
            }
        )

    daemon = ScriptedRecordingDaemon(
        root=workspace_root.resolve(),
        current_screen_id="screen-live",
        command_handlers={"wait": run_with_nulls},
    )
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command="wait",
            command=build_wait_command(
                predicate=LateBoundScreenRelativePredicate(kind="screen-change"),
                timeout_ms=None,
            ),
        ),
        ctx,
    )

    assert "sourceScreenId" not in outcome.payload
    assert "code" not in outcome.payload
    assert "message" not in outcome.payload
    truth = outcome.payload["truth"]
    assert isinstance(truth, dict)
    assert "changed" not in truth
    assert outcome.payload["artifacts"] == {}
    assert outcome.payload["nextScreenId"] == "screen-next"
    screen = outcome.payload["screen"]
    assert isinstance(screen, dict)
    assert screen["screenId"] == "screen-next"


def test_run_command_returns_alias_payload_for_retained_result(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"

    def run_retained(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> RetainedResultEnvelope:
        del daemon, request, command
        return _retained_result(
            command="screenshot",
            envelope="artifact",
            artifacts={
                "screenshotPng": ("/repo/.androidctl/screenshots/screen-00013.png")
            },
            details={"stage": "capture"},
        )

    daemon = ScriptedRecordingDaemon(
        root=workspace_root.resolve(),
        current_screen_id="screen-live",
        command_handlers={"screenshot": run_retained},
    )
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command="screenshot",
            command=ScreenshotCommandPayload(kind="screenshot"),
        ),
        ctx,
    )

    assert outcome.payload == {
        "ok": True,
        "command": "screenshot",
        "envelope": "artifact",
        "artifacts": {
            "screenshotPng": "/repo/.androidctl/screenshots/screen-00013.png"
        },
        "details": {"stage": "capture"},
    }


def test_run_command_dispatches_list_apps_without_live_screen_requirement(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"

    def run_list_apps(
        daemon: ScriptedRecordingDaemon,
        request: CommandRunRequest,
        command: dict[str, object],
    ) -> ListAppsResult:
        del daemon, request
        assert command == {"kind": "listApps"}
        return ListAppsResult.model_validate(
            {
                "ok": True,
                "command": "list-apps",
                "apps": [
                    {
                        "packageName": "com.android.settings",
                        "appLabel": "Settings",
                    }
                ],
            }
        )

    daemon = ScriptedRecordingDaemon(
        root=workspace_root.resolve(),
        runtime_payload=RuntimePayload(
            workspace_root=workspace_root.resolve().as_posix(),
            artifact_root=(workspace_root.resolve() / ".androidctl").as_posix(),
            status="connected",
        ),
        current_screen_id=None,
        command_handlers={"listApps": run_list_apps},
    )
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command="list-apps",
            command=ListAppsCommandPayload(kind="listApps"),
        ),
        ctx,
    )

    assert daemon.runtime_requests == 1
    assert daemon.runs == [{"command": {"kind": "listApps"}}]
    assert isinstance(daemon.run_requests[0].command, ListAppsCommandPayload)
    assert outcome.payload == {
        "ok": True,
        "command": "list-apps",
        "apps": [
            {
                "packageName": "com.android.settings",
                "appLabel": "Settings",
            }
        ],
    }


def test_run_command_binds_deferred_gone_wait_to_live_screen(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command="wait",
            command=build_wait_command(
                predicate=LateBoundScreenRelativePredicate(kind="gone", ref="n7"),
                timeout_ms=1_234,
            ),
        ),
        ctx,
    )

    assert daemon.runs == [
        {
            "command": {
                "kind": "wait",
                "predicate": {
                    "kind": "gone",
                    "ref": "n7",
                    "sourceScreenId": "screen-live",
                },
                "timeoutMs": 1234,
            },
        }
    ]
    assert isinstance(daemon.run_requests[0].command, WaitCommandPayload)
    assert isinstance(daemon.run_requests[0].command.predicate, GonePredicatePayload)
    assert daemon.run_requests[0].command.predicate.source_screen_id == "screen-live"
    assert outcome.payload["command"] == "wait"


@pytest.mark.parametrize("action", ["back", "recents", "notifications"])
def test_run_command_injects_live_screen_id_for_global_action(
    tmp_path: Path,
    action: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command=action,
            command=LateBoundGlobalActionCommand(kind=action),
        ),
        ctx,
    )

    assert daemon.runtime_requests == 1
    assert daemon.runs == [
        {
            "command": {
                "kind": action,
                "sourceScreenId": "screen-live",
            },
        }
    ]
    assert daemon.run_requests[0].command.source_screen_id == "screen-live"
    assert outcome.payload["command"] == action


@pytest.mark.parametrize("action", ["back", "home", "recents", "notifications"])
def test_run_command_dispatches_source_less_global_action_without_live_truth(
    tmp_path: Path,
    action: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    daemon.current_screen_id = None
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    outcome = run_command(
        CliCommandRequest(
            public_command=action,
            command=LateBoundGlobalActionCommand(kind=action),
        ),
        ctx,
    )

    assert daemon.runtime_requests == 1
    assert daemon.runs == [{"command": {"kind": action}}]
    assert daemon.run_requests[0].command.source_screen_id is None
    assert outcome.payload["command"] == action


def test_build_command_run_request_rejects_unbound_late_bound_global_action() -> None:
    from androidctl.commands.run_pipeline import _build_command_run_request

    with pytest.raises(RuntimeError, match="prepared command was not bound"):
        _build_command_run_request(LateBoundGlobalActionCommand(kind="back"))


@pytest.mark.parametrize(
    ("cli_request", "expected_command_type", "expected_command"),
    [
        (
            CliCommandRequest(
                public_command="type",
                command=build_type_command(
                    ref="n3",
                    text="hello",
                    source_screen_id=None,
                ),
            ),
            "type",
            {
                "kind": "type",
                "ref": "n3",
                "text": "hello",
                "sourceScreenId": "screen-live",
            },
        ),
        (
            CliCommandRequest(
                public_command="scroll",
                command=build_scroll_command(
                    ref="n8",
                    direction="down",
                    source_screen_id=None,
                ),
            ),
            "scroll",
            {
                "kind": "scroll",
                "ref": "n8",
                "direction": "down",
                "sourceScreenId": "screen-live",
            },
        ),
    ],
)
def test_run_command_finalizes_type_and_scroll_to_shared_contract(
    tmp_path: Path,
    cli_request: CliCommandRequest,
    expected_command_type: str,
    expected_command: dict[str, object],
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    run_command(cli_request, ctx)

    assert daemon.runs == [{"command": expected_command}]
    assert daemon.run_requests[0].command.kind == expected_command_type
    assert daemon.run_requests[0].command.source_screen_id == "screen-live"


def test_run_command_finalizes_ref_bound_command_to_shared_contract(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    daemon = _make_daemon(workspace_root.resolve())
    ctx = AppContext(
        daemon=daemon,
        cwd=workspace_root,
        env={},
    )

    run_command(
        CliCommandRequest(
            public_command="tap",
            command=build_ref_action_command(
                kind="tap",
                ref="n3",
                source_screen_id=None,
            ),
        ),
        ctx,
    )

    assert isinstance(daemon.run_requests[0].command, RefActionCommandPayload)
    assert daemon.run_requests[0].command.source_screen_id == "screen-live"


def test_run_close_command_uses_injected_daemon_without_runtime_get(
    tmp_path: Path,
) -> None:
    close_calls: list[str] = []

    def close_handler(daemon: ScriptedRecordingDaemon) -> RetainedResultEnvelope:
        close_calls.append(daemon.root.as_posix())
        return _retained_result(command="close", envelope="lifecycle")

    daemon = ScriptedRecordingDaemon(root=tmp_path, close_handler=close_handler)
    ctx = AppContext(
        daemon=daemon,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )

    outcome = run_close_command(ctx, None)

    assert close_calls == [tmp_path.as_posix()]
    assert daemon.runtime_requests == 0
    assert outcome.payload["command"] == "close"
    assert outcome.payload["ok"] is True


def test_run_close_command_discovers_only_existing_daemon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    close_calls: list[str] = []
    discovery_calls: list[Path] = []

    def close_handler(daemon: ScriptedRecordingDaemon) -> RetainedResultEnvelope:
        close_calls.append(daemon.root.as_posix())
        return _retained_result(command="close", envelope="lifecycle")

    discovered = ScriptedRecordingDaemon(
        root=tmp_path,
        close_handler=close_handler,
    )
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda *, workspace_root, env: (
            discovery_calls.append(workspace_root) or discovered
        ),
    )
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )

    outcome = run_close_command(ctx, None)

    assert outcome.payload["ok"] is True
    assert discovery_calls == [tmp_path.resolve()]
    assert close_calls == [tmp_path.as_posix()]


def test_run_close_command_without_daemon_is_idempotent_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.discover_existing_daemon_client",
        lambda *, workspace_root, env: None,
    )
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            AssertionError("close must not start androidctld")
        ),
    )

    outcome = run_close_command(ctx, None)

    assert outcome.payload == {
        "ok": True,
        "command": "close",
        "envelope": "lifecycle",
        "artifacts": {},
        "details": {},
    }


def test_run_command_wraps_expected_daemon_api_pre_dispatch_error(
    tmp_path: Path,
) -> None:
    class RuntimeErrorDaemon(ScriptedRecordingDaemon):
        def get_runtime(self) -> RuntimePayload:
            raise DaemonApiError(
                code="RUNTIME_NOT_CONNECTED",
                message="runtime is not connected",
                details={},
            )

    ctx = AppContext(
        daemon=RuntimeErrorDaemon(root=tmp_path),
        cwd=tmp_path,
        env={},
    )

    with pytest.raises(PreDispatchCommandError) as error:
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )

    assert isinstance(error.value.cause, DaemonApiError)
    assert error.value.execution_outcome is None
    assert error.value.error_tier is None


@pytest.mark.parametrize(
    ("public_command", "command"),
    [
        ("tap", LateBoundActionCommand(kind="tap", ref="n3")),
        (
            "wait",
            LateBoundWaitCommand(
                predicate=LateBoundScreenRelativePredicate(kind="screen-change")
            ),
        ),
    ],
)
def test_run_command_marks_live_source_late_bind_error_pre_dispatch(
    tmp_path: Path,
    public_command: str,
    command: object,
) -> None:
    daemon = ScriptedRecordingDaemon(
        root=tmp_path,
        runtime_payload=RuntimePayload(
            workspace_root=tmp_path.as_posix(),
            artifact_root=(tmp_path / ".androidctl").as_posix(),
            status="connected",
        ),
        command_handlers={"tap": _run_pipeline_result, "wait": _run_pipeline_result},
    )
    ctx = AppContext(
        daemon=daemon,
        cwd=tmp_path,
        env={},
    )

    with pytest.raises(PreDispatchCommandError) as error:
        run_command(
            CliCommandRequest(
                public_command=public_command,
                command=command,  # type: ignore[arg-type]
            ),
            ctx,
        )

    assert isinstance(error.value.cause, DaemonApiError)
    assert error.value.cause.code == "SCREEN_NOT_READY"
    assert error.value.error_tier == "preDispatch"
    assert daemon.run_calls == []


def test_run_command_wraps_expected_click_pre_dispatch_error(
    tmp_path: Path,
) -> None:
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=None,
    )

    with pytest.raises(PreDispatchCommandError) as error:
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )

    assert isinstance(error.value.cause, click.ClickException)
    assert error.value.execution_outcome is None


def test_run_command_wraps_expected_validation_pre_dispatch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def raise_validation_error(_command: object) -> None:
        CommandRunRequest.model_validate(
            {"command": {"kind": "wait", "predicate": {"kind": "text-present"}}}
        )

    monkeypatch.setattr(
        "androidctl.commands.run_pipeline._build_command_run_request",
        raise_validation_error,
    )
    ctx = AppContext(
        daemon=ScriptedRecordingDaemon(root=tmp_path),
        cwd=tmp_path,
        env={},
    )

    with pytest.raises(PreDispatchCommandError) as error:
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )

    assert isinstance(error.value.cause, ValidationError)
    assert error.value.execution_outcome is None


def test_run_command_propagates_incompatible_daemon_version_from_discovery(
    tmp_path: Path,
) -> None:
    ctx = AppContext(
        daemon=None,
        cwd=tmp_path,
        env={},
        daemon_discovery=lambda _workspace_root: (_ for _ in ()).throw(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )
        ),
    )

    with pytest.raises(
        IncompatibleDaemonVersionError,
        match="release version mismatch",
    ):
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )


def test_run_command_wraps_expected_os_pre_dispatch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline.resolve_runtime_paths",
        lambda _workspace_root, _ctx: (_ for _ in ()).throw(
            OSError("workspace unavailable")
        ),
    )
    ctx = AppContext(
        daemon=ScriptedRecordingDaemon(root=tmp_path),
        cwd=tmp_path,
        env={},
    )

    with pytest.raises(PreDispatchCommandError) as error:
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )

    assert isinstance(error.value.cause, OSError)


@pytest.mark.parametrize(
    "programmer_error",
    [
        TypeError("wrong helper signature"),
        AssertionError("impossible state"),
        RuntimeError("unexpected preparation failure"),
    ],
)
def test_run_command_does_not_wrap_unexpected_pre_dispatch_programmer_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    programmer_error: Exception,
) -> None:
    monkeypatch.setattr(
        "androidctl.commands.run_pipeline._prepare_ref_bound_request",
        lambda _cli_request, _runtime_payload: (_ for _ in ()).throw(programmer_error),
    )
    ctx = AppContext(
        daemon=ScriptedRecordingDaemon(root=tmp_path),
        cwd=tmp_path,
        env={},
    )

    with pytest.raises(type(programmer_error)) as error:
        run_command(
            CliCommandRequest(
                public_command="observe",
                command=ObserveCommandPayload(kind="observe"),
            ),
            ctx,
        )

    assert error.value is programmer_error
