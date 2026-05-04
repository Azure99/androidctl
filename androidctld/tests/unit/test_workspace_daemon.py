from __future__ import annotations

import json
import logging
import os
import socket
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import androidctld
import androidctld.__main__ as androidctld_main
import androidctld.daemon.http_host as http_host_module
from androidctl_contracts.daemon_api import OWNER_HEADER_NAME
from androidctl_contracts.paths import daemon_state_root
from androidctld.__main__ import build_arg_parser
from androidctld.auth.active_registry import ActiveDaemonRegistry
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.config import ACTIVE_FILE_NAME, TOKEN_HEADER_NAME, DaemonConfig
from androidctld.daemon.active_slot import ActiveSlotCoordinator
from androidctld.daemon.http_host import DaemonHttpHost
from androidctld.daemon.ownership_probe import (
    OwnershipHealthProbe,
    OwnershipHealthProbeResult,
    OwnershipHealthStatus,
)
from androidctld.daemon.server import AndroidctldHttpServer


class FakeOwnershipHealthProbe:
    def __init__(self, result: OwnershipHealthProbeResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def probe(self, **kwargs: object) -> OwnershipHealthProbeResult:
        self.calls.append(dict(kwargs))
        return self.result


def _file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_posix_file_mode(path: Path, expected: int) -> None:
    if os.name == "posix":
        assert _file_mode(path) == expected


def _chmod_posix(path: Path, mode: int) -> None:
    if os.name == "posix":
        os.chmod(path, mode)


def test_daemon_config_scopes_state_dir_to_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-1",
        host="127.0.0.1",
        port=0,
    )

    resolved_root = workspace_root.resolve()
    expected_state_dir = daemon_state_root(resolved_root)
    assert config.state_dir == expected_state_dir
    assert config.active_file_path == expected_state_dir / ACTIVE_FILE_NAME


def test_active_registry_persists_workspace_and_owner(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-2",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)

    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    payload = json.loads(config.active_file_path.read_text(encoding="utf-8"))
    assert payload["workspaceRoot"] == workspace_root.resolve().as_posix()
    assert payload["ownerId"] == "owner-2"

    round_tripped = registry.read()
    assert round_tripped is not None
    assert record.workspace_root == round_tripped.workspace_root
    assert record.owner_id == round_tripped.owner_id


def test_secret_state_files_are_written_with_restrictive_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-modes",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)

    DaemonTokenStore(config).current_token()
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)
    active_slot._publish_owner_lock_record(record)

    _assert_posix_file_mode(config.token_file_path, 0o600)
    _assert_posix_file_mode(config.active_file_path, 0o600)
    _assert_posix_file_mode(config.owner_lock_path, 0o600)


def test_secret_state_rewrites_existing_broad_files_with_restrictive_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-mode-rewrite",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in (
        (config.token_file_path, {"token": "old"}),
        (config.active_file_path, {"token": "old"}),
        (config.owner_lock_path, {"pid": 1}),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        _chmod_posix(path, 0o644)

    DaemonTokenStore._persist_token(config.token_file_path, "new")
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.restore(record)
    active_slot._publish_owner_lock_record(record)

    _assert_posix_file_mode(config.token_file_path, 0o600)
    _assert_posix_file_mode(config.active_file_path, 0o600)
    _assert_posix_file_mode(config.owner_lock_path, 0o600)


def test_secret_state_writer_creates_state_dir_with_restrictive_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-dir-create",
        host="127.0.0.1",
        port=0,
    )

    DaemonTokenStore._persist_token(config.token_file_path, "new")

    _assert_posix_file_mode(config.state_dir, 0o700)


