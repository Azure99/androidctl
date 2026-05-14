from __future__ import annotations

import errno
import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from androidctl_contracts.daemon_api import OWNER_HEADER_NAME, TOKEN_HEADER_NAME
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.config import DaemonConfig
from androidctld.daemon.ingress import IngressResult
from androidctld.daemon.server import AndroidctldHttpServer
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.protocol import RuntimeStatus
from androidctld.runtime.models import WorkspaceRuntime

from .test_runtime_api import BusyCommandService, FakeRuntimeStore


class CapturingWriter:
    def __init__(self, error: OSError | None = None) -> None:
        self._error = error
        self.body = b""
        self.write_count = 0

    def write(self, data: bytes) -> None:
        self.write_count += 1
        if self._error is not None:
            raise self._error
        self.body += data


class FakeHandler:
    def __init__(
        self,
        *,
        token: str,
        owner_id: str,
        path: str,
        writer: CapturingWriter,
        body: bytes = b"{}",
    ) -> None:
        self.command = "POST"
        self.path = path
        self.headers = {
            TOKEN_HEADER_NAME: token,
            OWNER_HEADER_NAME: owner_id,
            "Content-Length": str(len(body)),
        }
        self.rfile = io.BytesIO(body)
        self.wfile = writer
        self.status_codes: list[int] = []
        self.sent_headers: list[tuple[str, str]] = []
        self.close_connection = False

    def send_response(self, status_code: int) -> None:
        self.status_codes.append(status_code)

    def send_header(self, name: str, value: str) -> None:
        self.sent_headers.append((name, value))

    def end_headers(self) -> None:
        return None


def _server(tmp_path: Path) -> tuple[AndroidctldHttpServer, str, logging.Logger]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    token_store = DaemonTokenStore(config)
    runtime = WorkspaceRuntime(
        workspace_root=workspace_root,
        artifact_root=workspace_root / ".androidctl",
        runtime_path=workspace_root / ".androidctl" / "runtime.json",
        status=RuntimeStatus.READY,
    )
    logger = logging.getLogger(f"tests.androidctld.client_disconnect.{id(tmp_path)}")
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.INFO)
    server = AndroidctldHttpServer(
        config=config,
        token_store=token_store,
        runtime_store=FakeRuntimeStore(runtime),
        command_service=BusyCommandService(runtime),
        logger=logger,
    )
    token = token_store.current_token()
    return server, token, logger


def _handler(
    server: AndroidctldHttpServer,
    token: str,
    *,
    path: str = "/commands/run",
    writer: CapturingWriter,
) -> FakeHandler:
    return FakeHandler(
        token=token,
        owner_id=server._config.owner_id,
        path=path,
        writer=writer,
    )


def _json_body(writer: CapturingWriter) -> dict[str, Any]:
    payload = json.loads(writer.body.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _unexpected_failure_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "unexpected daemon failure" in record.getMessage()
    ]


def test_success_response_write_disconnect_is_normal_and_server_remains_usable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, token, logger = _server(tmp_path)
    caplog.set_level(logging.INFO, logger=logger.name)
    calls: list[str] = []

    def _success(**kwargs: Any) -> IngressResult:
        calls.append(str(kwargs["path"]))
        return IngressResult(
            status_code=200,
            payload={"path": kwargs["path"]},
            shutdown_after_write=False,
        )

    server._ingress.handle = _success  # type: ignore[method-assign]

    disconnected_writer = CapturingWriter(ConnectionAbortedError("client closed"))
    disconnected_handler = _handler(
        server,
        token,
        path="/commands/run",
        writer=disconnected_writer,
    )

    server._handle(disconnected_handler)

    assert disconnected_writer.write_count == 1
    assert disconnected_handler.status_codes == [200]
    assert disconnected_handler.close_connection is True
    assert server._closing is False
    assert _unexpected_failure_records(caplog) == []
    assert "client disconnected before response write completed" in caplog.text
    assert "response=success" in caplog.text

    healthy_writer = CapturingWriter()
    healthy_handler = _handler(server, token, path="/health", writer=healthy_writer)

    server._handle(healthy_handler)

    assert healthy_handler.status_codes == [200]
    assert _json_body(healthy_writer) == {
        "ok": True,
        "result": {"path": "/health"},
    }
    assert calls == ["/commands/run", "/health"]


def test_daemon_error_response_write_disconnect_does_not_escalate(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, token, logger = _server(tmp_path)
    caplog.set_level(logging.INFO, logger=logger.name)

    def _fail_with_daemon_error(**kwargs: Any) -> IngressResult:
        del kwargs
        raise DaemonError(
            code=DaemonErrorCode.DAEMON_BAD_REQUEST,
            message="bad request",
            retryable=False,
            details={"reason": "test"},
            http_status=400,
        )

    server._ingress.handle = _fail_with_daemon_error  # type: ignore[method-assign]
    writer = CapturingWriter(BrokenPipeError("client closed"))
    handler = _handler(server, token, path="/commands/run", writer=writer)

    server._handle(handler)

    assert writer.write_count == 1
    assert handler.status_codes == [400]
    assert handler.close_connection is True
    assert _unexpected_failure_records(caplog) == []
    assert "client disconnected before response write completed" in caplog.text
    assert "response=daemon_error" in caplog.text


def test_success_response_non_disconnect_write_failure_is_logged_once(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, token, logger = _server(tmp_path)
    caplog.set_level(logging.INFO, logger=logger.name)

    def _success(**kwargs: Any) -> IngressResult:
        return IngressResult(
            status_code=200,
            payload={"path": kwargs["path"]},
            shutdown_after_write=False,
        )

    server._ingress.handle = _success  # type: ignore[method-assign]
    writer = CapturingWriter(OSError(errno.EIO, "write failed"))
    handler = _handler(server, token, path="/commands/run", writer=writer)

    server._handle(handler)

    assert writer.write_count == 1
    assert handler.status_codes == [200]
    assert handler.close_connection is True
    assert "response write failed" in caplog.text
    assert "response=success" in caplog.text
    assert "client disconnected" not in caplog.text
    assert _unexpected_failure_records(caplog) == []


def test_internal_error_still_returns_internal_command_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, token, logger = _server(tmp_path)
    caplog.set_level(logging.INFO, logger=logger.name)

    def _boom(**kwargs: Any) -> IngressResult:
        del kwargs
        raise ValueError("boom")

    server._ingress.handle = _boom  # type: ignore[method-assign]
    writer = CapturingWriter()
    handler = _handler(server, token, path="/commands/run", writer=writer)

    server._handle(handler)

    assert handler.status_codes == [500]
    assert _json_body(writer) == {
        "ok": False,
        "error": {
            "code": "INTERNAL_COMMAND_FAILURE",
            "message": "unexpected daemon failure",
            "retryable": False,
            "details": {},
        },
    }
    assert len(_unexpected_failure_records(caplog)) == 1


def test_internal_error_response_write_disconnect_keeps_original_failure_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, token, logger = _server(tmp_path)
    caplog.set_level(logging.INFO, logger=logger.name)

    def _boom(**kwargs: Any) -> IngressResult:
        del kwargs
        raise ValueError("boom")

    server._ingress.handle = _boom  # type: ignore[method-assign]
    writer = CapturingWriter(BrokenPipeError("client closed"))
    handler = _handler(server, token, path="/commands/run", writer=writer)

    server._handle(handler)

    assert writer.write_count == 1
    assert handler.status_codes == [500]
    assert handler.close_connection is True
    assert len(_unexpected_failure_records(caplog)) == 1
    assert "client disconnected before response write completed" in caplog.text
    assert "response=internal_error" in caplog.text
