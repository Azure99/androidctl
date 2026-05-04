from __future__ import annotations

import ipaddress
import json
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

import httpx
from pydantic import ValidationError

from androidctl.daemon.client import (
    DaemonApiError,
    DaemonClient,
    DaemonProtocolError,
    IncompatibleDaemonError,
    try_get_healthy_daemon,
)
from androidctl.daemon.launcher import resolve_launch_spec
from androidctl.daemon.owner import derive_owner_id
from androidctl_contracts.paths import daemon_state_root
from androidctl_contracts.user_state import ActiveDaemonRecord


def resolve_daemon_client(
    *,
    workspace_root: Path,
    cwd: Path,
    env: Mapping[str, str],
) -> DaemonClient:
    resolved_workspace_root = workspace_root.resolve()
    owner_id = derive_owner_id(env=env)
    existing = _healthy_client_from_record(
        _read_active_daemon_record(resolved_workspace_root),
        workspace_root=resolved_workspace_root,
        owner_id=owner_id,
    )
    if existing is not None:
        return existing

    _launch_daemon_process(
        cwd=cwd,
        env=env,
        workspace_root=resolved_workspace_root,
        owner_id=owner_id,
    )
    launched = _wait_for_healthy_client(
        workspace_root=resolved_workspace_root,
        owner_id=owner_id,
        timeout_seconds=5.0,
    )
    if launched is not None:
        return launched
    raise RuntimeError("failed to discover or launch a healthy androidctld daemon")


def discover_existing_daemon_client(
    *,
    workspace_root: Path,
    env: Mapping[str, str],
) -> DaemonClient | None:
    resolved_workspace_root = workspace_root.resolve()
    owner_id = derive_owner_id(env=env)
    return _healthy_client_from_record(
        _read_active_daemon_record(resolved_workspace_root),
        workspace_root=resolved_workspace_root,
        owner_id=owner_id,
    )


def _healthy_client_from_record(
    record: ActiveDaemonRecord | None,
    *,
    workspace_root: Path,
    owner_id: str,
) -> DaemonClient | None:
    if record is None:
        return None
    if record.workspace_root != workspace_root.as_posix():
        return None
    if not _is_loopback_host(record.host):
        raise RuntimeError(f"active daemon host must be loopback: {record.host!r}")
    client = DaemonClient.from_active_record(record, owner_id=owner_id)
    if record.owner_id != owner_id:
        try:
            client.health(record)
        except IncompatibleDaemonError as error:
            raise DaemonApiError(
                code="WORKSPACE_BUSY",
                message="workspace daemon is owned by a different shell or agent",
                details={"ownerId": record.owner_id},
            ) from error
        except DaemonApiError as error:
            if error.code == "WORKSPACE_BUSY":
                raise error
            return None
        except (DaemonProtocolError, httpx.RequestError, httpx.HTTPStatusError):
            return None
        raise DaemonApiError(
            code="WORKSPACE_BUSY",
            message="workspace daemon is owned by a different shell or agent",
            details={"ownerId": record.owner_id},
        )
    try:
        health = try_get_healthy_daemon(client, record)
    except IncompatibleDaemonError:
        raise
    except DaemonProtocolError:
        return None
    if health is None:
        return None
    return client


def _wait_for_healthy_client(
    *,
    workspace_root: Path,
    owner_id: str,
    timeout_seconds: float,
) -> DaemonClient | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        client = _healthy_client_from_record(
            _read_active_daemon_record(workspace_root),
            workspace_root=workspace_root,
            owner_id=owner_id,
        )
        if client is not None:
            return client
        time.sleep(0.05)
    return None


def _launch_daemon_process(
    *,
    cwd: Path,
    env: Mapping[str, str],
    workspace_root: Path,
    owner_id: str,
) -> None:
    launch_spec = resolve_launch_spec(env=dict(env))
    process_env = dict(env)
    if launch_spec.env_overlay:
        process_env.update(launch_spec.env_overlay)
    log_dir = daemon_state_root(workspace_root) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    log_path = log_dir / f"androidctld-{timestamp}.log"
    log_file = log_path.open("a", buffering=1)
    subprocess.Popen(
        [
            launch_spec.executable,
            *launch_spec.argv,
            "--workspace-root",
            str(workspace_root),
            "--owner-id",
            owner_id,
        ],
        cwd=launch_spec.cwd or cwd,
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    log_file.close()


def _read_active_daemon_record(workspace_root: Path) -> ActiveDaemonRecord | None:
    active_path = daemon_state_root(workspace_root.resolve()) / "active.json"
    if not active_path.exists():
        return None
    try:
        payload = json.loads(active_path.read_text(encoding="utf-8"))
        return ActiveDaemonRecord.model_validate(payload)
    except (ValueError, json.JSONDecodeError, ValidationError):
        return None


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