def test_secret_state_writer_repairs_existing_broad_state_dir_mode(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-dir-repair",
        host="127.0.0.1",
        port=0,
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    _chmod_posix(config.state_dir, 0o755)

    DaemonTokenStore._persist_token(config.token_file_path, "new")

    _assert_posix_file_mode(config.state_dir, 0o700)


def test_secret_state_temp_sidecars_are_restrictive_and_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-temp",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    captured: dict[str, tuple[int, dict[str, object], str]] = {}
    real_replace = os.replace

    def capture_replace(source: Path | str, target: Path | str) -> None:
        source_path = Path(source)
        target_path = Path(target)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        captured[target_path.name] = (
            _file_mode(source_path),
            payload,
            source_path.name,
        )
        real_replace(source, target)

    monkeypatch.setattr("androidctld.auth.secret_files.os.replace", capture_replace)

    token = DaemonTokenStore(config).current_token()
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)
    active_slot._publish_owner_lock_record(record)

    if os.name == "posix":
        assert captured["token.json"][0] == 0o600
        assert captured["active.json"][0] == 0o600
        assert captured["owner.lock"][0] == 0o600
    assert captured["token.json"][1] == {"token": token}
    assert captured["active.json"][1]["token"] == "secret"
    assert "token" not in captured["owner.lock"][1]
    assert captured["token.json"][2].startswith(f"token.json.{os.getpid()}.")
    assert captured["active.json"][2].startswith(f"active.json.{os.getpid()}.")
    assert captured["owner.lock"][2].startswith(f"owner.lock.{os.getpid()}.")


def test_secret_state_temp_sidecar_cleanup_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-secret-replace-failure",
        host="127.0.0.1",
        port=0,
    )
    config.token_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.token_file_path.write_text(
        json.dumps({"token": "existing"}),
        encoding="utf-8",
    )
    captured: dict[str, Path] = {}

    def fail_replace(source: Path | str, target: Path | str) -> None:
        del target
        captured["source"] = Path(source)
        raise OSError("replace failed")

    monkeypatch.setattr("androidctld.auth.secret_files.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        DaemonTokenStore._persist_token(config.token_file_path, "new")

    assert config.token_file_path.read_text(encoding="utf-8") == (
        json.dumps({"token": "existing"})
    )
    assert "source" in captured
    assert captured["source"].exists() is False
    assert list(config.token_file_path.parent.glob("token.json.*.tmp")) == []


def test_token_store_repeated_writes_use_unique_temp_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-token-temp-unique",
        host="127.0.0.1",
        port=0,
    )
    temp_names: list[str] = []
    real_replace = os.replace

    def capture_replace(source: Path | str, target: Path | str) -> None:
        temp_names.append(Path(source).name)
        real_replace(source, target)

    monkeypatch.setattr("androidctld.auth.secret_files.os.replace", capture_replace)

    DaemonTokenStore._persist_token(config.token_file_path, "one")
    DaemonTokenStore._persist_token(config.token_file_path, "two")

    assert len(temp_names) == 2
    assert len(set(temp_names)) == 2
    assert json.loads(config.token_file_path.read_text(encoding="utf-8")) == {
        "token": "two"
    }


def test_active_registry_clear_uses_parsed_record_identity(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-identity",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    config.active_file_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "host": record.host,
                "port": record.port,
                "token": record.token,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
                "ownerId": record.owner_id,
            }
        ),
        encoding="utf-8",
    )

    registry.clear(record=record)

    assert config.active_file_path.exists() is False


def test_active_registry_clear_preserves_valid_different_identity_file(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-other",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    config.active_file_path.write_text(
        json.dumps(
            {
                "pid": record.pid + 1,
                "host": record.host,
                "port": record.port,
                "token": record.token,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
                "ownerId": record.owner_id,
            }
        ),
        encoding="utf-8",
    )

    registry.clear(record=record)

    assert config.active_file_path.exists() is True


def test_active_registry_clear_deletes_validation_failure_matching_file(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-invalid",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    config.active_file_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "host": record.host,
                "port": record.port,
                "token": record.token,
                "startedAt": record.started_at,
                "workspaceRoot": "",
                "ownerId": record.owner_id,
            }
        ),
        encoding="utf-8",
    )

    registry.clear(record=record)

    assert config.active_file_path.exists() is False


def test_active_registry_clear_deletes_malformed_json_matching_file(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-malformed-json",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    config.active_file_path.write_text("{", encoding="utf-8")

    registry.clear(record=record)

    assert config.active_file_path.exists() is False


def test_active_registry_clear_deletes_unknown_field_matching_file(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-extra",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    config.active_file_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "host": record.host,
                "port": record.port,
                "token": record.token,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
                "ownerId": record.owner_id,
                "unexpectedField": "preserve-me",
            }
        ),
        encoding="utf-8",
    )

    registry.clear(record=record)

    assert config.active_file_path.exists() is False


