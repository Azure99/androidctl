from __future__ import annotations

import json
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from androidctl.daemon.client import (
    DaemonApiError,
    DaemonClient,
    DaemonProtocolError,
    IncompatibleDaemonError,
    IncompatibleDaemonVersionError,
    try_get_healthy_daemon,
)
from androidctl.daemon.discovery import (
    discover_existing_daemon_client,
    resolve_daemon_client,
)
from androidctl.daemon.launcher import LaunchSpec, resolve_launch_spec
from androidctl_contracts.daemon_api import HealthResult
from androidctl_contracts.paths import daemon_state_root
from androidctl_contracts.user_state import ActiveDaemonRecord


def _active_record(
    *,
    pid: int = 1234,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "secret",
    started_at: str = "2026-03-27T00:00:00Z",
    workspace_root: str = "/repo",
    owner_id: str = "shell:self:1",
) -> ActiveDaemonRecord:
    return ActiveDaemonRecord(
        pid=pid,
        host=host,
        port=port,
        token=token,
        started_at=started_at,
        workspace_root=workspace_root,
        owner_id=owner_id,
    )


def _write_active_payload(
    workspace_root: Path,
    payload: str | dict[str, object],
) -> Path:
    active_path = daemon_state_root(workspace_root) / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        active_path.write_text(payload, encoding="utf-8")
    else:
        active_path.write_text(json.dumps(payload), encoding="utf-8")
    return active_path


def _write_active_record(
    workspace_root: Path,
    **record_kwargs: object,
) -> ActiveDaemonRecord:
    record = _active_record(
        workspace_root=workspace_root.resolve().as_posix(),
        **record_kwargs,
    )
    _write_active_payload(workspace_root, record.model_dump())
    return record


def _health_result(record: ActiveDaemonRecord) -> HealthResult:
    return HealthResult(
        service="androidctld",
        version="0.1.0",
        workspace_root=record.workspace_root,
        owner_id=record.owner_id,
    )


def _patch_launch_spec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env_overlay: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    monkeypatch.setattr(
        "androidctl.daemon.discovery.resolve_launch_spec",
        lambda *, env: LaunchSpec(
            executable="/bin/androidctld",
            argv=("--port", "0"),
            env_overlay=env_overlay,
            cwd=cwd,
        ),
    )


