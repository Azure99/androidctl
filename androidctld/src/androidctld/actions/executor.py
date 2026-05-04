"""Mutating action runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from androidctl_contracts.command_results import (
    ActionTargetEvidence,
    ActionTargetPayload,
)
from androidctld.actions.action_target import (
    build_action_target_payload,
    build_same_or_successor_action_target,
    public_ref_for_handle,
    public_ref_for_raw_node,
)
from androidctld.actions.capabilities import (
    ensure_action_request_supported,
    ensure_command_supported,
    validate_action_semantics,
    validate_resolved_ref_action,
)
from androidctld.actions.focus_confirmation import (
    FocusConfirmationContext,
    FocusConfirmationOutcome,
    build_focus_confirmation_context,
)
from androidctld.actions.fresh_current import (
    capture_global_fresh_current_baseline,
    validate_global_fresh_current_evidence,
)
from androidctld.actions.postconditions import (
    RefActionPostconditionContext,
    validate_postcondition,
)
from androidctld.actions.request_builder import (
    build_action_request,
    build_submit_action_request_for_route,
    resolve_ref_target,
)
from androidctld.actions.settle import ActionSettler
from androidctld.actions.submit_confirmation import (
    SubmitConfirmationContext,
    SubmitConfirmationOutcome,
    build_submit_confirmation_context,
    validate_submit_confirmation,
)
from androidctld.actions.submit_routing import (
    SubmitRouteOutcome,
    resolve_submit_route,
)
from androidctld.actions.type_confirmation import (
    TypeConfirmationCandidate,
    TypeConfirmationContext,
    build_type_confirmation_context,
    validate_type_confirmation,
)
from androidctld.commands.command_models import (
    ActionCommand,
    FocusCommand,
    GlobalCommand,
    LongTapCommand,
    OpenCommand,
    RefBoundActionCommand,
    ScrollCommand,
    SubmitCommand,
    TypeCommand,
    is_ref_bound_action_command,
)
from androidctld.commands.models import CommandRecord
from androidctld.commands.result_builders import app_payload
from androidctld.commands.result_models import SemanticResultAssemblyInput
from androidctld.device.action_models import BuiltDeviceActionRequest
from androidctld.device.interfaces import DeviceClientFactory
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import DeviceRpcErrorCode
from androidctld.refs.models import NodeHandle
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import (
    current_compiled_screen,
    current_public_screen,
)
from androidctld.runtime_policy import (
    DEVICE_RPC_REQUEST_ID_ACTION,
)
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import (
    PublicNode,
    PublicScreen,
    iter_public_nodes,
)
from androidctld.snapshots.models import RawSnapshot
from androidctld.snapshots.refresh import ScreenRefreshService, settle_screen_signature

_POST_DISPATCH_SETTLE_TIMEOUT_WARNING = (
    "post-dispatch observation timed out before stability was confirmed"
)
_ACTION_AVAILABILITY_ERROR_CODES = {
    DaemonErrorCode.RUNTIME_NOT_CONNECTED,
    DaemonErrorCode.SCREEN_NOT_READY,
    DaemonErrorCode.DEVICE_DISCONNECTED,
    DaemonErrorCode.DEVICE_AGENT_UNAVAILABLE,
    DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
    DaemonErrorCode.DEVICE_RPC_FAILED,
    DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
}
_POST_DISPATCH_TRANSPORT_INVALIDATING_ERROR_CODES = {
    DaemonErrorCode.DEVICE_DISCONNECTED,
    DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET,
}


@dataclass
class ActionExecutionFailure(Exception):
    """Failure after the device action RPC returned and a later phase failed."""

    original_error: DaemonError
    normalized_error: DaemonError
    dispatch_attempted: bool
    truth_lost_after_dispatch: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.normalized_error.message)


def _try_focused_input_noop_success(
    session: WorkspaceRuntime,
    command: ActionCommand,
) -> SemanticResultAssemblyInput | None:
    if not isinstance(command, FocusCommand):
        return None
    if command.source_screen_id != session.current_screen_id:
        return None
    snapshot = session.latest_snapshot
    public_screen = current_public_screen(session)
    compiled_screen = current_compiled_screen(session)
    if snapshot is None or public_screen is None or compiled_screen is None:
        return None
    if public_screen.surface.focus.input_ref != command.ref:
        return None
    public_node = _unique_public_node(public_screen, command.ref)
    if public_node is None or public_node.role != "input":
        return None
    focused_node = compiled_screen.focused_input_node()
    if (
        focused_node is None
        or focused_node.ref != command.ref
        or compiled_screen.focused_input_ref() != command.ref
    ):
        return None
    return SemanticResultAssemblyInput(
        app_payload=app_payload(snapshot),
        execution_outcome="notAttempted",
    )


def _unique_public_node(screen: PublicScreen, ref: str) -> PublicNode | None:
    nodes = [
        node
        for group in screen.groups
        for node in iter_public_nodes(group.nodes)
        if node.ref == ref
    ]
    return nodes[0] if len(nodes) == 1 else None


class ActionCommandRepairPort(Protocol):
    def repair_action_command(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> BuiltDeviceActionRequest: ...

    def repair_action_binding(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> NodeHandle | None: ...


class ActionExecutor:
    def __init__(
        self,
        *,
        device_client_factory: DeviceClientFactory,
        screen_refresh: ScreenRefreshService,
        settler: ActionSettler,
        repairer: ActionCommandRepairPort,
        runtime_kernel: RuntimeKernel | None = None,
    ) -> None:
        self._device_client_factory = device_client_factory
        self._screen_refresh = screen_refresh
        self._settler = settler
        self._repairer = repairer
        self._runtime_kernel = runtime_kernel or getattr(
            screen_refresh,
            "runtime_kernel",
            None,
        )

    def execute(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: ActionCommand,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> SemanticResultAssemblyInput:
        kind = record.kind
        if session.connection is None or session.device_token is None:
            raise DaemonError(
                code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
                message="runtime is not connected to a device",
                retryable=False,
                details={"workspaceRoot": session.workspace_root.as_posix()},
                http_status=200,
            )
        if is_ref_bound_action_command(command) and (
            session.latest_snapshot is None or session.screen_state is None
        ):
            raise DaemonError(
                code=DaemonErrorCode.SCREEN_NOT_READY,
                message="screen is not ready yet",
                retryable=False,
                details={"workspaceRoot": session.workspace_root.as_posix()},
                http_status=200,
            )

        focused_noop_success = _try_focused_input_noop_success(session, command)
        if focused_noop_success is not None:
            return focused_noop_success

        client = None
        if session.transport is None:
            client = self._device_client_factory(
                session,
                lifecycle_lease=lifecycle_lease,
            )
        ensure_command_supported(session, command)
        validate_action_semantics(session, command)
        previous_screen = current_public_screen(session)
        previous_snapshot = session.latest_snapshot
        previous_compiled = current_compiled_screen(session)
        settle_baseline_signature = _settle_baseline_signature(
            previous_compiled,
            previous_snapshot,
        )
        fresh_current_baseline = (
            capture_global_fresh_current_baseline(
                action=command.action,
                snapshot=previous_snapshot,
                compiled_screen=previous_compiled,
            )
            if isinstance(command, GlobalCommand)
            else None
        )
        if client is None:
            client = self._device_client_factory(
                session,
                lifecycle_lease=lifecycle_lease,
            )
        repaired_once = False
        submit_route: SubmitRouteOutcome | None = None
        request, submit_route, repaired_once = self._build_dispatch_request(
            session,
            record,
            command,
            lifecycle_lease=lifecycle_lease,
            repaired_once=repaired_once,
        )
        if is_ref_bound_action_command(command) and not isinstance(
            command, SubmitCommand
        ):
            validate_resolved_ref_action(session, command, request.request_handle)
        subject_ref, dispatched_ref = _action_target_request_refs(
            session=session,
            command=command,
            request=request,
            submit_route=submit_route,
        )
        focus_context, type_confirmation_context, submit_context = (
            _confirmation_contexts_for_request(
                session=session,
                command=command,
                request=request,
            )
        )
        ref_postcondition_context = _ref_postcondition_context_for_request(
            session=session,
            command=command,
            request=request,
        )
        type_command = command if isinstance(command, TypeCommand) else None
        submit_command = command if isinstance(command, SubmitCommand) else None
        try:
            action_result = client.action_perform(
                request.payload,
                request_id=DEVICE_RPC_REQUEST_ID_ACTION,
            )
        except DaemonError as error:
            if (
                is_ref_bound_action_command(command)
                and self._is_device_rpc_error(error, DeviceRpcErrorCode.STALE_TARGET)
                and not repaired_once
            ):
                request, submit_route, repaired_once = self._build_dispatch_request(
                    session,
                    record,
                    command,
                    lifecycle_lease=lifecycle_lease,
                    repaired_once=True,
                    required_submit_route=(
                        submit_route.route if submit_route is not None else None
                    ),
                )
                if is_ref_bound_action_command(command) and not isinstance(
                    command, SubmitCommand
                ):
                    validate_resolved_ref_action(
                        session,
                        command,
                        request.request_handle,
                    )
                subject_ref, dispatched_ref = _action_target_request_refs(
                    session=session,
                    command=command,
                    request=request,
                    submit_route=submit_route,
                )
                focus_context, type_confirmation_context, submit_context = (
                    _confirmation_contexts_for_request(
                        session=session,
                        command=command,
                        request=request,
                    )
                )
                ref_postcondition_context = _ref_postcondition_context_for_request(
                    session=session,
                    command=command,
                    request=request,
                )
                try:
                    action_result = client.action_perform(
                        request.payload,
                        request_id=DEVICE_RPC_REQUEST_ID_ACTION,
                    )
                except DaemonError as retry_error:
                    raise self._map_action_error(
                        retry_error,
                        command=command,
                    ) from retry_error
            else:
                raise self._map_action_error(error, command=command) from error

        if isinstance(command, GlobalCommand) and self._runtime_kernel is not None:
            self._runtime_kernel.drop_current_screen_authority(
                session,
                lifecycle_lease,
            )

        try:
            candidate_validator = None
            if fresh_current_baseline is not None:

                def candidate_validator(
                    candidate_snapshot: RawSnapshot,
                    _public_screen: PublicScreen,
                    candidate_compiled_screen: CompiledScreen,
                ) -> None:
                    validate_global_fresh_current_evidence(
                        fresh_current_baseline,
                        snapshot=candidate_snapshot,
                        compiled_screen=candidate_compiled_screen,
                    )

            settle_result = self._settler.settle(
                session,
                client,
                kind,
                baseline_signature=settle_baseline_signature,
                lifecycle_lease=lifecycle_lease,
            )
            snapshot = settle_result.snapshot
            snapshot, public_screen, _artifacts = self._screen_refresh.refresh(
                session,
                snapshot,
                lifecycle_lease=lifecycle_lease,
                command_kind=kind,
                record=record,
                candidate_validator=candidate_validator,
            )
            type_confirmation: TypeConfirmationCandidate | None = None
            submit_confirmation: SubmitConfirmationOutcome | None = None
            command_target_handle = request.request_handle

            postcondition = validate_postcondition(
                command,
                previous_snapshot,
                snapshot,
                previous_screen,
                public_screen,
                session=session,
                focus_context=focus_context,
                action_result=action_result,
                ref_context=ref_postcondition_context,
            )
            if type_command is not None:
                if type_confirmation_context is None:
                    raise RuntimeError("type confirmation context was not prepared")
                confirmed_candidate = validate_type_confirmation(
                    session=session,
                    command=type_command,
                    snapshot=snapshot,
                    context=type_confirmation_context,
                    action_result=action_result,
                )
                type_confirmation = confirmed_candidate
                command_target_handle = (
                    type_confirmation.target_handle or command_target_handle
                )
            if submit_command is not None:
                if submit_route is None:
                    raise RuntimeError("submit route was not resolved")
                submit_confirmation = validate_submit_confirmation(
                    session=session,
                    route_kind=submit_route.route,
                    action_result=action_result,
                    previous_snapshot=previous_snapshot,
                    snapshot=snapshot,
                    previous_screen=previous_screen,
                    public_screen=public_screen,
                    context=submit_context,
                    command_target_handle=command_target_handle,
                )
                if (
                    submit_confirmation is not None
                    and submit_route is not None
                    and submit_route.route == "direct"
                    and submit_confirmation.status == "unconfirmed"
                ):
                    raise DaemonError(
                        code=DaemonErrorCode.SUBMIT_NOT_CONFIRMED,
                        message="submit effect could not be confirmed",
                        retryable=True,
                        details={"reason": "direct_submit_not_confirmed"},
                        http_status=200,
                    )
            action_target = _build_success_action_target(
                command=command,
                source_ref=getattr(command, "ref", None),
                source_screen_id=getattr(command, "source_screen_id", None),
                subject_ref=subject_ref,
                dispatched_ref=dispatched_ref,
                repaired_once=repaired_once,
                public_screen=public_screen,
                compiled_screen=current_compiled_screen(session),
                focus_confirmation=postcondition.focus_confirmation,
                type_confirmation=type_confirmation,
                submit_confirmation=submit_confirmation,
                submit_route=submit_route,
            )
        except DaemonError as error:
            normalized_error = self._map_action_error(error, command=command)
            credential_invalidated = (
                normalized_error.code == DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED
            )
            if credential_invalidated:
                if self._runtime_kernel is not None:
                    self._runtime_kernel.invalidate_device_credentials(
                        session,
                        lifecycle_lease,
                    )
                truth_lost_after_dispatch = False
            else:
                truth_lost_after_dispatch = _is_action_availability_error(
                    normalized_error
                )
            if truth_lost_after_dispatch and self._runtime_kernel is not None:
                self._runtime_kernel.drop_current_screen_authority(
                    session,
                    lifecycle_lease,
                    discard_transport=(
                        _should_discard_transport_after_dispatch(normalized_error)
                    ),
                )
            raise ActionExecutionFailure(
                original_error=error,
                normalized_error=normalized_error,
                # This means the device action RPC returned successfully; a
                # later settle/refresh/confirmation phase failed.
                dispatch_attempted=True,
                truth_lost_after_dispatch=truth_lost_after_dispatch,
            ) from error

        return SemanticResultAssemblyInput(
            app_payload=app_payload(
                snapshot,
                app_match=postcondition.app_match,
            ),
            action_target=action_target,
            warnings=(
                (_POST_DISPATCH_SETTLE_TIMEOUT_WARNING,)
                if settle_result.timed_out
                else ()
            ),
        )

    def _build_dispatch_request(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: ActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
        repaired_once: bool,
        required_submit_route: str | None = None,
    ) -> tuple[BuiltDeviceActionRequest, SubmitRouteOutcome | None, bool]:
        if isinstance(command, SubmitCommand):
            if repaired_once or command.source_screen_id != session.current_screen_id:
                subject_handle = self._repair_action_binding(
                    session,
                    record,
                    command,
                    lifecycle_lease=lifecycle_lease,
                )
                source_evidence: ActionTargetEvidence = "refRepair"
                repaired_once = True
            else:
                subject_handle = resolve_ref_target(
                    session,
                    command.ref,
                    command.source_screen_id,
                )
                source_evidence = "liveRef"
            route = resolve_submit_route(
                session,
                command,
                subject_handle=subject_handle,
                source_evidence=source_evidence,
            )
            if (
                required_submit_route is not None
                and route.route != required_submit_route
            ):
                raise DaemonError(
                    code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
                    message="submit is not available for the requested target",
                    retryable=False,
                    details={
                        "reason": "submit_route_changed_after_repair",
                        "ref": command.ref,
                        "requiredRoute": required_submit_route,
                        "resolvedRoute": route.route,
                    },
                    http_status=200,
                )
            request = build_submit_action_request_for_route(route)
            ensure_action_request_supported(
                session,
                command=command.kind,
                request=request.payload,
            )
            return request, route, repaired_once

        if is_ref_bound_action_command(command) and (
            repaired_once or command.source_screen_id != session.current_screen_id
        ):
            request = self._repairer.repair_action_command(
                session,
                record,
                command,
                lifecycle_lease=lifecycle_lease,
            )
            return request, None, True
        return build_action_request(session, command), None, repaired_once

    def _repair_action_binding(
        self,
        session: WorkspaceRuntime,
        record: CommandRecord,
        command: RefBoundActionCommand,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> NodeHandle | None:
        return self._repairer.repair_action_binding(
            session,
            record,
            command,
            lifecycle_lease=lifecycle_lease,
        )

    def _map_action_error(
        self, error: DaemonError, *, command: ActionCommand
    ) -> DaemonError:
        if isinstance(command, OpenCommand) and self._is_device_rpc_error(
            error, DeviceRpcErrorCode.ACTION_FAILED
        ):
            return DaemonError(
                code=DaemonErrorCode.OPEN_FAILED,
                message=error.message,
                retryable=error.retryable,
                details=dict(error.details),
                http_status=error.http_status,
            )
        if not self._is_device_rpc_error(
            error, DeviceRpcErrorCode.TARGET_NOT_ACTIONABLE
        ):
            return error
        return DaemonError(
            code=DaemonErrorCode.TARGET_NOT_ACTIONABLE,
            message=error.message,
            retryable=error.retryable,
            details=dict(error.details),
            http_status=error.http_status,
        )

    def _is_device_rpc_error(
        self, error: DaemonError, code: DeviceRpcErrorCode
    ) -> bool:
        return (
            error.code == DaemonErrorCode.DEVICE_RPC_FAILED
            and error.details.get("deviceCode") == code.value
        )


def _settle_baseline_signature(
    compiled_screen: CompiledScreen | None,
    previous_snapshot: RawSnapshot | None,
) -> tuple[object, ...]:
    if previous_snapshot is not None:
        return settle_screen_signature(compiled_screen, previous_snapshot)
    return ("connected-without-screen",)


def _action_target_request_refs(
    *,
    session: WorkspaceRuntime,
    command: ActionCommand,
    request: BuiltDeviceActionRequest,
    submit_route: SubmitRouteOutcome | None,
) -> tuple[str | None, str | None]:
    if not isinstance(command, (FocusCommand, TypeCommand, SubmitCommand)):
        return None, None
    if submit_route is not None:
        return submit_route.subject_ref, submit_route.dispatched_ref
    subject_ref = public_ref_for_handle(
        compiled_screen=current_compiled_screen(session),
        public_screen=current_public_screen(session),
        handle=request.request_handle,
    )
    dispatched_handle = request.dispatched_handle or request.request_handle
    dispatched_ref = public_ref_for_handle(
        compiled_screen=current_compiled_screen(session),
        public_screen=current_public_screen(session),
        handle=dispatched_handle,
    )
    return subject_ref, dispatched_ref


def _confirmation_contexts_for_request(
    *,
    session: WorkspaceRuntime,
    command: ActionCommand,
    request: BuiltDeviceActionRequest,
) -> tuple[
    FocusConfirmationContext | None,
    TypeConfirmationContext | None,
    SubmitConfirmationContext | None,
]:
    focus_command = command if isinstance(command, FocusCommand) else None
    submit_command = command if isinstance(command, SubmitCommand) else None
    type_command = command if isinstance(command, TypeCommand) else None
    focus_context = (
        build_focus_confirmation_context(session, focus_command, request.request_handle)
        if focus_command is not None
        else None
    )
    type_confirmation_context = (
        build_type_confirmation_context(session, type_command, request.request_handle)
        if type_command is not None
        else None
    )
    submit_context = (
        build_submit_confirmation_context(
            session,
            submit_command,
            request.request_handle,
        )
        if submit_command is not None
        else None
    )
    return focus_context, type_confirmation_context, submit_context


def _ref_postcondition_context_for_request(
    *,
    session: WorkspaceRuntime,
    command: ActionCommand,
    request: BuiltDeviceActionRequest,
) -> RefActionPostconditionContext | None:
    if not isinstance(command, (LongTapCommand, ScrollCommand)):
        return None
    baseline_screen = current_public_screen(session)
    target_ref = public_ref_for_handle(
        compiled_screen=current_compiled_screen(session),
        public_screen=baseline_screen,
        handle=request.request_handle,
    )
    baseline_target = (
        None
        if baseline_screen is None or target_ref is None
        else _unique_public_node(baseline_screen, target_ref)
    )
    return RefActionPostconditionContext(
        target_ref=target_ref,
        baseline_screen=baseline_screen,
        baseline_target=baseline_target,
    )


def _is_action_availability_error(error: DaemonError) -> bool:
    return error.code in _ACTION_AVAILABILITY_ERROR_CODES


def _should_discard_transport_after_dispatch(error: DaemonError) -> bool:
    return error.code in _POST_DISPATCH_TRANSPORT_INVALIDATING_ERROR_CODES


def _build_success_action_target(
    *,
    command: ActionCommand,
    source_ref: object,
    source_screen_id: object,
    subject_ref: str | None,
    dispatched_ref: str | None,
    repaired_once: bool,
    public_screen: PublicScreen,
    compiled_screen: CompiledScreen | None,
    focus_confirmation: FocusConfirmationOutcome | None,
    type_confirmation: TypeConfirmationCandidate | None,
    submit_confirmation: SubmitConfirmationOutcome | None,
    submit_route: SubmitRouteOutcome | None,
) -> ActionTargetPayload | None:
    if not isinstance(command, (FocusCommand, TypeCommand, SubmitCommand)):
        return None
    if not isinstance(source_ref, str) or not isinstance(source_screen_id, str):
        return None
    source_evidence: ActionTargetEvidence = (
        submit_route.source_evidence
        if submit_route is not None
        else "refRepair" if repaired_once else "liveRef"
    )
    next_screen_id = public_screen.screen_id
    if isinstance(command, FocusCommand):
        next_ref = public_ref_for_handle(
            compiled_screen=compiled_screen,
            public_screen=public_screen,
            handle=(
                None if focus_confirmation is None else focus_confirmation.target_handle
            ),
        )
        strategy = None if focus_confirmation is None else focus_confirmation.strategy
        return build_same_or_successor_action_target(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            next_ref=next_ref,
            evidence=_confirmation_evidence(
                source_evidence,
                strategy,
                "focusConfirmation",
            ),
        )
    if isinstance(command, TypeCommand):
        next_ref = public_ref_for_handle(
            compiled_screen=compiled_screen,
            public_screen=public_screen,
            handle=(
                None if type_confirmation is None else type_confirmation.target_handle
            ),
        )
        if next_ref is None and type_confirmation is not None:
            next_ref = public_ref_for_raw_node(
                compiled_screen=compiled_screen,
                public_screen=public_screen,
                node=type_confirmation.node,
            )
        strategy = None if type_confirmation is None else type_confirmation.strategy
        return build_same_or_successor_action_target(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            next_ref=next_ref,
            evidence=_confirmation_evidence(
                source_evidence,
                strategy,
                "typeConfirmation",
            ),
        )
    if submit_confirmation is None:
        return None
    submit_evidence = _submit_confirmation_evidence(source_evidence, submit_route)
    if submit_confirmation.status == "targetGone":
        return build_action_target_payload(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            identity_status="gone",
            evidence=(*submit_evidence, "targetGone"),
        )
    if submit_confirmation.status == "sameTarget":
        next_ref = public_ref_for_handle(
            compiled_screen=compiled_screen,
            public_screen=public_screen,
            handle=submit_confirmation.target_handle,
        )
        if next_ref is None:
            next_ref = public_ref_for_raw_node(
                compiled_screen=compiled_screen,
                public_screen=public_screen,
                node=submit_confirmation.node,
            )
        return build_same_or_successor_action_target(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            next_ref=next_ref,
            evidence=submit_evidence,
        )
    if submit_confirmation.status == "publicChange":
        return build_action_target_payload(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            identity_status="unconfirmed",
            evidence=(*submit_evidence, "publicChange"),
        )
    if submit_confirmation.status == "unconfirmed":
        if submit_route is not None and submit_route.route == "direct":
            return None
        return build_action_target_payload(
            source_ref=source_ref,
            source_screen_id=source_screen_id,
            subject_ref=subject_ref,
            dispatched_ref=dispatched_ref,
            next_screen_id=next_screen_id,
            identity_status="unconfirmed",
            evidence=(*submit_evidence, "ambiguousSuccessor"),
        )


def _confirmation_evidence(
    source_evidence: ActionTargetEvidence,
    strategy: object,
    command_evidence: ActionTargetEvidence,
) -> tuple[ActionTargetEvidence, ...]:
    evidence: list[ActionTargetEvidence] = [source_evidence]
    if strategy in {
        "requestTarget",
        "resolvedTarget",
        "reusedRef",
        "fingerprintRematch",
    }:
        evidence.append(cast(ActionTargetEvidence, strategy))
    evidence.append(command_evidence)
    return tuple(evidence)


def _submit_confirmation_evidence(
    source_evidence: ActionTargetEvidence,
    submit_route: SubmitRouteOutcome | None,
) -> tuple[ActionTargetEvidence, ...]:
    evidence: list[ActionTargetEvidence] = [source_evidence]
    if submit_route is not None and submit_route.route == "attributed":
        evidence.append("attributedRoute")
    evidence.append("submitConfirmation")
    return tuple(evidence)
