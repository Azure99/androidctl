"""Runtime policy constants shared across androidctld subsystems."""

from __future__ import annotations

from typing import Any, Final

from androidctld.protocol import CommandKind

DEFAULT_DEVICE_PORT: Final[int] = 17171
DEFAULT_DEVICE_RPC_TIMEOUT_SECONDS: Final[float] = 5.0
ANDROID_SCREENSHOT_METHOD_TIMEOUT_SECONDS: Final[float] = 11.0
SCREENSHOT_DEVICE_RPC_TRANSPORT_MARGIN_SECONDS: Final[float] = 1.0
SCREENSHOT_DEVICE_RPC_TIMEOUT_SECONDS: Final[float] = (
    ANDROID_SCREENSHOT_METHOD_TIMEOUT_SECONDS
    + SCREENSHOT_DEVICE_RPC_TRANSPORT_MARGIN_SECONDS
)
ADB_COMMAND_TIMEOUT_SECONDS: Final[float] = 10.0
DAEMON_HTTP_MAX_REQUEST_BODY_BYTES: Final[int] = 1 * 1024 * 1024
DAEMON_HTTP_SOCKET_TIMEOUT_SECONDS: Final[float] = 5.0
DEVICE_RPC_MAX_RESPONSE_BYTES: Final[int] = 4 * 1024 * 1024
SCREENSHOT_MAX_BINARY_BYTES: Final[int] = 32 * 1024 * 1024
SCREENSHOT_MAX_BASE64_CHARS: Final[int] = ((SCREENSHOT_MAX_BINARY_BYTES + 2) // 3) * 4
SCREENSHOT_MAX_RPC_RESPONSE_BYTES: Final[int] = 48 * 1024 * 1024
SCREENSHOT_MAX_OUTPUT_PIXELS: Final[int] = 16_777_216
MAIN_LOOP_SLEEP_SECONDS: Final[float] = 0.1
NON_NUMERIC_REF_SORT_BUCKET: Final[int] = 1_000_000_000

DEVICE_RPC_REQUEST_ID_BOOTSTRAP: Final[str] = "androidctld-bootstrap"
DEVICE_RPC_REQUEST_ID_SNAPSHOT: Final[str] = "androidctld-snapshot"
DEVICE_RPC_REQUEST_ID_SETTLE: Final[str] = "androidctld-settle"
DEVICE_RPC_REQUEST_ID_ACTION: Final[str] = "androidctld-action"
DEVICE_RPC_REQUEST_ID_ACTION_REPAIRED: Final[str] = "androidctld-action-repaired"
DEVICE_RPC_REQUEST_ID_WAIT: Final[str] = "androidctld-wait"
DEVICE_RPC_REQUEST_ID_SCREENSHOT: Final[str] = "androidctld-screenshot"
DEVICE_RPC_REQUEST_ID_LIST_APPS: Final[str] = "androidctld-list-apps"

DEFAULT_SNAPSHOT_INCLUDE_INVISIBLE: Final[bool] = True
DEFAULT_SNAPSHOT_INCLUDE_SYSTEM_WINDOWS: Final[bool] = True
DEFAULT_SCREENSHOT_FORMAT: Final[str] = "png"
DEFAULT_SCREENSHOT_SCALE: Final[float] = 1.0

DEFAULT_SETTLE_MIN_GRACE_MS: Final[int] = 200
SETTLE_MIN_GRACE_MS_BY_COMMAND: Final[dict[CommandKind, int]] = {
    CommandKind.OPEN: 500,
}
DEFAULT_SETTLE_MAX_TOTAL_MS: Final[int] = 1200
SETTLE_MAX_TOTAL_MS_BY_COMMAND: Final[dict[CommandKind, int]] = {
    CommandKind.OPEN: 4000,
}
DEFAULT_SETTLE_STABLE_WINDOW_MS: Final[int] = 300
SETTLE_STABLE_WINDOW_MS_BY_COMMAND: Final[dict[CommandKind, int]] = {
    CommandKind.OPEN: 500,
}
DEFAULT_SETTLE_SNAPSHOT_MAX_INTERVAL_MS: Final[int] = 250
SETTLE_SNAPSHOT_MAX_INTERVAL_MS_BY_COMMAND: Final[dict[CommandKind, int]] = {
    CommandKind.OPEN: 500,
}
SETTLE_POLL_SLICE_MS: Final[int] = 100

WAIT_TIMEOUT_MS_BY_KIND: Final[dict[str, int]] = {
    "text": 3000,
    "screen-change": 3000,
    "gone": 3000,
    "app": 3000,
    "idle": 3000,
}

ACTION_TIMEOUT_MS_BY_COMMAND: Final[dict[CommandKind, int]] = {
    CommandKind.OPEN: 5000,
    CommandKind.TAP: 5000,
    CommandKind.LONG_TAP: 5000,
    CommandKind.TYPE: 8000,
    CommandKind.GLOBAL: 5000,
    CommandKind.SCROLL: 5000,
    CommandKind.FOCUS: 5000,
    CommandKind.SUBMIT: 5000,
}

WAIT_EVENT_POLL_SLICE_MS: Final[int] = 250
WAIT_SNAPSHOT_MAX_INTERVAL_MS: Final[int] = 500
WAIT_LOOP_SLEEP_SECONDS: Final[float] = 0.05
TRANSIENT_INVALID_SNAPSHOT_RETRY_SECONDS: Final[float] = 0.05
TRANSIENT_INVALID_SNAPSHOT_MAX_RETRIES: Final[int] = 2
WAIT_IDLE_STABLE_WINDOW_MS: Final[int] = 500
QUERY_PROGRESS_WAIT_SECONDS: Final[float] = 0.2
QUERY_PROGRESS_POLL_SECONDS: Final[float] = 0.02


def settle_min_grace_ms(kind: CommandKind) -> int:
    return SETTLE_MIN_GRACE_MS_BY_COMMAND.get(kind, DEFAULT_SETTLE_MIN_GRACE_MS)


def settle_max_total_ms(kind: CommandKind) -> int:
    return SETTLE_MAX_TOTAL_MS_BY_COMMAND.get(kind, DEFAULT_SETTLE_MAX_TOTAL_MS)


def settle_stable_window_ms(kind: CommandKind) -> int:
    return SETTLE_STABLE_WINDOW_MS_BY_COMMAND.get(kind, DEFAULT_SETTLE_STABLE_WINDOW_MS)


def settle_snapshot_max_interval_ms(kind: CommandKind) -> int:
    return SETTLE_SNAPSHOT_MAX_INTERVAL_MS_BY_COMMAND.get(
        kind, DEFAULT_SETTLE_SNAPSHOT_MAX_INTERVAL_MS
    )


def default_wait_timeout_ms(wait_kind: object) -> int:
    normalized = str(getattr(wait_kind, "value", wait_kind))
    return WAIT_TIMEOUT_MS_BY_KIND[normalized]


def action_timeout_ms(kind: CommandKind) -> int:
    return ACTION_TIMEOUT_MS_BY_COMMAND[kind]


def default_snapshot_params() -> dict[str, bool]:
    return {
        "includeInvisible": DEFAULT_SNAPSHOT_INCLUDE_INVISIBLE,
        "includeSystemWindows": DEFAULT_SNAPSHOT_INCLUDE_SYSTEM_WINDOWS,
    }


def default_screenshot_params() -> dict[str, Any]:
    return {
        "format": DEFAULT_SCREENSHOT_FORMAT,
        "scale": DEFAULT_SCREENSHOT_SCALE,
    }