def test_active_registry_read_rejects_active_file_with_unknown_field_and_cleans_it(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-read-extra",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    config.active_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.active_file_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "host": record.host,
                "port": record.port,
                "token": record.token,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
                "ownerId": record.owner_id,
                "unexpectedField": "preserve-me",
            }
        ),
        encoding="utf-8",
    )

    assert registry.read() is None
    assert config.active_file_path.exists() is False


def test_active_registry_read_preserves_file_on_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-read-oserror",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    def raise_os_error() -> object:
        raise OSError("transient read failure")

    monkeypatch.setattr(registry, "_read_active_file_model", raise_os_error)

    assert registry.read() is None
    assert config.active_file_path.exists() is True


def test_active_registry_clear_preserves_valid_file_on_read_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-clear-oserror",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    registry.publish(record)

    def raise_os_error() -> object:
        raise OSError("transient read failure")

    monkeypatch.setattr(registry, "_read_active_file_model", raise_os_error)

    registry.clear(record=record)

    assert config.active_file_path.exists() is True


def test_active_registry_publish_overwrites_unreachable_live_pid_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-active-unreachable",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(
        config,
        live_checker=lambda _record: OwnershipHealthProbeResult(
            OwnershipHealthStatus.UNREACHABLE
        ),
    )
    record = registry.build_record(host="127.0.0.1", port=17631, token="new")
    existing = record.model_copy(
        update={
            "pid": 4242,
            "started_at": "2026-04-27T00:00:00Z",
            "token": "old",
        }
    )
    registry.restore(existing)
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == 4242),
    )

    registry.publish(record)

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "new"


@pytest.mark.parametrize(
    "status",
    [OwnershipHealthStatus.LIVE_MATCH, OwnershipHealthStatus.LIVE_MISMATCH],
)
def test_active_registry_publish_blocks_on_live_listener_evidence(
    tmp_path: Path,
    status: OwnershipHealthStatus,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-active-live",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(
        config,
        live_checker=lambda _record: OwnershipHealthProbeResult(status),
    )
    record = registry.build_record(host="127.0.0.1", port=17631, token="new")
    existing = record.model_copy(
        update={
            "pid": 4242,
            "started_at": "2026-04-27T00:00:00Z",
            "token": "old",
        }
    )
    registry.restore(existing)

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        registry.publish(record)

    restored = registry.read()
    assert restored is not None
    assert restored.identity == existing.identity
    assert restored.token == "old"


