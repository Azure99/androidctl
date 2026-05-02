"""Route dispatch for androidctld daemon."""

from __future__ import annotations

import json
from typing import Any

from androidctl_contracts.daemon_api import RuntimeGetResult, RuntimePayload
from androidctld import __version__
from androidctld.commands.service import CommandService
from androidctld.errors import bad_request
from androidctld.runtime import RuntimeKernel
from androidctld.runtime.screen_state import get_authoritative_current_basis
from androidctld.runtime.store import RuntimeStore
from androidctld.schema.daemon_api import (
    HealthResult,
    parse_command_run_request,
    require_empty_payload,
)


class DaemonService:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        command_service: CommandService,
        bound_owner_id: str | None = None,
    ) -> None:
        self._runtime_store = runtime_store
        self._runtime_kernel = RuntimeKernel(runtime_store)
        self._command_service = command_service
        self._bound_owner_id = bound_owner_id

    def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        del headers
        if method != "POST":
            raise bad_request(
                "use POST for daemon endpoints", {"path": path, "method": method}
            )
        payload = self._parse_body(body)
        if path == "/health":
            return 200, self._handle_health(payload)
        if path == "/runtime/get":
            return 200, self._handle_runtime_get(payload)
        if path == "/runtime/close":
            return 200, self._handle_runtime_close(payload)
        if path == "/commands/run":
            return 200, self._handle_commands_run(payload)
        raise bad_request("path not found", {"path": path})

    def _parse_body(self, body: bytes) -> dict[str, Any]:
        if not body or not body.strip():
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError as error:
            raise bad_request("request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise bad_request("request body must be a JSON object")
        return payload

    def _handle_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_empty_payload(payload, "health")
        runtime = self._runtime_store.get_runtime()
        return HealthResult(
            service="androidctld",
            version=__version__,
            workspace_root=runtime.workspace_root.as_posix(),
            owner_id=self._bound_owner_id or "",
        ).model_dump(mode="json")

    def _handle_runtime_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_empty_payload(payload, "runtime/get")
        runtime = self._runtime_kernel.ensure_runtime()
        basis = get_authoritative_current_basis(runtime)
        runtime_payload_kwargs: dict[str, Any] = {
            "workspace_root": runtime.workspace_root.as_posix(),
            "artifact_root": runtime.artifact_root.as_posix(),
            "status": runtime.status,
        }
        if basis is not None:
            runtime_payload_kwargs["current_screen_id"] = basis.screen_id
        return RuntimeGetResult(
            runtime=RuntimePayload(**runtime_payload_kwargs)
        ).model_dump(mode="json")

    def _handle_runtime_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_empty_payload(payload, "runtime/close")
        return self._command_service.close_runtime()

    def _handle_commands_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = parse_command_run_request(payload)
        return self._command_service.run(command=request.command)
