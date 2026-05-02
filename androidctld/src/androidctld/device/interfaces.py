"""Typed device-client protocols for runtime collaborators."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from androidctld.device.action_models import DeviceActionRequest
from androidctld.device.types import (
    ActionPerformResult,
    EventsPollResult,
    ScreenshotCaptureResult,
)
from androidctld.runtime.lifecycle import RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime


class EventPollingClient(Protocol):
    def events_poll(
        self, after_seq: int, wait_ms: int, limit: int, request_id: str
    ) -> EventsPollResult: ...


class ActionPerformingClient(Protocol):
    def action_perform(
        self, request: DeviceActionRequest, request_id: str
    ) -> ActionPerformResult: ...


class ScreenshotCaptureClient(Protocol):
    def screenshot_capture(self, request_id: str) -> ScreenshotCaptureResult: ...


class DeviceRuntimeClient(
    EventPollingClient,
    ActionPerformingClient,
    ScreenshotCaptureClient,
    Protocol,
):
    pass


@runtime_checkable
class DeviceClientProvider(Protocol):
    def device_client(
        self,
        session: WorkspaceRuntime,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
    ) -> DeviceRuntimeClient: ...


class DeviceClientFactory(Protocol):
    def __call__(
        self,
        session: WorkspaceRuntime,
        *,
        lifecycle_lease: RuntimeLifecycleLease | None = None,
    ) -> DeviceRuntimeClient: ...