def test_active_slot_restores_live_record_before_rejecting_new_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-active-slot",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MATCH, token=record.token)
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    config.token_file_path.write_text(
        json.dumps({"token": "secret"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "secret"
    assert probe.calls[0]["tokens"] == ["secret"]


def test_active_slot_publish_writes_tokenless_owner_lock(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-tokenless-publish",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)

    active_slot.acquire()
    try:
        record = active_slot.prepare(host="127.0.0.1", port=17631, token="secret")
        active_slot.publish(record)

        owner_payload = json.loads(config.owner_lock_path.read_text(encoding="utf-8"))
        assert owner_payload == {
            "host": "127.0.0.1",
            "ownerId": "owner-tokenless-publish",
            "pid": record.pid,
            "port": 17631,
            "startedAt": record.started_at,
            "workspaceRoot": workspace_root.resolve().as_posix(),
        }
        assert "token" not in owner_payload

        active_payload = json.loads(config.active_file_path.read_text(encoding="utf-8"))
        assert active_payload["token"] == "secret"
    finally:
        active_slot.release()


def test_active_slot_owner_lock_temp_sidecar_is_tokenless(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-tokenless-temp",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    captured: dict[str, object] = {}

    def capture_replace(source: Path | str, target: Path | str) -> None:
        del target
        captured["payload"] = json.loads(Path(source).read_text(encoding="utf-8"))

    monkeypatch.setattr("androidctld.auth.secret_files.os.replace", capture_replace)

    active_slot._publish_owner_lock_record(record)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "token" not in payload


def test_active_slot_tokenless_live_owner_lock_restores_from_token_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-tokenless-restore",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="restored")
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MATCH, token="restored")
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    config.token_file_path.write_text(
        json.dumps({"token": "restored"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "restored"
    assert probe.calls[0]["tokens"] == ["restored"]


def test_active_slot_tokenless_live_owner_lock_uses_matching_active_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-active-token-restore",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="active-token")
    registry.restore(record)
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(
            OwnershipHealthStatus.LIVE_MATCH,
            token="active-token",
        )
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "active-token"
    assert probe.calls[0]["tokens"] == ["active-token"]


def test_active_slot_tokenless_owner_lock_repairs_active_token_from_token_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-tokenless-drift",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="correct")
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MATCH, token="correct")
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    registry.restore(record.model_copy(update={"token": "stale"}))
    config.token_file_path.write_text(
        json.dumps({"token": "correct"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "correct"
    assert probe.calls[0]["tokens"] == ["correct", "stale"]


def test_active_slot_owner_lock_embedded_token_is_ignored_for_probe_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-embedded-token-ignored",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="current")
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MATCH, token="current")
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "token": "embedded",
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    config.token_file_path.write_text(
        json.dumps({"token": "current"}),
        encoding="utf-8",
    )
    registry.restore(record.model_copy(update={"token": "stale"}))
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    restored = registry.read()
    assert restored is not None
    assert restored.identity == record.identity
    assert restored.token == "current"
    assert probe.calls[0]["owner_id"] == record.owner_id
    assert probe.calls[0]["workspace_root"] == record.workspace_root
    assert probe.calls[0]["tokens"] == ["current", "stale"]


@pytest.mark.parametrize(
    "token_payload",
    [
        None,
        "",
        "[]",
        json.dumps({"token": "   "}),
    ],
)
def test_active_slot_tokenless_live_owner_lock_conflicts_without_valid_token_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token_payload: str | None,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-tokenless-missing-token",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": record.pid,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    if token_payload is not None:
        config.token_file_path.write_text(token_payload, encoding="utf-8")
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == record.pid),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    assert config.active_file_path.exists() is False
    if token_payload is None:
        assert config.token_file_path.exists() is False


def test_active_slot_old_unreachable_owner_lock_with_live_pid_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-old-unreachable",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.UNREACHABLE)
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": record.host,
                "ownerId": record.owner_id,
                "pid": 4242,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    old_mtime = time.time() - (active_slot._OWNER_LOCK_STALE_SECONDS + 1.0)
    os.utime(config.owner_lock_path, (old_mtime, old_mtime))
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == 4242),
    )

    active_slot.acquire()
    try:
        assert config.owner_lock_path.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
    finally:
        active_slot.release_owner()


def test_active_slot_fresh_unreachable_owner_lock_with_live_pid_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-fresh-unreachable",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.UNREACHABLE)
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    owner_payload = {
        "host": record.host,
        "ownerId": record.owner_id,
        "pid": 4242,
        "port": record.port,
        "startedAt": record.started_at,
        "workspaceRoot": record.workspace_root,
    }
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(json.dumps(owner_payload), encoding="utf-8")
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == 4242),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    assert (
        json.loads(config.owner_lock_path.read_text(encoding="utf-8")) == owner_payload
    )


@pytest.mark.parametrize(
    "owner_update",
    [
        {"ownerId": "owner-other"},
        {"workspaceRoot": "/tmp/other-workspace"},
    ],
)
def test_active_slot_healthy_mismatched_owner_lock_fails_closed_after_grace(
    tmp_path: Path,
    owner_update: dict[str, str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-current-mismatch",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    probe = FakeOwnershipHealthProbe(
        OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MISMATCH)
    )
    active_slot = ActiveSlotCoordinator(
        config=config,
        active_registry=registry,
        ownership_probe=probe,  # type: ignore[arg-type]
    )
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    owner_payload = {
        "host": record.host,
        "ownerId": record.owner_id,
        "pid": 4242,
        "port": record.port,
        "startedAt": record.started_at,
        "workspaceRoot": record.workspace_root,
    }
    owner_payload.update(owner_update)
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(json.dumps(owner_payload), encoding="utf-8")
    old_mtime = time.time() - (active_slot._OWNER_LOCK_STALE_SECONDS + 1.0)
    os.utime(config.owner_lock_path, (old_mtime, old_mtime))

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    assert (
        json.loads(config.owner_lock_path.read_text(encoding="utf-8")) == owner_payload
    )
    assert config.active_file_path.exists() is False


def test_active_slot_old_unprobeable_non_loopback_owner_lock_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-non-loopback",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text(
        json.dumps(
            {
                "host": "192.0.2.10",
                "ownerId": record.owner_id,
                "pid": 4242,
                "port": record.port,
                "startedAt": record.started_at,
                "workspaceRoot": record.workspace_root,
            }
        ),
        encoding="utf-8",
    )
    old_mtime = time.time() - (active_slot._OWNER_LOCK_STALE_SECONDS + 1.0)
    os.utime(config.owner_lock_path, (old_mtime, old_mtime))
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == 4242),
    )

    active_slot.acquire()
    try:
        assert config.owner_lock_path.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
    finally:
        active_slot.release_owner()


def test_active_slot_plain_pid_owner_lock_live_and_stale_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-plain-pid",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    active_slot = ActiveSlotCoordinator(config=config, active_registry=registry)
    config.owner_lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.owner_lock_path.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda pid: pid == 4242),
    )

    with pytest.raises(RuntimeError, match="live daemon already owns active slot"):
        active_slot.acquire()

    assert config.owner_lock_path.read_text(encoding="utf-8") == "4242"
    assert config.active_file_path.exists() is False
    old_mtime = time.time() - (active_slot._OWNER_LOCK_STALE_SECONDS + 1.0)
    os.utime(config.owner_lock_path, (old_mtime, old_mtime))

    active_slot.acquire()
    try:
        assert config.owner_lock_path.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
    finally:
        active_slot.release_owner()

    config.owner_lock_path.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(
        ActiveDaemonRegistry,
        "_is_pid_live",
        staticmethod(lambda _pid: False),
    )

    active_slot.acquire()
    try:
        assert config.owner_lock_path.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        )
    finally:
        active_slot.release_owner()