@contextmanager
def _loopback_daemon_server(
    *,
    workspace_root: str = "/repo",
    owner_id: str = "shell:self:1",
):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            body = json.dumps(
                {
                    "ok": True,
                    "result": {
                        "service": "androidctld",
                        "version": "0.1.0",
                        "workspaceRoot": workspace_root,
                        "ownerId": owner_id,
                    },
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        thread.join(timeout=1.0)
        assert not thread.is_alive(), "loopback daemon server thread did not stop"
        server.server_close()


def test_daemon_client_health_ignores_proxy_env_for_loopback_active_record(
    monkeypatch,
) -> None:
    with _loopback_daemon_server() as port:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("NO_PROXY", "")
        record = ActiveDaemonRecord(
            pid=1234,
            host="127.0.0.1",
            port=port,
            token="secret",
            started_at="2026-03-27T00:00:00Z",
            workspace_root="/repo",
            owner_id="shell:self:1",
        )

        client = DaemonClient.from_active_record(record, owner_id="shell:self:1")
        try:
            health = client.health(record)
        finally:
            client._http.close()

    assert health.service == "androidctld"


def test_try_get_healthy_daemon_returns_none_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    assert try_get_healthy_daemon(client, _active_record()) is None


def test_try_get_healthy_daemon_returns_none_on_structured_daemon_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            json={
                "ok": False,
                "error": {
                    "code": "DAEMON_UNAUTHORIZED",
                    "message": "missing or invalid daemon token",
                    "retryable": False,
                    "details": {},
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    assert try_get_healthy_daemon(client, _active_record()) is None


def test_resolve_launch_spec_uses_androidctld_bin_env() -> None:
    spec = resolve_launch_spec(env={"ANDROIDCTLD_BIN": "/env/androidctld"})

    assert spec.executable == "/env/androidctld"
    assert spec.argv == ()


def test_resolve_launch_spec_ignores_path_lookup() -> None:
    spec = resolve_launch_spec(env={"PATH": "/custom/bin"})

    assert spec.executable == sys.executable
    assert spec.argv == ("-m", "androidctld")


def test_resolve_launch_spec_does_not_fallback_to_os_environ_when_env_is_empty(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANDROIDCTLD_BIN", "/os/env/androidctld")

    spec = resolve_launch_spec(env={})

    assert spec.executable == sys.executable
    assert spec.argv == ("-m", "androidctld")


def test_resolve_launch_spec_uses_os_environ_when_env_is_not_explicit(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANDROIDCTLD_BIN", "/os/env/androidctld")

    spec = resolve_launch_spec()

    assert spec.executable == "/os/env/androidctld"
    assert spec.argv == ()


def test_try_get_healthy_daemon_raises_on_malformed_health_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"unexpected": {}})

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    with pytest.raises(DaemonProtocolError):
        try_get_healthy_daemon(client, _active_record())


def test_try_get_healthy_daemon_raises_on_incompatible_health_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "result": {
                    "service": "not-androidctld",
                    "version": "0.1.0",
                    "workspaceRoot": "/repo",
                    "ownerId": "shell:self:1",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    with pytest.raises(DaemonProtocolError):
        try_get_healthy_daemon(client, _active_record())


def test_try_get_healthy_daemon_raises_on_undocumented_instance_id_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "ok": True,
                "result": {
                    "service": "androidctld",
                    "version": "0.1.0",
                    "instanceId": "inst-2",
                    "workspaceRoot": "/repo",
                    "ownerId": "shell:self:1",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    with pytest.raises(DaemonProtocolError, match="invalid health response schema"):
        try_get_healthy_daemon(client, _active_record())


def test_try_get_healthy_daemon_raises_on_extra_api_version_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "ok": True,
                "result": {
                    "service": "androidctld",
                    "version": "0.1.0",
                    "apiVersion": 1,
                    "workspaceRoot": "/repo",
                    "ownerId": "shell:self:1",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    with pytest.raises(IncompatibleDaemonError, match="health payload is incompatible"):
        try_get_healthy_daemon(client, _active_record())


def test_resolve_daemon_client_rejects_non_loopback_active_host(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root, host="192.168.1.25")

    with pytest.raises(RuntimeError, match="loopback"):
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )


def test_resolve_daemon_client_accepts_docs_payload_without_schema_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    _write_active_payload(workspace_root, record.model_dump())
    mock_client = object()

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client


def test_discover_existing_daemon_client_returns_healthy_active_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    _write_active_payload(workspace_root, record.model_dump())
    mock_client = object()

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    daemon = discover_existing_daemon_client(
        workspace_root=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client


def test_discover_existing_daemon_client_uses_active_token_with_tokenless_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(
        workspace_root=workspace_root.resolve().as_posix(),
        token="active-json-secret",
    )
    _write_active_payload(workspace_root, record.model_dump())
    owner_lock_path = daemon_state_root(workspace_root) / "owner.lock"
    owner_lock_path.write_text(
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
    mock_client = object()
    captured: dict[str, str] = {}

    def capture_record(active_record: ActiveDaemonRecord, owner_id: str) -> object:
        del owner_id
        captured["token"] = active_record.token
        return mock_client

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        capture_record,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )

    daemon = discover_existing_daemon_client(
        workspace_root=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client
    assert captured == {"token": "active-json-secret"}


def test_discover_existing_daemon_client_returns_none_when_active_record_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    daemon = discover_existing_daemon_client(
        workspace_root=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is None


def test_discover_existing_daemon_client_treats_malformed_active_payload_as_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    active_path = _write_active_payload(workspace_root, "{malformed")

    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    daemon = discover_existing_daemon_client(
        workspace_root=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is None
    assert active_path.exists()


def test_discover_existing_daemon_client_returns_none_for_unhealthy_active_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            DaemonProtocolError("stale active record is not healthy")
        ),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    daemon = discover_existing_daemon_client(
        workspace_root=workspace_root,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is None


def test_discover_existing_daemon_client_same_owner_version_mismatch_hard_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    with pytest.raises(
        IncompatibleDaemonVersionError,
        match="release version mismatch",
    ):
        discover_existing_daemon_client(
            workspace_root=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )


def test_discover_same_owner_bad_health_shape_hard_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            IncompatibleDaemonError(
                "androidctl/androidctld health payload is incompatible; "
                "install matching androidctl and androidctld versions"
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    with pytest.raises(IncompatibleDaemonError, match="health payload is incompatible"):
        discover_existing_daemon_client(
            workspace_root=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )


def test_resolve_daemon_client_launches_when_active_record_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    events: list[str] = []
    mock_client = object()

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )

    def fake_try_get_healthy_daemon(_client, _record):  # noqa: ANN001
        events.append("health")
        return _health_result(record)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        fake_try_get_healthy_daemon,
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        del argv, kwargs
        events.append("launch")
        _write_active_payload(workspace_root, record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client
    assert events == ["launch", "health"]


def test_resolve_daemon_client_ignores_legacy_home_config_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home_dir = tmp_path / "home"
    config_path = home_dir / ".androidctl" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "launcher": {
                    "executable": "/bad/androidctld",
                    "argv": ["--bad"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    captured: dict[str, object] = {}
    mock_client = object()

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        del kwargs
        captured["argv"] = argv
        _write_active_payload(workspace_root, record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client
    assert captured["argv"] == [
        sys.executable,
        "-m",
        "androidctld",
        "--workspace-root",
        str(workspace_root.resolve()),
        "--owner-id",
        "shell:self:1",
    ]
    assert config_path.exists()


def test_resolve_daemon_client_same_owner_version_mismatch_does_not_fallback_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("release version mismatch must not launch androidctld")
        ),
    )

    with pytest.raises(
        IncompatibleDaemonVersionError,
        match="release version mismatch",
    ):
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=tmp_path,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )


def test_resolve_same_owner_bad_health_shape_no_fallback_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            IncompatibleDaemonError(
                "androidctl/androidctld health payload is incompatible; "
                "install matching androidctl and androidctld versions"
            )
        ),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incompatible health payload must not launch androidctld")
        ),
    )

    with pytest.raises(IncompatibleDaemonError, match="health payload is incompatible"):
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=tmp_path,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )


def test_resolve_daemon_client_recovers_from_unhealthy_stale_active_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    stale_record = _write_active_record(workspace_root)
    fresh_record = _active_record(
        pid=5678,
        port=9876,
        token="fresh-secret",
        started_at="2026-03-28T00:00:00Z",
        workspace_root=workspace_root.resolve().as_posix(),
    )
    events: list[str] = []

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{record.started_at}:{owner_id}",
    )

    def fake_try_get_healthy_daemon(client, record):  # noqa: ANN001
        del client
        events.append(f"health:{record.pid}:{record.started_at}")
        if record.identity == stale_record.identity:
            raise DaemonProtocolError("stale active record is not healthy")
        return _health_result(record)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        fake_try_get_healthy_daemon,
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        del argv, kwargs
        events.append("launch")
        _write_active_payload(workspace_root, fresh_record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon == "client:5678:2026-03-28T00:00:00Z:shell:self:1"
    assert events == [
        "health:1234:2026-03-27T00:00:00Z",
        "launch",
        "health:5678:2026-03-28T00:00:00Z",
    ]


def test_resolve_daemon_client_recovers_when_stale_record_points_to_other_live_daemon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    stale_record = _write_active_record(workspace_root)
    fresh_record = _active_record(
        pid=5678,
        port=9876,
        token="fresh-secret",
        started_at="2026-03-28T00:00:00Z",
        workspace_root=workspace_root.resolve().as_posix(),
    )
    events: list[str] = []

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{record.started_at}:{owner_id}",
    )

    def fake_try_get_healthy_daemon(client, record):  # noqa: ANN001
        del client
        events.append(f"health:{record.pid}:{record.started_at}")
        if record.identity == stale_record.identity:
            raise DaemonProtocolError("health response owner id mismatch")
        return _health_result(record)

    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        fake_try_get_healthy_daemon,
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        del argv, kwargs
        events.append("launch")
        _write_active_payload(workspace_root, fresh_record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon == "client:5678:2026-03-28T00:00:00Z:shell:self:1"
    assert events == [
        "health:1234:2026-03-27T00:00:00Z",
        "launch",
        "health:5678:2026-03-28T00:00:00Z",
    ]


def test_resolve_daemon_client_launched_version_mismatch_hard_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    launches = 0

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: f"client:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: (_ for _ in ()).throw(
            IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )
        ),
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        nonlocal launches
        del argv, kwargs
        launches += 1
        _write_active_payload(workspace_root, record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    with pytest.raises(
        IncompatibleDaemonVersionError,
        match="release version mismatch",
    ):
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=tmp_path,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )

    assert launches == 1


def test_resolve_daemon_client_treats_malformed_active_payload_as_missing_hint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    active_path = _write_active_payload(workspace_root, "{malformed")
    record = _active_record(
        pid=5678,
        port=9876,
        token="fresh-secret",
        started_at="2026-03-28T00:00:00Z",
        workspace_root=workspace_root.resolve().as_posix(),
    )
    launches = 0
    mock_client = object()

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        nonlocal launches
        del argv, kwargs
        launches += 1
        _write_active_payload(workspace_root, record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client
    assert active_path.exists()
    assert launches == 1


def test_resolve_daemon_client_redirects_launch_output_to_workspace_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    daemon_root = daemon_state_root(workspace_root)
    record = _active_record(workspace_root=workspace_root.resolve().as_posix())
    mock_client = object()

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: mock_client,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(record),
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        captured["argv"] = argv
        captured["stdout_name"] = kwargs["stdout"].name
        captured["stderr_name"] = kwargs["stderr"].name
        captured["cwd"] = kwargs["cwd"]
        _write_active_payload(workspace_root, record.model_dump())
        return object()

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    daemon = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert daemon is mock_client
    stdout_name = Path(str(captured["stdout_name"]))
    stderr_name = Path(str(captured["stderr_name"]))
    assert captured["argv"] == [
        "/bin/androidctld",
        "--port",
        "0",
        "--workspace-root",
        str(workspace_root),
        "--owner-id",
        "shell:self:1",
    ]
    assert captured["cwd"] == tmp_path
    assert stdout_name.parent == daemon_root / "logs"
    assert stderr_name == stdout_name


def test_resolve_daemon_client_polls_after_launched_daemon_loses_owner_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    winner_record = _active_record(
        pid=5678,
        port=9876,
        token="winner-secret",
        workspace_root=workspace_root.resolve().as_posix(),
    )
    launched = object()
    reads = 0
    events: list[str] = []

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda _record, owner_id: launched,
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, _record: _health_result(winner_record),
    )

    def fake_read_active_daemon_record(
        _workspace_root: Path,
    ) -> ActiveDaemonRecord | None:
        nonlocal reads
        reads += 1
        events.append(f"read:{reads}")
        if reads >= 4:
            return winner_record
        return None

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        del argv, kwargs
        events.append("launch-loser")
        return object()

    monkeypatch.setattr(
        "androidctl.daemon.discovery._read_active_daemon_record",
        fake_read_active_daemon_record,
    )
    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.time.sleep",
        lambda _seconds: None,
    )

    result = resolve_daemon_client(
        workspace_root=workspace_root,
        cwd=tmp_path,
        env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
    )

    assert result is launched
    assert events == ["read:1", "launch-loser", "read:2", "read:3", "read:4"]


def test_resolve_daemon_client_concurrent_same_owner_launches_return_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    winner_record = _active_record(
        pid=5678,
        port=9876,
        token="winner-secret",
        workspace_root=workspace_root.resolve().as_posix(),
    )
    start_barrier = threading.Barrier(2)
    launch_barrier = threading.Barrier(2)
    write_lock = threading.Lock()
    launches = 0
    results: list[str] = []
    errors: list[BaseException] = []

    _patch_launch_spec(monkeypatch)
    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: f"client:{record.pid}:{owner_id}",
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.try_get_healthy_daemon",
        lambda _client, record: _health_result(record),
    )

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        nonlocal launches
        del argv, kwargs
        with write_lock:
            launches += 1
        launch_barrier.wait(timeout=2.0)
        with write_lock:
            active_path = daemon_state_root(workspace_root) / "active.json"
            if not active_path.exists():
                _write_active_payload(workspace_root, winner_record.model_dump())
        return object()

    def run_resolve() -> None:
        try:
            start_barrier.wait(timeout=2.0)
            daemon = resolve_daemon_client(
                workspace_root=workspace_root,
                cwd=tmp_path,
                env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
            )
            assert isinstance(daemon, str)
            results.append(daemon)
        except BaseException as error:  # pragma: no cover - surfaced after join
            errors.append(error)

    monkeypatch.setattr("androidctl.daemon.discovery.subprocess.Popen", fake_popen)

    threads = [threading.Thread(target=run_resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert launches == 2
    assert results == ["client:5678:shell:self:1", "client:5678:shell:self:1"]


def test_resolve_daemon_client_fails_workspace_busy_for_mismatched_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from androidctl.daemon.client import DaemonApiError

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root, owner_id="shell:other:1")

    class BusyClient:
        def health(self, record):  # noqa: ANN001
            from androidctl.daemon.client import DaemonApiError

            raise DaemonApiError(
                code="WORKSPACE_BUSY",
                message="workspace daemon is owned by a different shell or agent",
                details={"ownerId": record.owner_id},
            )

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: BusyClient(),
    )

    with pytest.raises(DaemonApiError) as error:
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )

    assert error.value.code == "WORKSPACE_BUSY"


def test_resolve_daemon_client_owner_mismatch_version_mismatch_stays_workspace_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root, owner_id="shell:other:1")

    class VersionMismatchClient:
        def health(self, record):  # noqa: ANN001
            raise IncompatibleDaemonVersionError(
                expected_version="0.1.0",
                actual_version="0.1.1",
            )

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: VersionMismatchClient(),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("owner-conflict live daemon must not launch androidctld")
        ),
    )

    with pytest.raises(DaemonApiError) as error:
        resolve_daemon_client(
            workspace_root=workspace_root,
            cwd=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )

    assert error.value.code == "WORKSPACE_BUSY"


