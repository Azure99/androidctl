from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import androidctld
import androidctld.daemon.server as daemon_server_module
from androidctl_contracts.daemon_api import OWNER_HEADER_NAME, TOKEN_HEADER_NAME
from androidctl_contracts.user_state import ActiveDaemonRecord
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.config import DaemonConfig
from androidctld.daemon.server import AndroidctldHttpServer


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class RunningDaemon:
    config: DaemonConfig
    record: ActiveDaemonRecord


def _start_daemon(tmp_path: Path) -> tuple[AndroidctldHttpServer, RunningDaemon]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = DaemonConfig(
        workspace_root=workspace_root,
        owner_id="shell:self:1",
        host="127.0.0.1",
        port=0,
    )
    server = AndroidctldHttpServer(
        config=config,
        token_store=DaemonTokenStore(config),
    )
    record = server.start()
    return server, RunningDaemon(config=config, record=record)


@pytest.fixture
def daemon_server(tmp_path: Path) -> Iterator[RunningDaemon]:
    server, daemon = _start_daemon(tmp_path)
    try:
        yield daemon
    finally:
        server.stop()


def _auth_headers(daemon: RunningDaemon) -> dict[str, str]:
    return {
        TOKEN_HEADER_NAME: daemon.record.token,
        OWNER_HEADER_NAME: daemon.config.owner_id,
    }


def _request(
    daemon: RunningDaemon,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
) -> HttpResponse:
    connection = http.client.HTTPConnection(
        daemon.record.host,
        daemon.record.port,
        timeout=2.0,
    )
    try:
        connection.request(method, path, body=body, headers=_auth_headers(daemon))
        response = connection.getresponse()
        return HttpResponse(
            status=response.status,
            headers={key.lower(): value for key, value in response.getheaders()},
            body=response.read(),
        )
    finally:
        connection.close()


def _raw_request(daemon: RunningDaemon, request: bytes) -> HttpResponse:
    with socket.create_connection(
        (daemon.record.host, daemon.record.port),
        timeout=2.0,
    ) as sock:
        sock.settimeout(2.0)
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        try:
            return HttpResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.getheaders()},
                body=response.read(),
            )
        finally:
            response.close()


def _raw_post_with_content_length(
    daemon: RunningDaemon,
    content_length_line: bytes,
) -> HttpResponse:
    request = b"".join(
        [
            b"POST /health HTTP/1.1\r\n",
            f"Host: {daemon.record.host}:{daemon.record.port}\r\n".encode("ascii"),
            f"{TOKEN_HEADER_NAME}: {daemon.record.token}\r\n".encode("ascii"),
            f"{OWNER_HEADER_NAME}: {daemon.config.owner_id}\r\n".encode("ascii"),
            content_length_line,
            b"Connection: close\r\n",
            b"\r\n",
        ]
    )
    return _raw_request(daemon, request)


def _raw_post_without_content_length(daemon: RunningDaemon) -> HttpResponse:
    request = b"".join(
        [
            b"POST /health HTTP/1.1\r\n",
            f"Host: {daemon.record.host}:{daemon.record.port}\r\n".encode("ascii"),
            f"{TOKEN_HEADER_NAME}: {daemon.record.token}\r\n".encode("ascii"),
            f"{OWNER_HEADER_NAME}: {daemon.config.owner_id}\r\n".encode("ascii"),
            b"Connection: close\r\n",
            b"\r\n",
        ]
    )
    return _raw_request(daemon, request)


def _raw_post_with_body(
    daemon: RunningDaemon,
    *,
    content_length: int,
    body: bytes,
) -> HttpResponse:
    request = b"".join(
        [
            b"POST /health HTTP/1.1\r\n",
            f"Host: {daemon.record.host}:{daemon.record.port}\r\n".encode("ascii"),
            f"{TOKEN_HEADER_NAME}: {daemon.record.token}\r\n".encode("ascii"),
            f"{OWNER_HEADER_NAME}: {daemon.config.owner_id}\r\n".encode("ascii"),
            f"Content-Length: {content_length}\r\n".encode("ascii"),
            b"Connection: close\r\n",
            b"\r\n",
            body,
        ]
    )
    return _raw_request(daemon, request)