def test_ownership_health_probe_does_not_probe_non_loopback_host() -> None:
    def fail_opener() -> object:
        raise AssertionError("non-loopback host must not open a socket")

    result = OwnershipHealthProbe(opener_factory=fail_opener).probe(
        host="192.0.2.10",
        port=17631,
        owner_id="owner",
        workspace_root="/tmp/workspace",
        expected_workspace_root="/tmp/workspace",
        expected_owner_id="owner",
        tokens=["secret"],
    )

    assert result.status == OwnershipHealthStatus.UNPROBEABLE


def test_ownership_health_probe_treats_workspace_busy_as_live_mismatch() -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "WORKSPACE_BUSY",
                        "message": "workspace daemon is owned elsewhere",
                        "retryable": False,
                        "details": {"ownerId": "owner-other"},
                    },
                }
            ).encode("utf-8")

        def close(self) -> None:
            pass

    class FakeOpener:
        def open(self, request, timeout: float) -> FakeResponse:
            del request, timeout
            return FakeResponse()

    result = OwnershipHealthProbe(opener_factory=lambda: FakeOpener()).probe(
        host="127.0.0.1",
        port=17631,
        owner_id="owner-current",
        workspace_root="/tmp/workspace",
        expected_workspace_root="/tmp/workspace",
        expected_owner_id="owner-current",
        tokens=["secret"],
    )

    assert result.status == OwnershipHealthStatus.LIVE_MISMATCH
    assert result.token is None


def test_ownership_health_probe_no_tokens_unauthorized_is_live_mismatch() -> None:
    calls: list[dict[str, str]] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "DAEMON_UNAUTHORIZED",
                        "message": "missing or invalid daemon token",
                        "retryable": False,
                        "details": {},
                    },
                }
            ).encode("utf-8")

        def close(self) -> None:
            pass

    class FakeOpener:
        def open(self, request, timeout: float) -> FakeResponse:
            del timeout
            calls.append(
                {name.lower(): value for name, value in request.header_items()}
            )
            return FakeResponse()

    result = OwnershipHealthProbe(opener_factory=lambda: FakeOpener()).probe(
        host="127.0.0.1",
        port=17631,
        owner_id="owner-current",
        workspace_root="/tmp/workspace",
        expected_workspace_root="/tmp/workspace",
        expected_owner_id="owner-current",
        tokens=[],
    )

    assert result.status == OwnershipHealthStatus.LIVE_MISMATCH
    assert result.token is None
    assert len(calls) == 1