def test_discover_existing_daemon_client_fails_workspace_busy_for_mismatched_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from androidctl.daemon.client import DaemonApiError

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _write_active_record(workspace_root, owner_id="shell:other:1")

    class BusyClient:
        def health(self, record):  # noqa: ANN001
            raise DaemonApiError(
                code="WORKSPACE_BUSY",
                message="workspace daemon is owned by a different shell or agent",
                details={"ownerId": record.owner_id},
            )

    monkeypatch.setattr(
        "androidctl.daemon.discovery.DaemonClient.from_active_record",
        lambda record, owner_id: BusyClient(),
    )
    monkeypatch.setattr(
        "androidctl.daemon.discovery.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery-only close must not launch androidctld")
        ),
    )

    with pytest.raises(DaemonApiError) as error:
        discover_existing_daemon_client(
            workspace_root=workspace_root,
            env={"ANDROIDCTL_OWNER_ID": "shell:self:1"},
        )

    assert error.value.code == "WORKSPACE_BUSY"


def test_try_get_healthy_daemon_raises_on_invalid_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    client = DaemonClient(
        httpx.Client(base_url="http://127.0.0.1:8765", transport=transport),
        owner_id="shell:self:1",
    )

    with pytest.raises(DaemonProtocolError):
        try_get_healthy_daemon(client, _active_record())
