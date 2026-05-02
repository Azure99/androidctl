"""HTTP server for androidctld."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from typing import Any

from androidctld import SERVICE_NAME, __version__
from androidctld.auth.active_registry import ActiveDaemonRecord, ActiveDaemonRegistry
from androidctld.auth.token_store import DaemonTokenStore
from androidctld.commands.service import CommandService
from androidctld.config import DaemonConfig
from androidctld.daemon.active_slot import ActiveSlotCoordinator
from androidctld.daemon.envelope import error_envelope, success_envelope
from androidctld.daemon.http_host import DaemonHttpHost
from androidctld.daemon.ingress import DaemonIngress
from androidctld.daemon.service import DaemonService
from androidctld.errors import DaemonError, DaemonErrorCode, bad_request
from androidctld.logging import configure_logging
from androidctld.runtime.store import RuntimeStore
from androidctld.runtime_policy import (
    DAEMON_HTTP_MAX_REQUEST_BODY_BYTES,
    DAEMON_HTTP_SOCKET_TIMEOUT_SECONDS,
)


class AndroidctldHttpServer:
    def __init__(
        self,
        config: DaemonConfig,
        token_store: DaemonTokenStore | None = None,
        active_registry: ActiveDaemonRegistry | None = None,
        runtime_store: RuntimeStore | None = None,
        command_service: CommandService | None = None,
        logger: logging.Logger | None = None,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._token_store = token_store or DaemonTokenStore(self._config)
        self._active_registry = active_registry or ActiveDaemonRegistry(self._config)
        self._logger = logger or configure_logging()
        self._runtime_store = runtime_store or RuntimeStore(self._config)
        self._shutdown_callback = shutdown_callback or (lambda: None)
        self._closing = False
        self._shutdown_after_close_requested = False
        self._stop_lock = threading.Lock()
        self._stop_completed = False
        self._service = DaemonService(
            runtime_store=self._runtime_store,
            command_service=command_service or CommandService(self._runtime_store),
            bound_owner_id=self._config.owner_id,
        )
        self._ingress = DaemonIngress(
            token_provider=self._token_store.current_token,
            owner_id_provider=lambda: self._config.owner_id,
            dispatcher=self,
        )
        self._active_slot = ActiveSlotCoordinator(
            config=self._config,
            active_registry=self._active_registry,
            existing_token_reader=lambda: DaemonTokenStore.load_existing_token(
                self._config.token_file_path
            ),
        )
        self._http_host = DaemonHttpHost(config=self._config, logger=self._logger)

    @property
    def active_record(self) -> ActiveDaemonRecord | None:
        return self._active_slot.active_record

    def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        if (
            self._closing
            and method == "POST"
            and path
            in {
                "/runtime/get",
                "/runtime/close",
                "/commands/run",
            }
        ):
            raise DaemonError(
                code=DaemonErrorCode.RUNTIME_BUSY,
                message="daemon is shutting down",
                retryable=True,
                details={"reason": "daemon_shutting_down"},
                http_status=200,
            )
        return self._service.handle(
            method=method,
            path=path,
            headers=headers,
            body=body,
        )

    def start(self) -> ActiveDaemonRecord:
        with self._stop_lock:
            if self._http_host.is_running:
                raise RuntimeError("androidctld server is already running")

            self._closing = False
            self._shutdown_after_close_requested = False
            self._stop_completed = False
            self._active_slot.acquire()
            try:
                host, port = self._http_host.start(self._build_handler())
                active_record = self._active_slot.prepare(
                    host=host,
                    port=port,
                    token=self._token_store.current_token(),
                )
                self._http_host.wait_until_ready(record=active_record)
                self._active_slot.publish(active_record)
                self._logger.info("androidctld listening on %s:%s", host, port)
                return active_record
            except Exception:
                self._active_slot.clear_record()
                self._http_host.stop()
                self._active_slot.release_owner()
                self._stop_completed = True
                raise

    def stop(self) -> None:
        with self._stop_lock:
            if self._stop_completed:
                return
            try:
                if self._http_host.is_running:
                    self._http_host.stop()
            finally:
                self._active_slot.clear_record()
                self._active_slot.release_owner()
                self._stop_completed = True
                self._logger.info("androidctld stopped")

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = f"{SERVICE_NAME}/{__version__}"
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(DAEMON_HTTP_SOCKET_TIMEOUT_SECONDS)

            def do_POST(self) -> None:
                outer._handle(self)

            def do_GET(self) -> None:
                outer._handle(self)

            def log_message(self, fmt: str, *args: Any) -> None:
                outer._logger.info("http %s", fmt % args)

        return RequestHandler

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            body = self._read_body(handler)
            result = self._ingress.handle(
                method=handler.command,
                path=handler.path,
                headers=self._normalize_headers(handler),
                body=body,
            )
            if result.shutdown_after_write:
                self._enter_closing_gate()
                try:
                    self._write_json(
                        handler,
                        result.status_code,
                        success_envelope(result.payload),
                    )
                except OSError:
                    self._logger.info("close response write failed", exc_info=True)
                finally:
                    self._request_shutdown_after_close()
                return
            self._write_json(
                handler,
                result.status_code,
                success_envelope(result.payload),
            )
        except DaemonError as error:
            self._write_json(handler, error.http_status, error_envelope(error))
        except Exception:  # pragma: no cover - defensive fallback
            self._logger.exception("unexpected daemon failure")
            daemon_error = DaemonError(
                code=DaemonErrorCode.INTERNAL_COMMAND_FAILURE,
                message="unexpected daemon failure",
                retryable=False,
                details={},
                http_status=500,
            )
            self._write_json(
                handler, daemon_error.http_status, error_envelope(daemon_error)
            )

    def _enter_closing_gate(self) -> None:
        if self._closing:
            return
        self._closing = True

    def _request_shutdown_after_close(self) -> None:
        if self._shutdown_after_close_requested:
            return
        self._shutdown_after_close_requested = True
        self._shutdown_callback()

    def _read_body(self, handler: BaseHTTPRequestHandler) -> bytes:
        content_length_header = handler.headers.get("Content-Length")
        if content_length_header is None:
            return b""
        content_length_raw = content_length_header.strip()
        if not content_length_raw:
            raise bad_request("invalid Content-Length header")
        try:
            content_length = int(content_length_raw)
        except ValueError as error:
            raise bad_request("invalid Content-Length header") from error
        if content_length < 0:
            raise bad_request("invalid Content-Length header")
        if content_length > DAEMON_HTTP_MAX_REQUEST_BODY_BYTES:
            handler.close_connection = True
            raise DaemonError(
                code=DaemonErrorCode.DAEMON_BAD_REQUEST,
                message="request body too large",
                retryable=False,
                details={
                    "reason": "request_body_too_large",
                    "max": DAEMON_HTTP_MAX_REQUEST_BODY_BYTES,
                    "contentLength": content_length,
                },
                http_status=413,
            )
        try:
            body = handler.rfile.read(content_length)
        except TimeoutError as error:
            handler.close_connection = True
            raise DaemonError(
                code=DaemonErrorCode.DAEMON_BAD_REQUEST,
                message="request body read timed out",
                retryable=False,
                details={"reason": "request_body_timeout"},
                http_status=408,
            ) from error
        if len(body) != content_length:
            handler.close_connection = True
            raise bad_request(
                "incomplete request body",
                {
                    "reason": "incomplete_body",
                    "contentLength": content_length,
                    "bytesRead": len(body),
                },
            )
        return body

    def _normalize_headers(self, handler: BaseHTTPRequestHandler) -> dict[str, str]:
        return dict(handler.headers.items())

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status_code: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
