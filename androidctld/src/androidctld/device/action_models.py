"""Typed outbound device action request models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from androidctld.refs.models import NodeHandle


@dataclass(frozen=True)
class HandleTarget:
    handle: NodeHandle


@dataclass(frozen=True)
class CoordinatesTarget:
    x: int
    y: int


@dataclass(frozen=True)
class NoneTarget:
    pass


TapTarget: TypeAlias = HandleTarget | CoordinatesTarget


@dataclass(frozen=True)
class TapActionRequest:
    target: TapTarget
    timeout_ms: int


@dataclass(frozen=True)
class LongTapActionRequest:
    target: TapTarget
    timeout_ms: int


@dataclass(frozen=True)
class TypeActionRequest:
    target: HandleTarget
    text: str
    timeout_ms: int
    submit: bool = False


@dataclass(frozen=True)
class NodeActionRequest:
    target: HandleTarget
    action: str
    timeout_ms: int


@dataclass(frozen=True)
class ScrollActionRequest:
    target: HandleTarget
    direction: str
    timeout_ms: int


@dataclass(frozen=True)
class SwipeActionRequest:
    target: NoneTarget
    direction: str
    timeout_ms: int


@dataclass(frozen=True)
class GlobalActionRequest:
    target: NoneTarget
    action: str
    timeout_ms: int


@dataclass(frozen=True)
class LaunchAppActionRequest:
    target: NoneTarget
    package_name: str
    timeout_ms: int


@dataclass(frozen=True)
class OpenUrlActionRequest:
    target: NoneTarget
    url: str
    timeout_ms: int


DeviceActionTarget: TypeAlias = HandleTarget | CoordinatesTarget | NoneTarget

DeviceActionRequest: TypeAlias = (
    TapActionRequest
    | LongTapActionRequest
    | TypeActionRequest
    | NodeActionRequest
    | ScrollActionRequest
    | SwipeActionRequest
    | GlobalActionRequest
    | LaunchAppActionRequest
    | OpenUrlActionRequest
)


@dataclass(frozen=True)
class BuiltDeviceActionRequest:
    payload: DeviceActionRequest
    request_handle: NodeHandle | None = None
    dispatched_handle: NodeHandle | None = None
    submit_route: str | None = None


def required_action_kind_for_request(request: DeviceActionRequest) -> str:
    if isinstance(request, TapActionRequest):
        return "tap"
    if isinstance(request, LongTapActionRequest):
        return "longTap"
    if isinstance(request, TypeActionRequest):
        return "type"
    if isinstance(request, NodeActionRequest):
        return "node"
    if isinstance(request, ScrollActionRequest):
        return "scroll"
    if isinstance(request, SwipeActionRequest):
        return "gesture"
    if isinstance(request, GlobalActionRequest):
        return "global"
    if isinstance(request, LaunchAppActionRequest):
        return "launchApp"
    if isinstance(request, OpenUrlActionRequest):
        return "openUrl"
    raise TypeError(f"unsupported device action request: {type(request)!r}")


__all__ = [
    "CoordinatesTarget",
    "DeviceActionRequest",
    "DeviceActionTarget",
    "GlobalActionRequest",
    "HandleTarget",
    "LaunchAppActionRequest",
    "LongTapActionRequest",
    "NodeActionRequest",
    "NoneTarget",
    "OpenUrlActionRequest",
    "ScrollActionRequest",
    "SwipeActionRequest",
    "TapActionRequest",
    "TapTarget",
    "TypeActionRequest",
    "required_action_kind_for_request",
]