def test_ownership_health_probe_continues_after_stale_token_mismatch() -> None:
    seen_tokens: list[str] = []

    class FakeResponse:
        def __init__(self, token: str) -> None:
            self._token = token

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            if self._token == "valid":
                return json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "service": "androidctld",
                            "version": androidctld.__version__,
                            "workspaceRoot": "/tmp/workspace",
                            "ownerId": "owner-current",
                        },
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "DAEMON_UNAUTHORIZED",
                        "message": "missing or invalid daemon token",
                        "retryable": False,
                        "details": {},
                    },
                }
            ).encode("utf-8")

        def close(self) -> None:
            pass

    class FakeOpener:
        def open(self, request, timeout: float) -> FakeResponse:
            del timeout
            headers = {name.lower(): value for name, value in request.header_items()}
            token = headers[TOKEN_HEADER_NAME.lower()]
            seen_tokens.append(token)
            return FakeResponse(token)

    result = OwnershipHealthProbe(opener_factory=lambda: FakeOpener()).probe(
        host="127.0.0.1",
        port=17631,
        owner_id="owner-current",
        workspace_root="/tmp/workspace",
        expected_workspace_root="/tmp/workspace",
        expected_owner_id="owner-current",
        tokens=["legacy", "valid"],
    )

    assert result.status == OwnershipHealthStatus.LIVE_MATCH
    assert result.token == "valid"
    assert seen_tokens == ["legacy", "valid"]


def test_http_host_readiness_uses_health_probe_contract(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-ready",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    seen: list[tuple[str, dict[str, str], float]] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": True,
                    "result": {
                        "service": "androidctld",
                        "version": androidctld.__version__,
                        "workspaceRoot": workspace_root.resolve().as_posix(),
                        "ownerId": "owner-ready",
                    },
                }
            ).encode("utf-8")

    class FakeOpener:
        def open(self, request, timeout: float) -> FakeResponse:
            seen.append(
                (
                    request.full_url,
                    {name.lower(): value for name, value in request.header_items()},
                    timeout,
                )
            )
            return FakeResponse()

    host = DaemonHttpHost(
        config=config,
        logger=logging.getLogger("test-http-host"),
        opener_factory=lambda: FakeOpener(),
    )

    host.wait_until_ready(record=record, owner_id=config.owner_id)

    assert seen == [
        (
            "http://127.0.0.1:17631/health",
            {
                TOKEN_HEADER_NAME.lower(): "secret",
                OWNER_HEADER_NAME.lower(): "owner-ready",
            },
            host.ready_poll_interval_seconds,
        )
    ]


def test_http_host_readiness_retries_invalid_health_payload_until_timeout(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-ready-timeout",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")
    seen: list[float] = []

    class FakeTime:
        def __init__(self) -> None:
            self.current = 0.0

        def monotonic(self) -> float:
            return self.current

        def sleep(self, seconds: float) -> None:
            self.current += seconds

    fake_time = FakeTime()

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "ok": True,
                    "result": {
                        "service": "androidctld",
                        "workspaceRoot": "/tmp/workspace",
                    },
                }
            ).encode("utf-8")

    class FakeOpener:
        def open(self, request, timeout: float) -> FakeResponse:
            del request
            seen.append(timeout)
            return FakeResponse()

    host = DaemonHttpHost(
        config=config,
        logger=logging.getLogger("test-http-host-timeout"),
        opener_factory=lambda: FakeOpener(),
    )
    host._READY_TIMEOUT_SECONDS = 0.11
    original_time = http_host_module.time
    http_host_module.time = fake_time
    try:
        with pytest.raises(RuntimeError, match="timed out waiting for androidctld"):
            host.wait_until_ready(record=record, owner_id=config.owner_id)
    finally:
        http_host_module.time = original_time

    assert len(seen) == 4
    assert seen == [host.ready_poll_interval_seconds] * 4


