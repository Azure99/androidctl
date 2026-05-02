from __future__ import annotations

from pathlib import Path

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.device.types import ConnectionSpec
from androidctld.protocol import ConnectionMode, RuntimeStatus
from androidctld.runtime.models import ScreenState, WorkspaceRuntime
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot


def build_runtime(
    workspace_root: Path,
    *,
    status: RuntimeStatus = RuntimeStatus.READY,
    screen_sequence: int = 0,
    current_screen_id: str | None = None,
    connection: ConnectionSpec | None = None,
    device_token: str | None = None,
) -> WorkspaceRuntime:
    artifact_root = workspace_root / ".androidctl"
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=artifact_root,
        runtime_path=artifact_root / "runtime.json",
        status=status,
        screen_sequence=screen_sequence,
        current_screen_id=current_screen_id,
    )
    runtime.connection = connection
    runtime.device_token = device_token
    return runtime


def build_connected_runtime(
    workspace_root: Path,
    *,
    status: RuntimeStatus = RuntimeStatus.READY,
    screen_sequence: int = 0,
    current_screen_id: str | None = None,
    serial: str = "emulator-5554",
    device_token: str = "device-token",
) -> WorkspaceRuntime:
    return build_runtime(
        workspace_root,
        status=status,
        screen_sequence=screen_sequence,
        current_screen_id=current_screen_id,
        connection=ConnectionSpec(mode=ConnectionMode.ADB, serial=serial),
        device_token=device_token,
    )


def build_artifact_path(
    runtime: WorkspaceRuntime,
    *,
    stem: str,
    extension: str,
    namespace: str = "artifacts",
) -> str:
    return (runtime.artifact_root / namespace / f"{stem}.{extension}").as_posix()


def build_screen_artifacts(
    runtime: WorkspaceRuntime,
    *,
    screen_id: str,
) -> ScreenArtifacts:
    return ScreenArtifacts(
        screen_json=build_artifact_path(
            runtime,
            stem=screen_id,
            extension="json",
            namespace="screens",
        ),
    )


def install_screen_state(
    runtime: WorkspaceRuntime,
    *,
    snapshot: RawSnapshot | None = None,
    public_screen: PublicScreen | None,
    compiled_screen: CompiledScreen | None = None,
    artifacts: ScreenArtifacts | None = None,
) -> None:
    if snapshot is not None:
        runtime.latest_snapshot = snapshot
    if public_screen is not None:
        runtime.current_screen_id = public_screen.screen_id
    if compiled_screen is not None:
        runtime.screen_sequence = compiled_screen.sequence
    runtime.screen_state = ScreenState(
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )
