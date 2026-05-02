"""HTTP host lifecycle and readiness probing for androidctld."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

from androidctl_contracts.daemon_api import (
    OWNER_HEADER_NAME,
    DaemonSuccessEnvelope,
    HealthResult,
)
from androidctld import SERVICE_NAME
from androidctld.auth.active_registry import ActiveDaemonRecord
from androidctld.config import TOKEN_HEADER_NAME, DaemonConfig


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> Any: ...


class DaemonHttpHost:
    _READY_TIMEOUT_SECONDS = 2.0
    _READY_POLL_INTERVAL_SECONDS = 0.05

    def __init__(
        self,
        *,
        config: DaemonConfig,
        logger: logging.Logger,
        opener_factory: Callable[[], _Opener | OpenerDirector] | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._opener_factory: Callable[[], _Opener | OpenerDirector]
        self._opener_factory = opener_factory or self._build_proxy_bypassing_opener
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def ready_poll_interval_seconds(self) -> float:
        return self._READY_POLL_INTERVAL_SECONDS

    def start(self, handler_class: type[BaseHTTPRequestHandler]) -> tuple[str, int]:
        if self._server is not None:
            raise RuntimeError("androidctld server is already running")
        host = self._config.host
        httpd = ThreadingHTTPServer((host, self._config.port), handler_class)
        httpd.daemon_threads = True
        try:
            thread = threading.Thread(
                target=httpd.serve_forever,
                name="androidctld-http",
                daemon=True,
            )
            thread.start()
        except Exception:
            httpd.server_close()
            raise
        self._server = httpd
        self._thread = thread
        _, port = httpd.server_address[:2]
        return host, port

    def wait_until_ready(
        self,
        *,
        record: ActiveDaemonRecord,
        owner_id: str | None = None,
    ) -> None:
        deadline = time.monotonic() + self._READY_TIMEOUT_SECONDS
        health_url = f"http://{record.host}:{record.port}/health"
        headers = {
            TOKEN_HEADER_NAME: record.token,
            OWNER_HEADER_NAME: owner_id or self._config.owner_id,
        }
        opener = self._opener_factory()
        while True:
            request = Request(health_url, method="POST", data=b"{}", headers=headers)
            try:
                with opener.open(
                    request, timeout=self._READY_POLL_INTERVAL_SECONDS
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                envelope = DaemonSuccessEnvelope[HealthResult].model_validate(payload)
                if envelope.result.service == SERVICE_NAME:
                    return
            except HTTPError as error:
                error.close()
            except (OSError, URLError, ValueError, json.JSONDecodeError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "timed out waiting for androidctld readiness health check"
                )
            time.sleep(self._READY_POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    @staticmethod
    def _build_proxy_bypassing_opener() -> OpenerDirector:
        return build_opener(ProxyHandler({}))
