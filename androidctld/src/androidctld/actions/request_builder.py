"""Helpers for building device action requests and resolving ref targets."""

from __future__ import annotations

from androidctld.actions.submit_routing import SubmitRouteOutcome
from androidctld.commands.command_models import (
    ActionCommand,
    FocusCommand,
    GlobalCommand,
    LongTapCommand,
    OpenCommand,
    RefBoundActionCommand,
    ScrollCommand,
    SubmitCommand,
    TapCommand,
    TypeCommand,
    is_ref_bound_action_command,
)
from androidctld.commands.open_targets import (
    OpenAppTarget,
    OpenUrlTarget,
    validate_open_target,
)
from androidctld.device.action_models import (
    BuiltDeviceActionRequest,
    GlobalActionRequest,
    HandleTarget,
    LaunchAppActionRequest,
    LongTapActionRequest,
    NodeActionRequest,
    NoneTarget,
    OpenUrlActionRequest,
    ScrollActionRequest,
    TapActionRequest,
    TypeActionRequest,
)
from androidctld.errors import DaemonError, DaemonErrorCode, bad_request
from androidctld.protocol import CommandKind
from androidctld.refs.models import NodeHandle
from androidctld.refs.repair import (
    ref_repair_error,
    resolve_ref_decision,
)
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_artifacts,
    current_public_screen,
)
from androidctld.runtime_policy import action_timeout_ms


def build_action_request(
    session: WorkspaceRuntime, command: ActionCommand
) -> BuiltDeviceActionRequest:
    if isinstance(command, OpenCommand):
        return build_open_action_request(command)
    if isinstance(command, GlobalCommand):
        return BuiltDeviceActionRequest(
            payload=GlobalActionRequest(
                target=NoneTarget(),
                action=command.action,
                timeout_ms=action_timeout_ms(command.kind),
            )
        )
    if is_ref_bound_action_command(command):
        handle = resolve_ref_target(session, command.ref, command.source_screen_id)
        return build_action_request_for_binding(handle, command)
    raise bad_request("unsupported action kind", {"kind": command.kind.value})


def build_action_request_for_binding(
    handle: NodeHandle, command: RefBoundActionCommand
) -> BuiltDeviceActionRequest:
    if isinstance(command, TapCommand):
        return BuiltDeviceActionRequest(
            payload=TapActionRequest(
                target=HandleTarget(handle),
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    if isinstance(command, LongTapCommand):
        return BuiltDeviceActionRequest(
            payload=LongTapActionRequest(
                target=HandleTarget(handle),
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    if isinstance(command, TypeCommand):
        return BuiltDeviceActionRequest(
            payload=TypeActionRequest(
                target=HandleTarget(handle),
                text=command.text,
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    if isinstance(command, FocusCommand):
        return BuiltDeviceActionRequest(
            payload=NodeActionRequest(
                target=HandleTarget(handle),
                action=command.kind.value,
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    if isinstance(command, SubmitCommand):
        return BuiltDeviceActionRequest(
            payload=NodeActionRequest(
                target=HandleTarget(handle),
                action=command.kind.value,
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    if isinstance(command, ScrollCommand):
        return BuiltDeviceActionRequest(
            payload=ScrollActionRequest(
                target=HandleTarget(handle),
                direction=command.direction,
                timeout_ms=action_timeout_ms(command.kind),
            ),
            request_handle=handle,
        )
    raise bad_request("unsupported action kind", {"kind": command.kind.value})


def build_submit_action_request_for_route(
    route: SubmitRouteOutcome,
) -> BuiltDeviceActionRequest:
    if route.route == "direct":
        return BuiltDeviceActionRequest(
            payload=NodeActionRequest(
                target=HandleTarget(route.subject_handle),
                action=CommandKind.SUBMIT.value,
                timeout_ms=action_timeout_ms(CommandKind.SUBMIT),
            ),
            request_handle=route.subject_handle,
            dispatched_handle=route.dispatched_handle,
            submit_route=route.route,
        )
    return BuiltDeviceActionRequest(
        payload=TapActionRequest(
            target=HandleTarget(route.dispatched_handle),
            timeout_ms=action_timeout_ms(CommandKind.SUBMIT),
        ),
        request_handle=route.subject_handle,
        dispatched_handle=route.dispatched_handle,
        submit_route=route.route,
    )


def build_open_action_request(command: ActionCommand) -> BuiltDeviceActionRequest:
    assert isinstance(command, OpenCommand)
    target = validate_open_target(command.target)
    if isinstance(target, OpenAppTarget):
        return BuiltDeviceActionRequest(
            payload=LaunchAppActionRequest(
                target=NoneTarget(),
                package_name=target.package_name,
                timeout_ms=action_timeout_ms(CommandKind.OPEN),
            )
        )
    if isinstance(target, OpenUrlTarget):
        return BuiltDeviceActionRequest(
            payload=OpenUrlActionRequest(
                target=NoneTarget(),
                url=target.url,
                timeout_ms=action_timeout_ms(CommandKind.OPEN),
            )
        )
    raise bad_request("open requires target.kind app|url and target.value")


def resolve_ref_target(
    session: WorkspaceRuntime,
    ref: str,
    source_screen_id: str,
) -> NodeHandle:
    ensure_screen_ready(session)
    decision = resolve_ref_decision(session, ref, source_screen_id)
    if not decision.is_resolved:
        raise ref_repair_error(
            decision,
            public_screen=current_public_screen(session),
            artifacts=current_artifacts(session),
        )
    binding = decision.binding
    assert binding is not None
    return binding.handle


def ensure_screen_ready(session: WorkspaceRuntime) -> None:
    if session.screen_state is None or session.latest_snapshot is None:
        raise DaemonError(
            code=DaemonErrorCode.SCREEN_NOT_READY,
            message="screen is not ready yet",
            retryable=False,
            details={"workspaceRoot": session.workspace_root.as_posix()},
            http_status=200,
        )
