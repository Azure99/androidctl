"""Shared device-facing types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from androidctld.config import DEFAULT_HOST
from androidctld.protocol import ConnectionMode
from androidctld.refs.models import NodeHandle
from androidctld.runtime_policy import DEFAULT_DEVICE_PORT


@dataclass(frozen=True)
class ConnectionConfig:
    mode: ConnectionMode
    token: str
    serial: str | None = None
    host: str | None = None
    port: int = DEFAULT_DEVICE_PORT


@dataclass(frozen=True)
class ConnectionSpec:
    mode: ConnectionMode
    port: int = DEFAULT_DEVICE_PORT
    serial: str | None = None
    host: str | None = None

    @classmethod
    def from_config(cls, config: ConnectionConfig) -> ConnectionSpec:
        if config.mode is ConnectionMode.ADB:
            return cls(
                mode=config.mode,
                port=config.port,
                serial=config.serial,
                host=None,
            )
        return cls(
            mode=config.mode,
            port=config.port,
            serial=config.serial,
            host=config.host,
        )

    def to_connection_config(self, token: str) -> ConnectionConfig:
        return ConnectionConfig(
            mode=self.mode,
            token=token,
            serial=self.serial,
            host=self.host,
            port=self.port,
        )


@dataclass(frozen=True)
class DeviceEndpoint:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_DEVICE_PORT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class DeviceCapabilities:
    supports_events_poll: bool
    supports_screenshot: bool
    action_kinds: list[str] = field(default_factory=list)

    def supports_action(self, action_kind: str) -> bool:
        return action_kind in self.action_kinds


@dataclass
class MetaInfo:
    service: str
    version: str
    capabilities: DeviceCapabilities


class ActionStatus(str, Enum):
    DONE = "done"
    PARTIAL = "partial"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ObservedApp:
    package_name: str | None = None
    activity_name: str | None = None


@dataclass(frozen=True)
class ResolvedHandleTarget:
    handle: NodeHandle
    kind: str = "handle"


@dataclass(frozen=True)
class ResolvedCoordinatesTarget:
    x: float
    y: float
    kind: str = "coordinates"


@dataclass(frozen=True)
class ResolvedNoneTarget:
    kind: str = "none"


ResolvedTarget = ResolvedHandleTarget | ResolvedCoordinatesTarget | ResolvedNoneTarget


@dataclass(frozen=True)
class ActionPerformResult:
    action_id: str
    status: ActionStatus
    duration_ms: int | None = None
    resolved_target: ResolvedTarget | None = None
    observed: ObservedApp | None = None


@dataclass(frozen=True)
class DeviceEvent:
    seq: int
    type: str
    timestamp: str
    data: dict[str, Any]


@dataclass(frozen=True)
class EventsPollResult:
    events: tuple[DeviceEvent, ...]
    latest_seq: int
    need_resync: bool
    timed_out: bool


@dataclass(frozen=True)
class ScreenshotCaptureResult:
    content_type: str
    width_px: int
    height_px: int
    body_base64: str


@dataclass
class RuntimeTransport:
    endpoint: DeviceEndpoint
    close: Callable[[], None]


@dataclass
class BootstrapResult:
    connection: ConnectionSpec
    transport: RuntimeTransport
    meta: MetaInfo
