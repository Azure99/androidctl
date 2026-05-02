"""JSON envelope helpers."""

from __future__ import annotations

from typing import Any

from androidctl_contracts.daemon_api import DaemonErrorEnvelope
from androidctl_contracts.errors import DaemonError as ContractDaemonError
from androidctl_contracts.errors import DaemonErrorCode as ContractDaemonErrorCode
from androidctld.errors import DaemonError


def success_envelope(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": result,
    }


def error_envelope(error: DaemonError) -> dict[str, Any]:
    try:
        contract_error = error.to_contract_error()
    except ValueError:
        contract_error = ContractDaemonError(
            code=ContractDaemonErrorCode.INTERNAL_COMMAND_FAILURE,
            message="unexpected daemon failure",
            retryable=False,
            details={},
        )
    return DaemonErrorEnvelope(error=contract_error).model_dump()
