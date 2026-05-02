"""Listener health evidence for daemon ownership records."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

from androidctl_contracts.daemon_api import (
    OWNER_HEADER_NAME,
    DaemonErrorEnvelope,
    DaemonSuccessEnvelope,
    HealthResult,
)
from androidctl_contracts.user_state import ActiveDaemonRecord
from androidctld import SERVICE_NAME
from androidctld.config import TOKEN_HEADER_NAME, normalize_loopback_host


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> Any: ...


class OwnershipHealthStatus(str, Enum):
    LIVE_MATCH = "live_match"
    LIVE_MISMATCH = "live_mismatch"
    UNREACHABLE = "unreachable"
    UNPROBEABLE = "unprobeable"


@dataclass(frozen=True)
class OwnershipHealthProbeResult:
    status: OwnershipHealthStatus
    token: str | None = None

    @property
    def is_live(self) -> bool:
        return self.status in {
            OwnershipHealthStatus.LIVE_MATCH,
            OwnershipHealthStatus.LIVE_MISMATCH,
        }


class OwnershipHealthProbe:
    _TIMEOUT_SECONDS = 0.15

    def __init__(
        self,
        *,
        opener_factory: Callable[[], _Opener | OpenerDirector] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._opener_factory = opener_factory or self._build_proxy_bypassing_opener
        self._timeout_seconds = timeout_seconds or self._TIMEOUT_SECONDS

    def probe(
        self,
        *,
        host: str,
        port: int,
        owner_id: str,
        workspace_root: str,
        expected_workspace_root: str,
        expected_owner_id: str,
        tokens: Iterable[str],
    ) -> OwnershipHealthProbeResult:
        if port <= 0:
            return OwnershipHealthProbeResult(OwnershipHealthStatus.UNPROBEABLE)
        try:
            normalized_host = normalize_loopback_host(host)
        except ValueError:
            return OwnershipHealthProbeResult(OwnershipHealthStatus.UNPROBEABLE)

        opener = self._opener_factory()
        url = f"http://{self._url_host(normalized_host)}:{port}/health"
        mismatch: OwnershipHealthProbeResult | None = None
        for token in self._probe_tokens(tokens):
            headers = {
                TOKEN_HEADER_NAME: token,
                OWNER_HEADER_NAME: owner_id,
            }
            request = Request(url, method="POST", data=b"{}", headers=headers)
            try:
                with opener.open(request, timeout=self._timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                try:
                    payload = json.loads(error.read().decode("utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    error.close()
                    continue
                error.close()
            except (OSError, URLError, ValueError, json.JSONDecodeError):
                continue

            status = self._classify_health_payload(
                payload,
                token=token,
                owner_id=owner_id,
                workspace_root=workspace_root,
                expected_workspace_root=expected_workspace_root,
                expected_owner_id=expected_owner_id,
            )
            if status is None:
                continue
            if status.status == OwnershipHealthStatus.LIVE_MATCH:
                return status
            if status.status == OwnershipHealthStatus.LIVE_MISMATCH:
                mismatch = status
                continue
            return status
        if mismatch is not None:
            return mismatch
        return OwnershipHealthProbeResult(OwnershipHealthStatus.UNREACHABLE)

    def _classify_health_payload(
        self,
        payload: object,
        *,
        token: str,
        owner_id: str,
        workspace_root: str,
        expected_workspace_root: str,
        expected_owner_id: str,
    ) -> OwnershipHealthProbeResult | None:
        try:
            envelope = DaemonSuccessEnvelope[HealthResult].model_validate(payload)
        except ValueError:
            return self._classify_error_payload(payload)

        result = envelope.result
        if result.service != SERVICE_NAME:
            return None
        if (
            result.workspace_root == expected_workspace_root
            and result.owner_id == expected_owner_id
            and workspace_root == expected_workspace_root
            and owner_id == expected_owner_id
        ):
            return OwnershipHealthProbeResult(
                OwnershipHealthStatus.LIVE_MATCH,
                token=token,
            )
        return OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MISMATCH)

    @staticmethod
    def _classify_error_payload(
        payload: object,
    ) -> OwnershipHealthProbeResult | None:
        try:
            envelope = DaemonErrorEnvelope.model_validate(payload)
        except ValueError:
            return None
        if envelope.error.code.value in {"DAEMON_UNAUTHORIZED", "WORKSPACE_BUSY"}:
            return OwnershipHealthProbeResult(OwnershipHealthStatus.LIVE_MISMATCH)
        return None

    def probe_active_record(
        self,
        record: ActiveDaemonRecord,
        *,
        expected_workspace_root: str,
        expected_owner_id: str,
    ) -> OwnershipHealthProbeResult:
        return self.probe(
            host=record.host,
            port=record.port,
            owner_id=record.owner_id,
            workspace_root=record.workspace_root,
            expected_workspace_root=expected_workspace_root,
            expected_owner_id=expected_owner_id,
            tokens=(record.token,),
        )

    @staticmethod
    def _unique_tokens(tokens: Iterable[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            token = token.strip()
            if not token or token in seen:
                continue
            unique.append(token)
            seen.add(token)
        return unique

    @classmethod
    def _probe_tokens(cls, tokens: Iterable[str]) -> list[str]:
        unique = cls._unique_tokens(tokens)
        return unique or [""]

    @staticmethod
    def _url_host(host: str) -> str:
        if ":" in host and not host.startswith("["):
            return f"[{host}]"
        return host

    @staticmethod
    def _build_proxy_bypassing_opener() -> OpenerDirector:
        return build_opener(ProxyHandler({}))
