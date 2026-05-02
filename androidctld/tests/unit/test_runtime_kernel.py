from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import androidctld.runtime.kernel as runtime_kernel_module
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.writer import (
    ArtifactWriter,
    StagedArtifactWrite,
    StagedFileUpdate,
)
from androidctld.device.types import (
    BootstrapResult,
    ConnectionConfig,
    ConnectionSpec,
    DeviceCapabilities,
    DeviceEndpoint,
    MetaInfo,
    RuntimeTransport,
)
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.refs.models import RefRegistry
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.kernel import ScreenRefreshUpdate
from androidctld.runtime.models import ScreenState
from androidctld.semantics.compiler import SemanticCompiler
from androidctld.snapshots.models import RawIme, RawSnapshot

from ..support.runtime_store import runtime_store_for_workspace


class _CloseRecorder:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _transport(recorder: _CloseRecorder | None = None) -> RuntimeTransport:
    handle = recorder or _CloseRecorder()
    return RuntimeTransport(
        endpoint=DeviceEndpoint(host="127.0.0.1", port=17171),
        close=handle.close,
    )


def _bootstrap_result(transport: RuntimeTransport) -> BootstrapResult:
    return BootstrapResult(
        connection=ConnectionSpec(
            mode=ConnectionMode.LAN,
            host="127.0.0.1",
            port=17171,
        ),
        transport=transport,
        meta=MetaInfo(
            service="androidctl-device-agent",
            version="test",
            capabilities=DeviceCapabilities(
                supports_events_poll=True,
                supports_screenshot=True,
                action_kinds=["tap"],
            ),
        ),
    )


def _snapshot(snapshot_id: int = 1) -> RawSnapshot:
    return RawSnapshot(
        snapshot_id=snapshot_id,
        captured_at="1970-01-01T00:00:00Z",
        package_name="com.example",
        activity_name=".MainActivity",
        ime=RawIme(visible=False, window_id=None),
        windows=(),
        nodes=(),
        display={
            "widthPx": 1080,
            "heightPx": 1920,
            "densityDpi": 440,
            "rotation": 0,
        },
    )


def _staged_screen_artifacts(
    tmp_path: Path,
) -> tuple[ScreenArtifacts, StagedArtifactWrite, Path, Path]:
    staged_path = tmp_path / "screen.staged.json"
    final_path = tmp_path / "screen.json"
    staged_path.write_text("{}", encoding="utf-8")
    artifacts = ScreenArtifacts(screen_json=final_path.as_posix())
    return (
        artifacts,
        StagedArtifactWrite(
            artifacts=artifacts,
            file_updates=(
                StagedFileUpdate(staged_path=staged_path, final_path=final_path),
            ),
        ),
        staged_path,
        final_path,
    )