def test_http_host_start_closes_bound_socket_when_thread_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-start-failure",
        host="127.0.0.1",
        port=0,
    )
    host = DaemonHttpHost(
        config=config,
        logger=logging.getLogger("test-http-host-start-failure"),
    )
    bound_servers: list[ThreadingHTTPServer] = []
    original_server = http_host_module.ThreadingHTTPServer

    class RecordingThreadingHTTPServer(original_server):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            bound_servers.append(self)

    class StubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            del format, args

    class FailingThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(
        http_host_module, "ThreadingHTTPServer", RecordingThreadingHTTPServer
    )
    monkeypatch.setattr(
        http_host_module,
        "threading",
        SimpleNamespace(Thread=FailingThread),
    )

    with pytest.raises(RuntimeError, match="thread start failed"):
        host.start(StubHandler)

    assert host.is_running is False
    assert len(bound_servers) == 1

    rebound_host, rebound_port = bound_servers[0].server_address[:2]
    assert rebound_host == config.host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound_socket:
        rebound_socket.bind((rebound_host, rebound_port))


def test_server_stop_stops_listener_before_releasing_active_ownership(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-stop-order",
        host="127.0.0.1",
        port=0,
    )
    events: list[str] = []
    stop_started = threading.Event()
    allow_stop_finish = threading.Event()
    server = AndroidctldHttpServer(config=config)

    class FakeHttpHost:
        def __init__(self) -> None:
            self.running = True

        @property
        def is_running(self) -> bool:
            return self.running

        def stop(self) -> None:
            events.append("listener-stop-begin")
            self.running = False
            stop_started.set()
            assert allow_stop_finish.wait(timeout=2.0)
            events.append("listener-stop-end")

    class FakeActiveSlot:
        def clear_record(self) -> None:
            events.append("active-clear")

        def release_owner(self) -> None:
            events.append("owner-release")

    server._http_host = FakeHttpHost()  # type: ignore[assignment]
    server._active_slot = FakeActiveSlot()  # type: ignore[assignment]

    first_stop = threading.Thread(target=server.stop)
    second_stop = threading.Thread(target=server.stop)
    first_stop.start()
    assert stop_started.wait(timeout=2.0)
    second_stop.start()

    assert events == ["listener-stop-begin"]

    allow_stop_finish.set()
    first_stop.join(timeout=2.0)
    second_stop.join(timeout=2.0)

    assert first_stop.is_alive() is False
    assert second_stop.is_alive() is False
    assert events == [
        "listener-stop-begin",
        "listener-stop-end",
        "active-clear",
        "owner-release",
    ]


def test_server_start_resets_closing_gate_for_reused_server(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-restart",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)
    server = AndroidctldHttpServer(config=config)
    server._closing = True
    events: list[tuple[str, object]] = []

    class FakeHttpHost:
        def __init__(self) -> None:
            self.running = False

        @property
        def is_running(self) -> bool:
            return self.running

        def start(self, handler_class: type[BaseHTTPRequestHandler]) -> tuple[str, int]:
            del handler_class
            self.running = True
            return config.host, 17631

        def wait_until_ready(self, *, record: object) -> None:
            del record
            status, payload = server.handle(
                method="POST",
                path="/health",
                headers={},
                body=b"{}",
            )
            events.append(("readiness", status))
            events.append(("closing", server._closing))
            events.append(("service", payload["service"]))

        def stop(self) -> None:
            self.running = False

    class FakeActiveSlot:
        active_record = None

        def acquire(self) -> None:
            events.append(("active", "acquire"))

        def prepare(self, *, host: str, port: int, token: str) -> object:
            record = registry.build_record(host=host, port=port, token=token)
            self.active_record = record
            return record

        def publish(self, record: object) -> object:
            events.append(("active", "publish"))
            return record

        def clear_record(self) -> None:
            events.append(("active", "clear"))

        def release_owner(self) -> None:
            events.append(("active", "release"))

    server._http_host = FakeHttpHost()  # type: ignore[assignment]
    server._active_slot = FakeActiveSlot()  # type: ignore[assignment]

    server.start()

    assert server._closing is False
    assert events == [
        ("active", "acquire"),
        ("readiness", 200),
        ("closing", False),
        ("service", "androidctld"),
        ("active", "publish"),
    ]

    status, _ = server.handle(
        method="POST",
        path="/runtime/get",
        headers={},
        body=b"{}",
    )

    assert status == 200


def test_daemon_config_normalizes_owner_id(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="  owner-6  ",
        host="127.0.0.1",
        port=0,
    )
    registry = ActiveDaemonRegistry(config)

    record = registry.build_record(host="127.0.0.1", port=17631, token="secret")

    assert config.owner_id == "owner-6"
    assert record.owner_id == "owner-6"


def test_daemon_config_rejects_empty_workspace_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace_root must not be empty"):
        DaemonConfig(
            workspace_root="  ",
            owner_id="owner-7",
            host="127.0.0.1",
            port=0,
        )


def test_token_store_persists_token_to_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-3",
        host="127.0.0.1",
        port=0,
    )

    store = DaemonTokenStore(config)
    token = store.current_token()

    token_path = config.state_dir / "token.json"
    assert token_path.exists()
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert payload["token"] == token

    reloaded = DaemonTokenStore(config)
    assert reloaded.current_token() == token


