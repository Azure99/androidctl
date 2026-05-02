"""active.json persistence for androidctld."""

from __future__ import annotations

import json
import os
import platform
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from androidctl_contracts.user_state import ActiveDaemonRecord
from androidctld.auth.secret_files import write_secret_json_file_atomically
from androidctld.config import DaemonConfig, normalize_loopback_host
from androidctld.daemon.ownership_probe import (
    OwnershipHealthProbe,
    OwnershipHealthProbeResult,
    OwnershipHealthStatus,
)
from androidctld.schema.persistence import (
    ActiveDaemonFile,
    build_persistence_model,
)

__all__ = ["ActiveDaemonRecord", "ActiveDaemonRegistry"]


class ActiveDaemonRegistry:
    _LOCK_ACQUIRE_TIMEOUT_SECONDS = 2.0
    _LOCK_RETRY_INTERVAL_SECONDS = 0.01
    _LOCK_STALE_SECONDS = 30.0

    def __init__(
        self,
        config: DaemonConfig,
        *,
        live_checker: (
            Callable[[ActiveDaemonRecord], OwnershipHealthProbeResult] | None
        ) = None,
    ) -> None:
        self._config = config
        self._live_checker = live_checker or self._default_live_checker

    def build_record(self, host: str, port: int, token: str) -> ActiveDaemonRecord:
        pid = os.getpid()
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return ActiveDaemonRecord(
            pid=pid,
            host=normalize_loopback_host(host),
            port=port,
            token=token,
            started_at=started_at,
            workspace_root=self._config.workspace_root.as_posix(),
            owner_id=self._config.owner_id,
        )

    def publish(self, record: ActiveDaemonRecord) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        with self._active_lock():
            existing = self.read()
            if existing is not None and self._active_record_conflicts(
                existing,
                record,
            ):
                raise RuntimeError("live daemon already owns active slot")
            payload = self._build_active_file_payload(record)
            self._write_active_file_atomically(payload)

    def clear(self, *, record: ActiveDaemonRecord | None = None) -> None:
        with self._active_lock():
            if record is not None:
                try:
                    active_file = self._read_active_file_model()
                except FileNotFoundError:
                    return
                except OSError:
                    return
                except (ValueError, json.JSONDecodeError, ValidationError):
                    pass
                else:
                    if (
                        active_file.pid != record.pid
                        or active_file.started_at != record.started_at
                    ):
                        return
            try:
                self._config.active_file_path.unlink()
            except FileNotFoundError:
                return

    def restore(self, record: ActiveDaemonRecord) -> None:
        payload = self._build_active_file_payload(record)
        with self._active_lock():
            self._write_active_file_atomically(payload)

    def read(self) -> ActiveDaemonRecord | None:
        if not self._config.active_file_path.exists():
            return None
        try:
            record = self._read_active_file_model()
        except OSError:
            return None
        except (ValueError, json.JSONDecodeError, ValidationError):
            with suppress(OSError):
                self._config.active_file_path.unlink()
            return None
        return ActiveDaemonRecord(
            pid=record.pid,
            host=record.host,
            port=record.port,
            token=record.token,
            started_at=record.started_at,
            workspace_root=record.workspace_root,
            owner_id=record.owner_id,
        )

    def _active_record_conflicts(
        self,
        existing: ActiveDaemonRecord,
        record: ActiveDaemonRecord,
    ) -> bool:
        if existing.identity == record.identity:
            return False
        probe_result = self._live_checker(existing)
        if probe_result.is_live:
            return True
        if probe_result.status == OwnershipHealthStatus.UNREACHABLE:
            return False
        return self._is_pid_live(existing.pid)

    def _default_live_checker(
        self,
        record: ActiveDaemonRecord,
    ) -> OwnershipHealthProbeResult:
        return OwnershipHealthProbe().probe_active_record(
            record,
            expected_workspace_root=self._config.workspace_root.as_posix(),
            expected_owner_id=self._config.owner_id,
        )

    def _read_active_file_model(self) -> ActiveDaemonFile:
        with self._config.active_file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return ActiveDaemonFile.model_validate(payload)

    @staticmethod
    def _build_active_file_payload(record: ActiveDaemonRecord) -> dict[str, object]:
        return cast(
            dict[str, object],
            build_persistence_model(
                ActiveDaemonFile,
                pid=record.pid,
                host=normalize_loopback_host(record.host),
                port=record.port,
                token=record.token,
                started_at=record.started_at,
                workspace_root=record.workspace_root,
                owner_id=record.owner_id,
            ).model_dump(by_alias=True, mode="json"),
        )

    @staticmethod
    def _is_pid_live(pid: int) -> bool:
        if pid <= 0:
            return False
        if platform.system() == "Windows":
            return ActiveDaemonRegistry._windows_is_pid_live(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _windows_is_pid_live(pid: int, kernel32: Any | None = None) -> bool:
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        if kernel32 is None:
            windll = getattr(ctypes, "windll", None)
            if windll is None:
                return False
            kernel32 = windll.kernel32

        process_handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not process_handle:
            return False

        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(process_handle)

    @contextmanager
    def _active_lock(self) -> Iterator[None]:
        lock_path = self._config.active_lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._LOCK_ACQUIRE_TIMEOUT_SECONDS
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
                break
            except FileExistsError:
                if self._recover_stale_lock(lock_path):
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError("timed out acquiring active slot lock") from None
                time.sleep(self._LOCK_RETRY_INTERVAL_SECONDS)
            finally:
                if fd is not None:
                    os.close(fd)
        try:
            yield
        finally:
            with suppress(FileNotFoundError):
                lock_path.unlink()

    def _recover_stale_lock(self, lock_path: Path) -> bool:
        try:
            raw = lock_path.read_text(encoding="utf-8").strip()
            pid = int(raw) if raw else -1
        except (OSError, ValueError):
            pid = -1
        if pid > 0 and self._is_pid_live(pid):
            return False
        try:
            lock_age_seconds = time.time() - lock_path.stat().st_mtime
        except OSError:
            return False
        if pid <= 0 and lock_age_seconds < self._LOCK_STALE_SECONDS:
            return False
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _write_active_file_atomically(self, payload: dict[str, object]) -> None:
        write_secret_json_file_atomically(self._config.active_file_path, payload)