def test_ensure_runtime_normalizes_stale_ready_and_persists_runtime_only(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00003"
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"

    ensured = kernel.ensure_runtime()

    assert ensured is runtime
    assert runtime.status is RuntimeStatus.BROKEN
    assert cast(Any, runtime).current_screen_id is None
    assert cast(Any, runtime).connection is None
    assert cast(Any, runtime).device_token is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"


def test_begin_connect_clears_runtime_state_and_persists_runtime_only(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    previous_transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00002"
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.transport = _transport(previous_transport_recorder)
    lease = kernel.capture_lifecycle_lease(runtime)

    connecting_transport = _transport()
    assert kernel.begin_connect(runtime, lease, transport=connecting_transport) is True

    assert previous_transport_recorder.close_calls == 1
    assert runtime.status is RuntimeStatus.BOOTSTRAPPING
    assert runtime.transport is connecting_transport
    assert cast(Any, runtime).connection is None
    assert cast(Any, runtime).device_token is None
    assert cast(Any, runtime).current_screen_id is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "bootstrapping"


def test_activate_connect_closes_bootstrap_transport_when_lease_is_stale(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    stale_lease = kernel.capture_lifecycle_lease(runtime)

    assert kernel.invalidate_runtime(runtime, lease=stale_lease) is True

    bootstrap_transport_recorder = _CloseRecorder()
    activated = kernel.activate_connect(
        runtime,
        stale_lease,
        bootstrap_result=_bootstrap_result(_transport(bootstrap_transport_recorder)),
        device_token="device-token",
    )

    assert activated is False
    assert bootstrap_transport_recorder.close_calls == 1
    assert runtime.transport is None
    assert runtime.status is RuntimeStatus.NEW


def test_rebootstrap_transport_returns_existing_transport_without_bootstrap(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    existing_transport = _transport()
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.transport = existing_transport

    def _unexpected_bootstrap(config: ConnectionConfig) -> BootstrapResult:
        del config
        raise AssertionError("bootstrap should not be called")

    transport = kernel.rebootstrap_transport(
        runtime,
        bootstrap=_unexpected_bootstrap,
    )

    assert transport is existing_transport
    assert runtime.transport is existing_transport


def test_rebootstrap_transport_rejects_stale_lease_before_existing_transport(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    previous_transport_recorder = _CloseRecorder()
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.transport = _transport(previous_transport_recorder)
    stale_lease = kernel.capture_lifecycle_lease(runtime)

    assert kernel.invalidate_runtime(runtime, lease=stale_lease) is True
    replacement_transport = _transport()
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.transport = replacement_transport

    def _unexpected_bootstrap(config: ConnectionConfig) -> BootstrapResult:
        del config
        raise AssertionError("bootstrap should not be called")

    with pytest.raises(DaemonError) as error:
        kernel.rebootstrap_transport(
            runtime,
            bootstrap=_unexpected_bootstrap,
            lease=stale_lease,
        )

    assert error.value.code == "RUNTIME_NOT_CONNECTED"
    assert previous_transport_recorder.close_calls == 1
    assert runtime.transport is replacement_transport


def test_rebootstrap_transport_installs_result_and_updates_capabilities(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    runtime.status = RuntimeStatus.READY
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=False,
        action_kinds=["tap"],
    )
    configs: list[ConnectionConfig] = []
    bootstrap_transport = _transport()
    bootstrap_result = _bootstrap_result(bootstrap_transport)

    def _bootstrap(config: ConnectionConfig) -> BootstrapResult:
        configs.append(config)
        return bootstrap_result

    transport = kernel.rebootstrap_transport(runtime, bootstrap=_bootstrap)

    assert transport is bootstrap_transport
    assert runtime.transport is bootstrap_transport
    assert runtime.connection == bootstrap_result.connection
    assert runtime.device_capabilities == bootstrap_result.meta.capabilities
    assert configs == [
        ConnectionConfig(
            mode=ConnectionMode.ADB,
            token="device-token",
            serial="emulator-5554",
        )
    ]
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert runtime.status is RuntimeStatus.READY
    assert "connection" not in persisted
    assert "deviceToken" not in persisted


def test_commit_transport_rebootstrap_closes_bootstrap_transport_when_lease_is_stale(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    stale_lease = kernel.capture_lifecycle_lease(runtime)
    assert kernel.invalidate_runtime(runtime, lease=stale_lease) is True

    bootstrap_transport_recorder = _CloseRecorder()
    transport = kernel.commit_transport_rebootstrap(
        runtime,
        stale_lease,
        bootstrap_result=_bootstrap_result(_transport(bootstrap_transport_recorder)),
    )

    assert transport is None
    assert bootstrap_transport_recorder.close_calls == 1
    assert runtime.transport is None
    assert runtime.connection is None


def test_fail_connect_marks_runtime_broken_and_persists_runtime_only(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()
    lease = kernel.capture_lifecycle_lease(runtime)

    assert (
        kernel.begin_connect(
            runtime,
            lease,
            transport=_transport(transport_recorder),
        )
        is True
    )
    assert kernel.fail_connect(runtime, lease) is True

    assert transport_recorder.close_calls == 1
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.transport is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"


def test_invalidate_device_credentials_clears_credentials_and_persists_broken(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.lifecycle_revision = 7
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["tap"],
    )
    runtime.transport = _transport(transport_recorder)
    runtime.latest_snapshot = _snapshot(2)
    runtime.previous_snapshot = _snapshot(1)
    runtime.current_screen_id = "screen-00002"
    runtime.screen_state = ScreenState(public_screen=None)
    runtime.ref_registry.bindings["n1"] = cast(Any, object())
    lease = kernel.capture_lifecycle_lease(runtime)

    assert kernel.invalidate_device_credentials(runtime, lease) is True

    assert transport_recorder.close_calls == 1
    assert runtime.status is RuntimeStatus.BROKEN
    assert runtime.lifecycle_revision == 7
    assert cast(Any, runtime).transport is None
    assert cast(Any, runtime).connection is None
    assert cast(Any, runtime).device_token is None
    assert cast(Any, runtime).device_capabilities is None
    assert cast(Any, runtime).latest_snapshot is None
    assert cast(Any, runtime).previous_snapshot is None
    assert cast(Any, runtime).current_screen_id is None
    assert cast(Any, runtime).screen_state is None
    assert runtime.ref_registry.bindings == {}
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert "currentScreenId" not in persisted
    assert "connection" not in persisted
    assert "deviceToken" not in persisted


def test_invalidate_device_credentials_ignores_stale_lease(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.lifecycle_revision = 2
    stale_lease = kernel.capture_lifecycle_lease(runtime)
    runtime.lifecycle_revision = 3
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.transport = _transport(transport_recorder)
    runtime.current_screen_id = "screen-00002"

    assert kernel.invalidate_device_credentials(runtime, stale_lease) is False

    assert transport_recorder.close_calls == 0
    assert runtime.status is RuntimeStatus.READY
    assert runtime.lifecycle_revision == 3
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert runtime.transport is not None
    assert runtime.current_screen_id == "screen-00002"
    assert runtime.runtime_path.exists() is False


def test_ensure_runtime_uses_canonical_runtime_shape_without_hydration_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)

    def _unexpected_hydration(*args: object, **kwargs: object) -> object:
        raise AssertionError("hydrate_runtime should not be called")

    monkeypatch.setattr(
        runtime_kernel_module,
        "hydrate_runtime",
        _unexpected_hydration,
        raising=False,
    )

    runtime = kernel.ensure_runtime()

    assert runtime is runtime_store.get_runtime()
    assert runtime.status is RuntimeStatus.NEW


def test_progress_lane_policy_surfaces_runtime_busy_for_query_timeout(
    tmp_path: Path,
) -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(
        runtime_store,
        time_fn=lambda: now[0],
        sleep_fn=sleep,
    )
    runtime = runtime_store.get_runtime()

    kernel.acquire_progress_lane(runtime, occupant_kind="untracked")
    try:
        with pytest.raises(DaemonError) as progress_error:
            kernel.acquire_progress_lane(
                runtime,
                occupant_kind="tracked",
            )
        with pytest.raises(DaemonError) as error:
            kernel.acquire_query_lane(runtime)
    finally:
        kernel.release_progress_lane(runtime)

    assert progress_error.value.code == "RUNTIME_BUSY"
    assert "clientCommandId" not in progress_error.value.details
    assert error.value.code == "RUNTIME_BUSY"
    assert "clientCommandId" not in error.value.details
    assert sleeps


def test_progress_lane_uses_canonical_runtime_shape_without_hydration_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    def _unexpected_hydration(*args: object, **kwargs: object) -> object:
        raise AssertionError("hydrate_runtime should not be called")

    monkeypatch.setattr(
        runtime_kernel_module,
        "hydrate_runtime",
        _unexpected_hydration,
        raising=False,
    )

    kernel.acquire_progress_lane(runtime, occupant_kind="tracked")
    try:
        assert runtime.progress_occupant_kind == "tracked"
    finally:
        kernel.release_progress_lane(runtime)


def test_progress_lane_busy_details_do_not_echo_client_command_id(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    kernel.acquire_progress_lane(
        runtime,
        occupant_kind="tracked",
    )
    try:
        with pytest.raises(DaemonError) as error:
            kernel.acquire_progress_lane(
                runtime,
                occupant_kind="untracked",
            )
    finally:
        kernel.release_progress_lane(runtime)

    assert error.value.code == "RUNTIME_BUSY"
    assert "clientCommandId" not in error.value.details


def test_drop_current_screen_authority_discard_transport_clears_capabilities(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00002"
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["tap"],
    )
    runtime.transport = _transport(transport_recorder)
    runtime.latest_snapshot = _snapshot(2)
    runtime.previous_snapshot = _snapshot(1)
    runtime.screen_state = ScreenState(public_screen=None)
    lease = kernel.capture_lifecycle_lease(runtime)

    assert kernel.drop_current_screen_authority(
        runtime,
        lease,
        discard_transport=True,
    )

    assert transport_recorder.close_calls == 1
    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.connection is not None
    assert runtime.device_token == "device-token"
    assert cast(Any, runtime).transport is None
    assert cast(Any, runtime).device_capabilities is None
    assert cast(Any, runtime).latest_snapshot is None
    assert cast(Any, runtime).previous_snapshot is None
    assert cast(Any, runtime).current_screen_id is None
    assert cast(Any, runtime).screen_state is None


def test_close_runtime_clears_live_state_records_and_persists_closed(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.lifecycle_revision = 7
    runtime.current_screen_id = "screen-00002"
    runtime.connection = ConnectionSpec(
        mode=ConnectionMode.ADB,
        serial="emulator-5554",
    )
    runtime.device_token = "device-token"
    runtime.device_capabilities = DeviceCapabilities(
        supports_events_poll=True,
        supports_screenshot=True,
        action_kinds=["tap"],
    )
    runtime.transport = _transport(transport_recorder)
    runtime.latest_snapshot = _snapshot(2)
    runtime.previous_snapshot = _snapshot(1)
    runtime.screen_state = ScreenState(public_screen=None)

    kernel.close_runtime(runtime)

    assert transport_recorder.close_calls == 1
    assert runtime.status is RuntimeStatus.CLOSED
    assert runtime.lifecycle_revision == 8
    assert cast(Any, runtime).transport is None
    assert cast(Any, runtime).connection is None
    assert cast(Any, runtime).device_token is None
    assert cast(Any, runtime).device_capabilities is None
    assert cast(Any, runtime).latest_snapshot is None
    assert cast(Any, runtime).previous_snapshot is None
    assert cast(Any, runtime).current_screen_id is None
    assert cast(Any, runtime).screen_state is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "closed"
    assert "currentScreenId" not in persisted


def test_close_runtime_does_not_close_released_transport_twice(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()
    runtime.transport = _transport(transport_recorder)

    kernel.close_runtime(runtime)
    kernel.close_runtime(runtime)

    assert transport_recorder.close_calls == 1


def test_close_runtime_ignores_busy_progress_lane(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    kernel.acquire_progress_lane(runtime, occupant_kind="tracked")
    try:
        kernel.close_runtime(runtime)
        with pytest.raises(DaemonError):
            kernel.acquire_progress_lane(runtime, occupant_kind="untracked")
    finally:
        kernel.release_progress_lane(runtime)

    assert runtime.status is RuntimeStatus.CLOSED


def test_invalidate_runtime_preserves_busy_owner_until_lane_release(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    kernel.acquire_progress_lane(
        runtime,
        occupant_kind="tracked",
    )
    try:
        assert kernel.invalidate_runtime(runtime) is True
        with pytest.raises(DaemonError) as error:
            kernel.acquire_progress_lane(
                runtime,
                occupant_kind="untracked",
            )
        assert "clientCommandId" not in error.value.details
    finally:
        kernel.release_progress_lane(runtime)


def test_invalidate_runtime_remains_live_only_and_does_not_persist(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    transport_recorder = _CloseRecorder()

    runtime.status = RuntimeStatus.READY
    runtime.current_screen_id = "screen-00002"
    kernel.commit_runtime(runtime)
    runtime.transport = _transport(transport_recorder)

    assert kernel.invalidate_runtime(runtime) is True

    assert transport_recorder.close_calls == 1
    assert runtime.lifecycle_revision == 1
    assert cast(Any, runtime).current_screen_id is None
    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert "currentScreenId" not in persisted


def test_attach_screenshot_artifact_preserves_current_screen(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    snapshot = _snapshot(1)
    compiled_screen = SemanticCompiler().compile(1, snapshot)
    public_screen = compiled_screen.to_public_screen()
    artifacts = ScreenArtifacts(
        screen_json=(runtime.artifact_root / "screens" / "obs-00001.json").as_posix()
    )
    runtime.current_screen_id = public_screen.screen_id
    runtime.latest_snapshot = snapshot
    runtime.screen_state = ScreenState(
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )
    lease = kernel.capture_lifecycle_lease(runtime)

    attachment = kernel.attach_screenshot_artifact(
        runtime,
        lease,
        screenshot_png="/tmp/shot.png",
    )

    assert attachment is not None
    assert attachment.current_screen is public_screen
    assert attachment.artifacts.screen_json == artifacts.screen_json
    assert attachment.artifacts.screenshot_png == "/tmp/shot.png"
    assert runtime.current_screen_id == public_screen.screen_id
    assert runtime.latest_snapshot is snapshot
    assert runtime.screen_state is not None
    assert runtime.screen_state.public_screen is public_screen
    assert runtime.screen_state.compiled_screen is compiled_screen
    assert runtime.screen_state.artifacts is attachment.artifacts


def test_attach_screenshot_artifact_rejects_stale_lease_without_mutation(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    stale_lease = kernel.capture_lifecycle_lease(runtime)
    assert kernel.invalidate_runtime(runtime, lease=stale_lease) is True
    compiled_screen = SemanticCompiler().compile(1, _snapshot(1))
    public_screen = compiled_screen.to_public_screen()
    artifacts = ScreenArtifacts(screen_json="/tmp/screen.json")
    runtime.screen_state = ScreenState(
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )

    attachment = kernel.attach_screenshot_artifact(
        runtime,
        stale_lease,
        screenshot_png="/tmp/stale-shot.png",
    )

    assert attachment is None
    assert runtime.screen_state is not None
    assert runtime.screen_state.public_screen is public_screen
    assert runtime.screen_state.compiled_screen is compiled_screen
    assert runtime.screen_state.artifacts is artifacts
    assert runtime.screen_state.artifacts.screenshot_png is None


def test_attach_screenshot_artifact_without_current_screen_keeps_failure_state(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    lease = kernel.capture_lifecycle_lease(runtime)

    attachment = kernel.attach_screenshot_artifact(
        runtime,
        lease,
        screenshot_png="/tmp/shot-without-screen.png",
    )

    assert attachment is not None
    assert attachment.current_screen is None
    assert attachment.artifacts.screen_json is None
    assert attachment.artifacts.screenshot_png == "/tmp/shot-without-screen.png"
    assert runtime.screen_state is not None
    assert runtime.screen_state.public_screen is None
    assert runtime.screen_state.compiled_screen is None
    assert runtime.screen_state.artifacts is attachment.artifacts


def test_attach_screenshot_artifact_does_not_write_runtime_json(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    lease = kernel.capture_lifecycle_lease(runtime)

    assert runtime.runtime_path.exists() is False

    attachment = kernel.attach_screenshot_artifact(
        runtime,
        lease,
        screenshot_png="/tmp/live-only-shot.png",
    )

    assert attachment is not None
    assert runtime.runtime_path.exists() is False


def test_commit_screen_refresh_applies_named_screen_state_boundary(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    previous_snapshot = _snapshot(1)
    older_snapshot = _snapshot(0)
    next_snapshot = _snapshot(2)
    compiled_screen = SemanticCompiler().compile(3, next_snapshot)
    public_screen = compiled_screen.to_public_screen()
    ref_registry = RefRegistry()
    artifacts, staged_artifacts, staged_path, final_path = _staged_screen_artifacts(
        tmp_path
    )

    runtime.status = RuntimeStatus.CONNECTED
    runtime.screen_sequence = 2
    runtime.current_screen_id = "screen-00002"
    runtime.latest_snapshot = previous_snapshot
    runtime.previous_snapshot = older_snapshot
    runtime.screen_state = ScreenState(public_screen=None)

    kernel.commit_screen_refresh(
        runtime,
        update=ScreenRefreshUpdate(
            sequence=compiled_screen.sequence,
            snapshot=next_snapshot,
            public_screen=public_screen,
            compiled_screen=compiled_screen,
            artifacts=artifacts,
            ref_registry=ref_registry,
            staged_artifacts=staged_artifacts,
        ),
    )

    assert runtime.status is RuntimeStatus.READY
    assert runtime.previous_snapshot is previous_snapshot
    assert runtime.latest_snapshot is next_snapshot
    assert runtime.screen_sequence == compiled_screen.sequence
    assert runtime.current_screen_id == public_screen.screen_id
    assert runtime.ref_registry is ref_registry
    assert runtime.screen_state is not None
    assert runtime.screen_state.public_screen is public_screen
    assert runtime.screen_state.compiled_screen is compiled_screen
    assert runtime.screen_state.artifacts is artifacts
    assert staged_path.exists() is False
    assert final_path.is_file()


def test_commit_screen_refresh_rolls_back_state_and_staged_artifacts_on_persist_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    previous_snapshot = _snapshot(1)
    older_snapshot = _snapshot(0)
    next_snapshot = _snapshot(2)
    previous_screen_state = ScreenState(public_screen=None)
    previous_ref_registry = RefRegistry()
    compiled_screen = SemanticCompiler().compile(3, next_snapshot)
    public_screen = compiled_screen.to_public_screen()
    ref_registry = RefRegistry()
    artifacts, staged_artifacts, staged_path, final_path = _staged_screen_artifacts(
        tmp_path
    )

    runtime.status = RuntimeStatus.CONNECTED
    runtime.screen_sequence = 2
    runtime.current_screen_id = "screen-00002"
    runtime.latest_snapshot = previous_snapshot
    runtime.previous_snapshot = older_snapshot
    runtime.screen_state = previous_screen_state
    runtime.ref_registry = previous_ref_registry

    def _fail_persist(_runtime: object) -> None:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(kernel, "commit_runtime", _fail_persist)

    with pytest.raises(RuntimeError, match="persist failed"):
        kernel.commit_screen_refresh(
            runtime,
            update=ScreenRefreshUpdate(
                sequence=compiled_screen.sequence,
                snapshot=next_snapshot,
                public_screen=public_screen,
                compiled_screen=compiled_screen,
                artifacts=artifacts,
                ref_registry=ref_registry,
                staged_artifacts=staged_artifacts,
            ),
        )

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.screen_sequence == 2
    assert runtime.current_screen_id == "screen-00002"
    assert runtime.latest_snapshot is previous_snapshot
    assert runtime.previous_snapshot is older_snapshot
    assert runtime.screen_state is previous_screen_state
    assert runtime.ref_registry is previous_ref_registry
    assert staged_path.exists() is False
    assert final_path.exists() is False


def test_commit_screen_refresh_restores_existing_artifact_on_persist_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    previous_snapshot = _snapshot(1)
    next_snapshot = _snapshot(2)
    previous_screen_state = ScreenState(public_screen=None)
    previous_ref_registry = RefRegistry()
    compiled_screen = SemanticCompiler().compile(3, next_snapshot)
    public_screen = compiled_screen.to_public_screen()
    ref_registry = RefRegistry()
    final_path = runtime.artifact_root / "screens" / "obs-00003.json"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text('{"existing": true}\n', encoding="utf-8")
    original_content = final_path.read_text(encoding="utf-8")
    final_xml_path = runtime.artifact_root / "artifacts" / "screens" / "obs-00003.xml"
    final_xml_path.parent.mkdir(parents=True, exist_ok=True)
    final_xml_path.write_text('<screen screenId="old" />\n', encoding="utf-8")
    original_xml_content = final_xml_path.read_text(encoding="utf-8")
    staged_artifacts = ArtifactWriter().stage_screen(
        runtime,
        public_screen,
        sequence=compiled_screen.sequence,
        source_snapshot_id=compiled_screen.source_snapshot_id,
        captured_at=compiled_screen.captured_at,
        ref_registry=ref_registry,
    )

    runtime.status = RuntimeStatus.CONNECTED
    runtime.screen_sequence = 2
    runtime.current_screen_id = "screen-00002"
    runtime.latest_snapshot = previous_snapshot
    runtime.screen_state = previous_screen_state
    runtime.ref_registry = previous_ref_registry

    def _fail_persist(_runtime: object) -> None:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(kernel, "commit_runtime", _fail_persist)

    with pytest.raises(RuntimeError, match="persist failed"):
        kernel.commit_screen_refresh(
            runtime,
            update=ScreenRefreshUpdate(
                sequence=compiled_screen.sequence,
                snapshot=next_snapshot,
                public_screen=public_screen,
                compiled_screen=compiled_screen,
                artifacts=staged_artifacts.artifacts,
                ref_registry=ref_registry,
                staged_artifacts=staged_artifacts,
            ),
        )

    assert runtime.status is RuntimeStatus.CONNECTED
    assert runtime.screen_sequence == 2
    assert runtime.current_screen_id == "screen-00002"
    assert runtime.latest_snapshot is previous_snapshot
    assert runtime.screen_state is previous_screen_state
    assert runtime.ref_registry is previous_ref_registry
    assert final_path.read_text(encoding="utf-8") == original_content
    assert final_xml_path.read_text(encoding="utf-8") == original_xml_content
    assert list(final_path.parent.glob("*.tmp-*")) == []
    assert list(final_path.parent.glob("*.bak-*")) == []
    assert list(final_xml_path.parent.glob("*.tmp-*")) == []
    assert list(final_xml_path.parent.glob("*.bak-*")) == []


def test_commit_screen_refresh_discards_staged_artifacts_on_stale_pre_commit(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()
    next_snapshot = _snapshot(2)
    compiled_screen = SemanticCompiler().compile(1, next_snapshot)
    public_screen = compiled_screen.to_public_screen()
    artifacts, staged_artifacts, staged_path, final_path = _staged_screen_artifacts(
        tmp_path
    )

    def _raise_stale(_runtime: object) -> None:
        raise DaemonError(
            code=DaemonErrorCode.COMMAND_CANCELLED,
            message="command was canceled",
            retryable=False,
        )

    with pytest.raises(DaemonError) as error:
        kernel.commit_screen_refresh(
            runtime,
            update=ScreenRefreshUpdate(
                sequence=compiled_screen.sequence,
                snapshot=next_snapshot,
                public_screen=public_screen,
                compiled_screen=compiled_screen,
                artifacts=artifacts,
                ref_registry=RefRegistry(),
                staged_artifacts=staged_artifacts,
            ),
            pre_commit=_raise_stale,
        )

    assert error.value.code is DaemonErrorCode.COMMAND_CANCELLED
    assert runtime.status is RuntimeStatus.NEW
    assert runtime.latest_snapshot is None
    assert runtime.screen_state is None
    assert staged_path.exists() is False
    assert final_path.exists() is False


def test_committed_runtime_mutation_persists_runtime_by_default(tmp_path: Path) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    with kernel.committed_runtime_mutation(runtime):
        runtime.status = RuntimeStatus.READY

    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert runtime.status is RuntimeStatus.READY


def test_committed_runtime_mutation_persists_without_hydration_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    def _unexpected_hydration(*args: object, **kwargs: object) -> object:
        raise AssertionError("hydrate_runtime should not be called")

    monkeypatch.setattr(
        runtime_kernel_module,
        "hydrate_runtime",
        _unexpected_hydration,
        raising=False,
    )

    with kernel.committed_runtime_mutation(runtime):
        runtime.status = RuntimeStatus.READY

    persisted = json.loads(runtime.runtime_path.read_text())
    assert persisted["status"] == "broken"
    assert runtime.status is RuntimeStatus.READY


def test_committed_runtime_mutation_rolls_back_under_kernel_boundary(
    tmp_path: Path,
) -> None:
    runtime_store = runtime_store_for_workspace(tmp_path)
    kernel = RuntimeKernel(runtime_store)
    runtime = runtime_store.get_runtime()

    with (
        pytest.raises(RuntimeError, match="persist failed"),
        kernel.committed_runtime_mutation(
            runtime,
            persist=lambda _: (_ for _ in ()).throw(RuntimeError("persist failed")),
            rollback=lambda active_runtime: setattr(
                active_runtime,
                "status",
                RuntimeStatus.NEW,
            ),
        ),
    ):
        runtime.status = RuntimeStatus.READY

    assert runtime.status is RuntimeStatus.NEW
    assert runtime.runtime_path.exists() is False
