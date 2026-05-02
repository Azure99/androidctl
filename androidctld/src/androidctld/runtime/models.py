"""Workspace-scoped runtime models."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.device.types import (
    ConnectionSpec,
    DeviceCapabilities,
    RuntimeTransport,
)
from androidctld.protocol import RuntimeStatus
from androidctld.refs.models import RefRegistry
from androidctld.semantics.compiler import CompiledScreen
from androidctld.semantics.public_models import PublicScreen
from androidctld.snapshots.models import RawSnapshot


@dataclass
class ScreenState:
    public_screen: PublicScreen | None
    compiled_screen: CompiledScreen | None = None
    artifacts: ScreenArtifacts | None = None


@dataclass
class WorkspaceRuntime:
    workspace_root: Path
    artifact_root: Path
    runtime_path: Path
    status: RuntimeStatus = RuntimeStatus.NEW
    screen_sequence: int = 0
    current_screen_id: str | None = None
    connection: ConnectionSpec | None = field(default=None, repr=False)
    device_token: str | None = field(default=None, repr=False)
    device_capabilities: DeviceCapabilities | None = field(default=None, repr=False)
    transport: RuntimeTransport | None = field(default=None, repr=False)
    latest_snapshot: RawSnapshot | None = field(default=None, repr=False)
    previous_snapshot: RawSnapshot | None = field(default=None, repr=False)
    screen_state: ScreenState | None = field(default=None, repr=False)
    ref_registry: RefRegistry = field(default_factory=RefRegistry, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    progress_occupant_kind: str | None = field(default=None, repr=False)
    lifecycle_revision: int = field(default=0, repr=False)