def test_token_store_ignores_non_object_json(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-5",
        host="127.0.0.1",
        port=0,
    )
    token_path = config.state_dir / "token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text('["not-a-token"]', encoding="utf-8")

    store = DaemonTokenStore(config)
    token = store.current_token()

    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert payload["token"] == token


def test_token_store_load_existing_token_loads_valid_token(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-existing-token-valid",
        host="127.0.0.1",
        port=0,
    )
    config.token_file_path.parent.mkdir(parents=True, exist_ok=True)
    config.token_file_path.write_text(
        json.dumps({"token": "  existing-secret  "}),
        encoding="utf-8",
    )

    assert DaemonTokenStore.load_existing_token(config.token_file_path) == (
        "existing-secret"
    )


@pytest.mark.parametrize(
    "token_payload",
    [
        None,
        "",
        "[]",
        json.dumps({"token": "   "}),
        json.dumps({"token": 123}),
    ],
)
def test_token_store_load_existing_token_returns_none_without_generating(
    tmp_path: Path,
    token_payload: str | None,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-existing-token-none",
        host="127.0.0.1",
        port=0,
    )
    if token_payload is not None:
        config.token_file_path.parent.mkdir(parents=True, exist_ok=True)
        config.token_file_path.write_text(token_payload, encoding="utf-8")

    assert DaemonTokenStore.load_existing_token(config.token_file_path) is None

    if token_payload is None:
        assert config.token_file_path.exists() is False
    else:
        assert config.token_file_path.read_text(encoding="utf-8") == token_payload


def test_token_store_load_existing_token_returns_none_for_unreadable_path(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="owner-existing-token-unreadable",
        host="127.0.0.1",
        port=0,
    )
    config.token_file_path.mkdir(parents=True)

    assert DaemonTokenStore.load_existing_token(config.token_file_path) is None
    assert config.token_file_path.is_dir()


def test_build_arg_parser_requires_workspace_and_owner(tmp_path: Path) -> None:
    parser = build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--workspace-root", str(tmp_path)])
    with pytest.raises(SystemExit):
        parser.parse_args(["--owner-id", "owner-4"])

    args = parser.parse_args(
        ["--workspace-root", str(tmp_path), "--owner-id", "owner-4"]
    )
    assert args.workspace_root == str(tmp_path)
    assert args.owner_id == "owner-4"


def test_main_wires_runtime_close_shutdown_to_main_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: dict[str, object] = {}

    class FakeServer:
        def __init__(
            self,
            *,
            config: DaemonConfig,
            token_store: DaemonTokenStore,
            shutdown_callback=None,
        ) -> None:
            del token_store
            events["workspace_root"] = config.workspace_root
            events["shutdown_callback"] = shutdown_callback
            events["stopped"] = False

        def start(self) -> None:
            callback = events["shutdown_callback"]
            assert callback is not None
            callback()

        def stop(self) -> None:
            events["stopped"] = True

    monkeypatch.setattr(androidctld_main, "AndroidctldHttpServer", FakeServer)
    monkeypatch.setattr(androidctld_main.signal, "signal", lambda *args, **kwargs: None)

    exit_code = androidctld_main.main(
        ["--workspace-root", str(tmp_path), "--owner-id", "owner-main"]
    )

    assert exit_code == 0
    assert events["workspace_root"] == tmp_path.resolve()
    assert events["stopped"] is True
