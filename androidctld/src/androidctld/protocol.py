"""Centralized protocol enums for androidctld runtime."""

from __future__ import annotations

from enum import Enum


class RuntimeStatus(str, Enum):
    NEW = "new"
    BOOTSTRAPPING = "bootstrapping"
    CONNECTED = "connected"
    READY = "ready"
    BROKEN = "broken"
    CLOSED = "closed"


class ConnectionMode(str, Enum):
    ADB = "adb"
    LAN = "lan"


class CommandKind(str, Enum):
    CONNECT = "connect"
    OBSERVE = "observe"
    LIST_APPS = "listApps"
    OPEN = "open"
    TAP = "tap"
    LONG_TAP = "longTap"
    TYPE = "type"
    FOCUS = "focus"
    SUBMIT = "submit"
    SCROLL = "scroll"
    GLOBAL = "global"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    CLOSE = "close"


class DeviceRpcMethod(str, Enum):
    META_GET = "meta.get"
    SNAPSHOT_GET = "snapshot.get"
    EVENTS_POLL = "events.poll"
    ACTION_PERFORM = "action.perform"
    SCREENSHOT_CAPTURE = "screenshot.capture"


class DeviceRpcErrorCode(str, Enum):
    STALE_TARGET = "STALE_TARGET"
    TARGET_NOT_ACTIONABLE = "TARGET_NOT_ACTIONABLE"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    RUNTIME_NOT_READY = "RUNTIME_NOT_READY"
    ACCESSIBILITY_DISABLED = "ACCESSIBILITY_DISABLED"
