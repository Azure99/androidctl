"""Pure compilers from validated shared wire payloads to executable commands."""

from __future__ import annotations

from androidctl_contracts import daemon_api as wire_api
from androidctld.commands.command_models import (
    AppWaitPredicate,
    ConnectCommand,
    FocusCommand,
    GlobalCommand,
    GoneWaitPredicate,
    IdleWaitPredicate,
    ListAppsCommand,
    LongTapCommand,
    ObserveCommand,
    OpenAppTarget,
    OpenCommand,
    OpenUrlTarget,
    RefBoundActionCommand,
    ScreenChangeWaitPredicate,
    ScreenshotCommand,
    ScrollCommand,
    SubmitCommand,
    TapCommand,
    TextWaitPredicate,
    TypeCommand,
    WaitCommand,
)
from androidctld.device.types import ConnectionConfig
from androidctld.protocol import ConnectionMode


def compile_connect_command(payload: wire_api.ConnectCommandPayload) -> ConnectCommand:
    connection = payload.connection
    mode = ConnectionMode(connection.mode)
    if mode is ConnectionMode.LAN:
        if connection.host is None or connection.port is None:
            raise TypeError("lan connect wire payload requires host and port")
        return ConnectCommand(
            connection=ConnectionConfig(
                mode=mode,
                token=connection.token,
                host=connection.host,
                port=connection.port,
            )
        )
    return ConnectCommand(
        connection=ConnectionConfig(
            mode=mode,
            token=connection.token,
            serial=connection.serial,
        )
    )


def compile_observe_command(payload: wire_api.ObserveCommandPayload) -> ObserveCommand:
    del payload
    return ObserveCommand()


def compile_list_apps_command(
    payload: wire_api.ListAppsCommandPayload,
) -> ListAppsCommand:
    del payload
    return ListAppsCommand()


def compile_open_command(payload: wire_api.OpenCommandPayload) -> OpenCommand:
    target = payload.target
    if isinstance(target, wire_api.OpenAppTargetPayload):
        return OpenCommand(target=OpenAppTarget(package_name=target.value))
    if isinstance(target, wire_api.OpenUrlTargetPayload):
        return OpenCommand(target=OpenUrlTarget(url=target.value))
    raise TypeError(f"unsupported open target payload: {type(target)!r}")


def compile_ref_action_command(
    payload: (
        wire_api.RefActionCommandPayload
        | wire_api.TypeCommandPayload
        | wire_api.ScrollCommandPayload
    ),
) -> RefBoundActionCommand:
    if isinstance(payload, wire_api.TypeCommandPayload):
        return TypeCommand(
            ref=payload.ref,
            source_screen_id=payload.source_screen_id,
            text=payload.text,
        )
    if isinstance(payload, wire_api.ScrollCommandPayload):
        return ScrollCommand(
            ref=payload.ref,
            source_screen_id=payload.source_screen_id,
            direction=payload.direction,
        )
    if payload.kind == "tap":
        return TapCommand(ref=payload.ref, source_screen_id=payload.source_screen_id)
    if payload.kind == "longTap":
        return LongTapCommand(
            ref=payload.ref,
            source_screen_id=payload.source_screen_id,
        )
    if payload.kind == "focus":
        return FocusCommand(ref=payload.ref, source_screen_id=payload.source_screen_id)
    if payload.kind == "submit":
        return SubmitCommand(
            ref=payload.ref,
            source_screen_id=payload.source_screen_id,
        )
    raise TypeError(f"unsupported ref action payload kind: {payload.kind!r}")


def compile_global_action_command(
    payload: wire_api.GlobalActionCommandPayload,
) -> GlobalCommand:
    return GlobalCommand(
        action=payload.kind,
        source_screen_id=payload.source_screen_id,
    )


def compile_service_wait_command(
    payload: wire_api.WaitCommandPayload,
) -> WaitCommand:
    predicate = payload.predicate
    if isinstance(predicate, wire_api.TextPresentPredicatePayload):
        return WaitCommand(
            predicate=TextWaitPredicate(text=predicate.text),
            timeout_ms=payload.timeout_ms,
        )
    if isinstance(predicate, wire_api.ScreenChangePredicatePayload):
        return WaitCommand(
            predicate=ScreenChangeWaitPredicate(
                source_screen_id=predicate.source_screen_id,
            ),
            timeout_ms=payload.timeout_ms,
        )
    if isinstance(predicate, wire_api.GonePredicatePayload):
        return WaitCommand(
            predicate=GoneWaitPredicate(
                source_screen_id=predicate.source_screen_id,
                ref=predicate.ref,
            ),
            timeout_ms=payload.timeout_ms,
        )
    if isinstance(predicate, wire_api.AppPredicatePayload):
        return WaitCommand(
            predicate=AppWaitPredicate(package_name=predicate.package_name),
            timeout_ms=payload.timeout_ms,
        )
    if isinstance(predicate, wire_api.IdlePredicatePayload):
        return WaitCommand(
            predicate=IdleWaitPredicate(),
            timeout_ms=payload.timeout_ms,
        )
    raise TypeError(f"unsupported wait predicate payload: {type(predicate)!r}")


def compile_screenshot_command(
    payload: wire_api.ScreenshotCommandPayload,
) -> ScreenshotCommand:
    del payload
    return ScreenshotCommand()


__all__ = [
    "compile_connect_command",
    "compile_global_action_command",
    "compile_list_apps_command",
    "compile_observe_command",
    "compile_open_command",
    "compile_ref_action_command",
    "compile_screenshot_command",
    "compile_service_wait_command",
]
