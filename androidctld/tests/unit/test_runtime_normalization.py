from __future__ import annotations

import json
from typing import Any

from androidctld.daemon.service import DaemonService
from androidctld.device.types import ConnectionSpec, DeviceEndpoint, RuntimeTransport
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.runtime.models import ScreenState
from androidctld.runtime.store import RuntimeStore
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import (
    PublicApp,
    PublicFocus,
    PublicScreen,
    PublicSurface,
    build_public_groups,
)
from androidctld.snapshots.models import RawIme, RawSnapshot

from ..support.runtime_store import runtime_store_for_workspace


class _UnusedCommandService:
    def run(
        self,
        *,
        command: Any,
    ) -> dict[str, Any]:
        del command
        raise AssertionError("run should not be called")

    def close_runtime(self) -> dict[str, Any]:
        raise AssertionError("close_runtime should not be called")


def _runtime_get(service: DaemonService) -> dict[str, object]:
    _, payload = service.handle(
        method="POST",
        path="/runtime/get",
        headers={
            "X-Androidctld-Token": "daemon-token",
            "X-Androidctld-Owner": "shell:self:1",
        },
        body=b"{}",
    )
    return payload


def _service(runtime_store: RuntimeStore) -> DaemonService:
    return DaemonService(
        runtime_store=runtime_store,
        command_service=_UnusedCommandService(),  # type: ignore[arg-type]
        bound_owner_id="shell:self:1",
    )


def test_runtime_get_stale_ready_state_with_connection_downgrades_to_connected(
    tmp_path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00003"
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )
    runtime_store.persist_runtime(runtime)

    payload = _runtime_get(_service(runtime_store))

    assert payload["runtime"]["status"] == "connected"
    assert "currentScreenId" not in payload["runtime"]
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.current_screen_id is None
    persisted = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "connected"
    assert "currentScreenId" not in persisted


def test_runtime_get_stale_ready_consistent_screen_no_transport_downgrades_to_broken(
    tmp_path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00003"
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime_store.persist_runtime(runtime)

    payload = _runtime_get(_service(runtime_store))

    assert payload["runtime"]["status"] == "broken"
    assert "currentScreenId" not in payload["runtime"]
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.current_screen_id is None
    assert runtime.transport is None
    assert runtime.connection is None
    assert runtime.device_token is None
    persisted = json.loads(runtime.runtime_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "broken"
    assert "currentScreenId" not in persisted


def test_runtime_get_connected_state_omits_non_authoritative_current_id(
    tmp_path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.CONNECTED
    runtime.current_screen_id = "screen-old"
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )

    payload = _runtime_get(_service(runtime_store))

    assert payload["runtime"]["status"] == "connected"
    assert "currentScreenId" not in payload["runtime"]
    assert runtime.current_screen_id == "screen-old"


def test_runtime_get_zero_ref_live_screen_remains_ready(tmp_path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.screen_sequence = 3
    runtime.current_screen_id = "screen-00003"
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.transport = RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=lambda: None,
    )
    runtime.latest_snapshot = RawSnapshot(
        snapshot_id=3,
        captured_at="2026-04-08T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )
    compiled_screen = CompiledScreen(
        screen_id="screen-00003",
        sequence=3,
        source_snapshot_id=3,
        captured_at="2026-04-08T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        keyboard_visible=False,
    )
    runtime.screen_state = ScreenState(
        public_screen=PublicScreen(
            screen_id="screen-00003",
            app=PublicApp(
                package_name="com.android.settings",
                activity_name="SettingsActivity",
            ),
            surface=PublicSurface(
                keyboard_visible=False,
                focus=PublicFocus(),
            ),
            groups=build_public_groups(),
            omitted=(),
            visible_windows=(),
            transient=(),
        ),
        compiled_screen=compiled_screen,
    )
    runtime_store.persist_runtime(runtime)

    payload = _runtime_get(_service(runtime_store))

    assert payload["runtime"]["status"] == "ready"
    assert payload["runtime"]["currentScreenId"] == "screen-00003"
    assert runtime.status is RuntimeStatus.READY
    assert runtime.current_screen_id == "screen-00003"


def test_runtime_get_stale_ready_state_without_live_transport_downgrades_to_broken(
    tmp_path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00003"
    runtime.connection = ConnectionSpec(mode=ConnectionMode.ADB, serial="emulator-5554")
    runtime.device_token = "device-token"
    runtime.latest_snapshot = RawSnapshot(
        snapshot_id=3,
        captured_at="2026-04-08T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(),
        display={"widthPx": 1080, "heightPx": 2400, "densityDpi": 420, "rotation": 0},
    )
    compiled_screen = CompiledScreen(
        screen_id="screen-00003",
        sequence=3,
        source_snapshot_id=3,
        captured_at="2026-04-08T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        keyboard_visible=False,
    )
    runtime.screen_state = ScreenState(
        public_screen=PublicScreen(
            screen_id="screen-00003",
            app=PublicApp(
                package_name="com.android.settings",
                activity_name="SettingsActivity",
            ),
            surface=PublicSurface(
                keyboard_visible=False,
                focus=PublicFocus(),
            ),
            groups=build_public_groups(),
            omitted=(),
            visible_windows=(),
            transient=(),
        ),
        compiled_screen=compiled_screen,
    )
    runtime_store.persist_runtime(runtime)

    payload = _runtime_get(_service(runtime_store))

    assert payload["runtime"]["status"] == "broken"
    assert "currentScreenId" not in payload["runtime"]
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.current_screen_id is None
    assert runtime.transport is None
    assert runtime.connection is None
    assert runtime.device_token is None
