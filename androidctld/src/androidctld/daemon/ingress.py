"""Daemon ingress boundary for authenticated request dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from androidctl_contracts.command_results import RetainedResultEnvelope
from androidctl_contracts.daemon_api import OWNER_HEADER_NAME
from androidctld.config import TOKEN_HEADER_NAME
from androidctld.errors import DaemonError, DaemonErrorCode, unauthorized


class DaemonDispatcher(Protocol):
    def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]: ...


@dataclass(frozen=True)
class IngressResult:
    status_code: int
    payload: dict[str, Any]
    shutdown_after_write: bool = False


class DaemonIngress:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None],
        owner_id_provider: Callable[[], str | None],
        dispatcher: DaemonDispatcher,
    ) -> None:
        self._token_provider = token_provider
        self._owner_id_provider = owner_id_provider
        self._dispatcher = dispatcher

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> IngressResult:
        self._require_token(headers)
        self._require_owner(headers)
        status_code, payload = self._dispatcher.handle(
            method=method,
            path=path,
            headers=headers,
            body=body,
        )
        return IngressResult(
            status_code=status_code,
            payload=payload,
            shutdown_after_write=_should_shutdown_after_write(
                method=method,
                path=path,
                status_code=status_code,
                payload=payload,
            ),
        )

    def _require_token(self, headers: dict[str, str]) -> None:
        expected = self._token_provider()
        supplied = self._header_value(headers, TOKEN_HEADER_NAME)
        if not expected or supplied != expected:
            raise unauthorized()

    def _require_owner(self, headers: dict[str, str]) -> None:
        owner_id = self._owner_id_provider()
        if not owner_id:
            return
        supplied = self._header_value(headers, OWNER_HEADER_NAME)
        if supplied == owner_id:
            return
        raise DaemonError(
            code=DaemonErrorCode.WORKSPACE_BUSY,
            message="workspace daemon is owned by a different shell or agent",
            retryable=False,
            details={"ownerId": owner_id},
            http_status=200,
        )

    def _header_value(self, headers: dict[str, str], header_name: str) -> str | None:
        for key, value in headers.items():
            if key.lower() == header_name.lower():
                return value.strip()
        return None


def _should_shutdown_after_write(
    *,
    method: str,
    path: str,
    status_code: int,
    payload: dict[str, Any],
) -> bool:
    if method != "POST" or path != "/runtime/close" or status_code != 200:
        return False
    try:
        result = RetainedResultEnvelope.model_validate(payload)
    except ValueError:
        return False
    return result.ok is True and result.command == "close"
