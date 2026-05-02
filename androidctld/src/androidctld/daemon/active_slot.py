"""Active-slot ownership and publication for androidctld."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from androidctld.auth.active_registry import ActiveDaemonRecord, ActiveDaemonRegistry
from androidctld.auth.secret_files import write_secret_json_file_atomically
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.config import DaemonConfig, normalize_loopback_host
from androidctld.daemon.ownership_probe import (
    OwnershipHealthProbe,
    OwnershipHealthProbeResult,
    OwnershipHealthStatus,
)


@dataclass(frozen=True)
class _OwnerLockRecord:
    pid: int
    host: str
    port: int
    started_at: str
    workspace_root: str
    owner_id: str

    @property
    def identity(self) -> tuple[int, str]:
        return (self.pid, self.started_at)


class ActiveSlotCoordinator:
    _OWNER_LOCK_STALE_SECONDS = 2.0

    def __init__(
        self,
        *,
        config: DaemonConfig,
        active_registry: ActiveDaemonRegistry,
        existing_token_reader: Callable[[], str | None] | None = None,
        ownership_probe: OwnershipHealthProbe | None = None,
    ) -> None:
        self._config = config
        self._active_registry = active_registry
        self._existing_token_reader = existing_token_reader or (
            lambda: DaemonTokenStore.load_existing_token(self._config.token_file_path)
        )
        self._ownership_probe = ownership_probe or OwnershipHealthProbe()
        self._active_record: ActiveDaemonRecord | None = None
        self._owns_owner_lock = False

    @property
    def active_record(self) -> ActiveDaemonRecord | None:
        return self._active_record

    def acquire(self) -> None:
        lock_path = self._config.owner_lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            fd: int | None = None
            try:
                fd = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
                fd = None
                self._owns_owner_lock = True
                return
            except FileExistsError:
                if self._recover_stale_owner_lock(lock_path):
                    continue
                self._restore_active_record_from_owner_lock()
                raise RuntimeError("live daemon already owns active slot") from None
            finally:
                if fd is not None:
                    os.close(fd)

    def prepare(self, *, host: str, port: int, token: str) -> ActiveDaemonRecord:
        record = self._active_registry.build_record(host=host, port=port, token=token)
        self._active_record = record
        return record

    def publish(self, record: ActiveDaemonRecord | None = None) -> ActiveDaemonRecord:
        active_record = record or self._active_record
        if active_record is None:
            raise RuntimeError("active record is not prepared")
        self._active_registry.publish(active_record)
        self._publish_owner_lock_record(active_record)
        self._active_record = active_record
        return active_record

    def clear_record(self) -> None:
        active_record = self._active_record
        if active_record is not None:
            self._active_registry.clear(record=active_record)
        self._active_record = None

    def release_owner(self) -> None:
        if not self._owns_owner_lock:
            return
        self._owns_owner_lock = False
        try:
            self._config.owner_lock_path.unlink()
        except FileNotFoundError:
            return

    def release(self) -> None:
        self.clear_record()
        self.release_owner()

    def _recover_stale_owner_lock(self, lock_path: Path) -> bool:
        try:
            lock_age_seconds = time.time() - lock_path.stat().st_mtime
        except OSError:
            return False
        if self._owner_lock_has_live_evidence(lock_path):
            return False
        if lock_age_seconds < self._OWNER_LOCK_STALE_SECONDS:
            pid = self._owner_lock_pid(lock_path)
            if pid <= 0 or ActiveDaemonRegistry._is_pid_live(pid):
                return False
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _owner_lock_has_live_evidence(self, lock_path: Path) -> bool:
        owner_lock = self._read_owner_lock_record(lock_path)
        if owner_lock is None:
            return False
        token_probe = self._probe_owner_lock(owner_lock)
        return token_probe.is_live

    def _publish_owner_lock_record(self, record: ActiveDaemonRecord) -> None:
        payload = {
            "host": normalize_loopback_host(record.host),
            "ownerId": record.owner_id,
            "pid": record.pid,
            "port": record.port,
            "startedAt": record.started_at,
            "workspaceRoot": record.workspace_root,
        }
        write_secret_json_file_atomically(self._config.owner_lock_path, payload)

    def _owner_lock_pid(self, lock_path: Path) -> int:
        try:
            raw = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return -1
        if not raw:
            return -1
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return -1
        if not isinstance(payload, dict):
            return -1
        pid = payload.get("pid")
        if isinstance(pid, int):
            return pid
        return -1

    def _restore_active_record_from_owner_lock(self) -> None:
        owner_lock = self._read_owner_lock_record(self._config.owner_lock_path)
        if owner_lock is None:
            return
        if not self._owner_lock_matches_current_config(owner_lock):
            return
        probe_result = self._probe_owner_lock(owner_lock)
        if probe_result.status != OwnershipHealthStatus.LIVE_MATCH:
            return
        token = probe_result.token
        if token is None:
            token = self._first_token_for_owner_lock(owner_lock)
        if token is None:
            return
        record = ActiveDaemonRecord(
            pid=owner_lock.pid,
            host=owner_lock.host,
            port=owner_lock.port,
            token=token,
            started_at=owner_lock.started_at,
            workspace_root=owner_lock.workspace_root,
            owner_id=owner_lock.owner_id,
        )
        try:
            self._active_registry.restore(record)
        except ValueError:
            return

    def _read_owner_lock_record(self, lock_path: Path) -> _OwnerLockRecord | None:
        try:
            raw = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            pid = self._read_int(payload.get("pid"))
            host = self._read_str(payload.get("host"))
            port = self._read_int(payload.get("port"))
            started_at = self._read_str(payload.get("startedAt"))
            workspace_root = self._read_str(payload.get("workspaceRoot"))
            owner_id = self._read_str(payload.get("ownerId"))
        except ValueError:
            return None
        return _OwnerLockRecord(
            pid=pid,
            host=host,
            port=port,
            started_at=started_at,
            workspace_root=workspace_root,
            owner_id=owner_id,
        )

    @staticmethod
    def _read_int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected integer")
        return value

    @staticmethod
    def _read_str(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("expected string")
        value = value.strip()
        if not value:
            raise ValueError("expected non-empty string")
        return value

    def _owner_lock_matches_current_config(self, record: _OwnerLockRecord) -> bool:
        if record.workspace_root != self._config.workspace_root.as_posix():
            return False
        if record.owner_id != self._config.owner_id:
            return False
        if self._normalize_owner_host(record.host) != self._config.host:
            return False
        return self._config.port == 0 or record.port == self._config.port

    def _probe_owner_lock(
        self,
        record: _OwnerLockRecord,
    ) -> OwnershipHealthProbeResult:
        tokens = self._tokens_for_owner_lock(record)
        return self._ownership_probe.probe(
            host=record.host,
            port=record.port,
            owner_id=record.owner_id,
            workspace_root=record.workspace_root,
            expected_workspace_root=self._config.workspace_root.as_posix(),
            expected_owner_id=self._config.owner_id,
            tokens=tokens,
        )

    def _tokens_for_owner_lock(self, record: _OwnerLockRecord) -> list[str]:
        tokens: list[str] = []
        token = self._existing_token_reader()
        if token is not None:
            tokens.append(token)
        active_record = self._active_registry.read()
        if active_record is not None and self._active_record_matches_owner_lock(
            active_record,
            record,
        ):
            tokens.append(active_record.token)
        return self._unique_tokens(tokens)

    def _first_token_for_owner_lock(self, record: _OwnerLockRecord) -> str | None:
        tokens = self._tokens_for_owner_lock(record)
        if not tokens:
            return None
        return tokens[0]

    @staticmethod
    def _unique_tokens(tokens: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            token = token.strip()
            if not token or token in seen:
                continue
            unique.append(token)
            seen.add(token)
        return unique

    @staticmethod
    def _normalize_owner_host(host: str) -> str:
        try:
            return normalize_loopback_host(host)
        except ValueError:
            return host

    @staticmethod
    def _active_record_matches_owner_lock(
        active_record: ActiveDaemonRecord,
        owner_lock: _OwnerLockRecord,
    ) -> bool:
        return (
            active_record.pid == owner_lock.pid
            and active_record.host == owner_lock.host
            and active_record.port == owner_lock.port
            and active_record.started_at == owner_lock.started_at
            and active_record.workspace_root == owner_lock.workspace_root
            and active_record.owner_id == owner_lock.owner_id
        )