def _json_payload(response: HttpResponse) -> dict[str, object]:
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/json")
    payload = json.loads(response.body.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _is_daemon_json_envelope(response: HttpResponse) -> bool:
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        return False
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("ok") in {True, False}


def test_get_health_with_valid_auth_returns_daemon_bad_request_json(
    daemon_server: RunningDaemon,
) -> None:
    response = _request(daemon_server, "GET", "/health")

    assert response.status == 400
    payload = _json_payload(response)
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "DAEMON_BAD_REQUEST"
    assert error["details"] == {"path": "/health", "method": "GET"}


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_unsupported_methods_are_rejected_before_daemon_json_envelope(
    daemon_server: RunningDaemon,
    method: str,
) -> None:
    response = _request(daemon_server, method, "/health")

    assert response.status == 501
    assert not _is_daemon_json_envelope(response)


def test_missing_content_length_is_treated_as_empty_body(
    daemon_server: RunningDaemon,
) -> None:
    response = _raw_post_without_content_length(daemon_server)

    assert response.status == 200
    payload = _json_payload(response)
    assert payload["ok"] is True
    result = payload["result"]
    assert isinstance(result, dict)
    assert result["service"] == "androidctld"


def test_health_response_server_header_uses_package_version_prefix(
    daemon_server: RunningDaemon,
) -> None:
    response = _request(daemon_server, "POST", "/health", body=b"{}")

    assert response.status == 200
    assert response.headers["server"].startswith(
        f"androidctld/{androidctld.__version__}"
    )


def test_legacy_v1_health_path_returns_path_not_found_with_valid_auth(
    daemon_server: RunningDaemon,
) -> None:
    response = _request(daemon_server, "POST", "/v1/health", body=b"{}")

    assert response.status == 400
    payload = _json_payload(response)
    assert payload == {
        "ok": False,
        "error": {
            "code": "DAEMON_BAD_REQUEST",
            "message": "path not found",
            "retryable": False,
            "details": {"path": "/v1/health"},
        },
    }


@pytest.mark.parametrize(
    "content_length_line",
    [
        b"Content-Length:\r\n",
        b"Content-Length: \t  \r\n",
        b"Content-Length: abc\r\n",
        b"Content-Length: -1\r\n",
    ],
)
def test_malformed_content_length_returns_daemon_bad_request_json(
    daemon_server: RunningDaemon,
    content_length_line: bytes,
) -> None:
    response = _raw_post_with_content_length(daemon_server, content_length_line)

    assert response.status == 400
    payload = _json_payload(response)
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "DAEMON_BAD_REQUEST"
    assert error["message"] == "invalid Content-Length header"


def test_oversized_content_length_returns_daemon_bad_request_json(
    daemon_server: RunningDaemon,
) -> None:
    content_length = daemon_server_module.DAEMON_HTTP_MAX_REQUEST_BODY_BYTES + 1

    response = _raw_post_with_content_length(
        daemon_server,
        f"Content-Length: {content_length}\r\n".encode("ascii"),
    )

    assert response.status == 413
    payload = _json_payload(response)
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "DAEMON_BAD_REQUEST"
    assert error["details"] == {
        "reason": "request_body_too_large",
        "max": daemon_server_module.DAEMON_HTTP_MAX_REQUEST_BODY_BYTES,
        "contentLength": content_length,
    }


def test_incomplete_declared_body_returns_daemon_bad_request_json(
    daemon_server: RunningDaemon,
) -> None:
    response = _raw_post_with_body(daemon_server, content_length=2, body=b"{")

    assert response.status == 400
    payload = _json_payload(response)
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "DAEMON_BAD_REQUEST"
    assert error["details"] == {
        "reason": "incomplete_body",
        "contentLength": 2,
        "bytesRead": 1,
    }


def test_handler_body_read_timeout_returns_daemon_bad_request_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        daemon_server_module,
        "DAEMON_HTTP_SOCKET_TIMEOUT_SECONDS",
        0.2,
    )
    server, daemon = _start_daemon(tmp_path)
    try:
        with socket.create_connection(
            (daemon.record.host, daemon.record.port),
            timeout=2.0,
        ) as sock:
            sock.settimeout(2.0)
            request_head = b"".join(
                [
                    b"POST /health HTTP/1.1\r\n",
                    f"Host: {daemon.record.host}:{daemon.record.port}\r\n".encode(
                        "ascii"
                    ),
                    f"{TOKEN_HEADER_NAME}: {daemon.record.token}\r\n".encode("ascii"),
                    f"{OWNER_HEADER_NAME}: {daemon.config.owner_id}\r\n".encode(
                        "ascii"
                    ),
                    b"Content-Length: 2\r\n",
                    b"Connection: close\r\n",
                    b"\r\n",
                    b"{",
                ]
            )
            sock.sendall(request_head)
            response = http.client.HTTPResponse(sock)
            response.begin()
            try:
                http_response = HttpResponse(
                    status=response.status,
                    headers={
                        key.lower(): value for key, value in response.getheaders()
                    },
                    body=response.read(),
                )
            finally:
                response.close()
    finally:
        server.stop()

    assert http_response.status == 408
    payload = _json_payload(http_response)
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "DAEMON_BAD_REQUEST"
    assert error["details"] == {"reason": "request_body_timeout"}


def test_malformed_request_line_is_rejected_before_daemon_json_envelope(
    daemon_server: RunningDaemon,
) -> None:
    request = b"".join(
        [
            b"PO ST /health HTTP/1.1\r\n",
            f"Host: {daemon_server.record.host}:{daemon_server.record.port}\r\n".encode(
                "ascii"
            ),
            f"{TOKEN_HEADER_NAME}: {daemon_server.record.token}\r\n".encode("ascii"),
            f"{OWNER_HEADER_NAME}: {daemon_server.config.owner_id}\r\n".encode("ascii"),
            b"Content-Length: 0\r\n",
            b"Connection: close\r\n",
            b"\r\n",
        ]
    )

    response = _raw_request(daemon_server, request)

    assert response.status == 400
    assert not _is_daemon_json_envelope(response)
