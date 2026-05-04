from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from androidctl.commands import run_pipeline
from androidctl.errors.mapping import map_exception
from androidctl.setup import adb as setup_adb
from androidctl_contracts.daemon_api import ConnectCommandPayload, ConnectionPayload


@dataclass(frozen=True)
class SetupVerificationResult:
    command: str
    envelope: str


class SetupVerificationError(RuntimeError):
    def __init__(self, code: str, layer: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.layer = layer
        self.message = message


DEFAULT_VERIFY_ATTEMPTS = 5
DEFAULT_VERIFY_RETRY_DELAY_SECONDS = 0.5
_RETRYABLE_VERIFY_CODES = {
    "ACCESSIBILITY_DISABLED",
    "ACCESSIBILITY_NOT_READY",
    "DEVICE_AGENT_UNAVAILABLE",
    "DEVICE_DISCONNECTED",
    "DEVICE_NOT_CONNECTED",
    "DEVICE_RPC_TRANSPORT_RESET",
}


def verify_setup_readiness(
    *,
    serial: str,
    token: str,
    workspace_root: Path | None,
    attempts: int = DEFAULT_VERIFY_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_VERIFY_RETRY_DELAY_SECONDS,
) -> SetupVerificationResult:
    normalized_attempts = max(1, attempts)
    last_error: SetupVerificationError | None = None
    for attempt_index in range(normalized_attempts):
        try:
            return _verify_setup_readiness_once(
                serial=serial,
                token=token,
                workspace_root=workspace_root,
            )
        except SetupVerificationError as error:
            last_error = error
            if (
                attempt_index == normalized_attempts - 1
                or not _is_retryable_verify_error(error)
            ):
                raise
            time.sleep(max(0.0, retry_delay_seconds))
    if last_error is not None:
        raise last_error
    raise SetupVerificationError(
        code="SETUP_VERIFY_FAILED",
        layer="daemon",
        message="daemon connect/readiness verification failed",
    )


def _verify_setup_readiness_once(
    *,
    serial: str,
    token: str,
    workspace_root: Path | None,
) -> SetupVerificationResult:
    request = run_pipeline.CliCommandRequest(
        public_command="connect",
        command=ConnectCommandPayload(
            kind="connect",
            connection=ConnectionPayload(
                mode="adb",
                token=token,
                serial=serial,
            ),
        ),
        workspace_root=workspace_root,
    )
    verification_error: SetupVerificationError | None = None
    try:
        outcome = run_pipeline.run_command(request, run_pipeline.build_context())
    except Exception as error:
        verification_error = _verification_error_from_exception(error, token=token)
    if verification_error is not None:
        raise verification_error

    payload = outcome.payload
    if payload.get("ok") is not True:
        raise _verification_error_from_payload(payload, token=token)
    command = _payload_string(payload, "command")
    envelope = _payload_string(payload, "envelope")
    if command != "connect" or envelope != "bootstrap":
        raise SetupVerificationError(
            code="SETUP_VERIFY_FAILED",
            layer="daemon",
            message="daemon connect returned an unexpected readiness result",
        )
    return SetupVerificationResult(
        command=command,
        envelope=envelope,
    )


def _is_retryable_verify_error(error: SetupVerificationError) -> bool:
    return error.code in _RETRYABLE_VERIFY_CODES


def _verification_error_from_payload(
    payload: dict[str, object],
    *,
    token: str,
) -> SetupVerificationError:
    code = _payload_string(payload, "code") or "SETUP_VERIFY_FAILED"
    message = _payload_string(payload, "message") or (
        "daemon connect/readiness verification failed"
    )
    return SetupVerificationError(
        code=code,
        layer=verification_layer_for_code(code),
        message=_redact_sensitive_text(message, token=token),
    )


def _verification_error_from_exception(
    error: Exception,
    *,
    token: str,
) -> SetupVerificationError:
    mapped_error = error
    if isinstance(error, run_pipeline.PreDispatchCommandError):
        mapped_error = error.cause
    public_error = map_exception(mapped_error)
    return SetupVerificationError(
        code=public_error.code,
        layer=verification_layer_for_code(public_error.code),
        message=_redact_sensitive_text(public_error.message, token=token),
    )


def verification_layer_for_code(code: str) -> str:
    if code in {"DEVICE_AGENT_UNAUTHORIZED"}:
        return "auth"
    if code in {"ACCESSIBILITY_NOT_READY", "ACCESSIBILITY_DISABLED"}:
        return "accessibility"
    if code in {
        "DEVICE_AGENT_UNAVAILABLE",
        "DEVICE_DISCONNECTED",
        "DEVICE_NOT_CONNECTED",
    }:
        return "server"
    return "daemon"


def _payload_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _redact_sensitive_text(text: str, *, token: str) -> str:
    return setup_adb.redact_adb_output(text, sensitive_values=(token,))
