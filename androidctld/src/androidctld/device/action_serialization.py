"""Serialization helpers for outbound device action requests."""

from __future__ import annotations

from typing import Any

from androidctld.device.action_models import (
    CoordinatesTarget,
    DeviceActionRequest,
    DeviceActionTarget,
    GlobalActionRequest,
    HandleTarget,
    LaunchAppActionRequest,
    LongTapActionRequest,
    NodeActionRequest,
    NoneTarget,
    OpenUrlActionRequest,
    ScrollActionRequest,
    SwipeActionRequest,
    TapActionRequest,
    TypeActionRequest,
)
from androidctld.device.adapters import dump_node_handle


def dump_device_action_request(
    request: DeviceActionRequest,
) -> dict[str, Any]:
    if isinstance(request, TapActionRequest):
        return {
            "kind": "tap",
            "target": dump_device_action_target(request.target),
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, LongTapActionRequest):
        return {
            "kind": "longTap",
            "target": dump_device_action_target(request.target),
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, TypeActionRequest):
        return {
            "kind": "type",
            "target": dump_device_action_target(request.target),
            "input": {
                "text": request.text,
                "replace": True,
                "submit": request.submit,
                "ensureFocused": True,
            },
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, NodeActionRequest):
        return {
            "kind": "node",
            "target": dump_device_action_target(request.target),
            "node": {"action": request.action},
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, ScrollActionRequest):
        return {
            "kind": "scroll",
            "target": dump_device_action_target(request.target),
            "scroll": {"direction": request.direction},
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, SwipeActionRequest):
        if not isinstance(request.target, NoneTarget):
            raise TypeError("swipe action requires none target")
        return {
            "kind": "gesture",
            "target": dump_device_action_target(request.target),
            "gesture": {"direction": request.direction},
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, GlobalActionRequest):
        return {
            "kind": "global",
            "target": dump_device_action_target(request.target),
            "global": {"action": request.action},
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, LaunchAppActionRequest):
        return {
            "kind": "launchApp",
            "target": dump_device_action_target(request.target),
            "intent": {
                "packageName": request.package_name,
            },
            "options": {"timeoutMs": request.timeout_ms},
        }
    if isinstance(request, OpenUrlActionRequest):
        return {
            "kind": "openUrl",
            "target": dump_device_action_target(request.target),
            "intent": {
                "url": request.url,
            },
            "options": {"timeoutMs": request.timeout_ms},
        }
    raise TypeError(f"unsupported device action request: {type(request)!r}")


def dump_device_action_target(target: DeviceActionTarget) -> dict[str, Any]:
    if isinstance(target, HandleTarget):
        return {
            "kind": "handle",
            "handle": dump_node_handle(target.handle),
        }
    if isinstance(target, CoordinatesTarget):
        return {
            "kind": "coordinates",
            "x": target.x,
            "y": target.y,
        }
    if isinstance(target, NoneTarget):
        return {"kind": "none"}
    raise TypeError(f"unsupported device action target: {type(target)!r}")


__all__ = ["dump_device_action_request", "dump_device_action_target"]
