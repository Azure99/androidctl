from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from androidctl_contracts.command_catalog import (
    entry_for_daemon_kind,
    runtime_close_entry,
)
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import (
    OWNER_HEADER_NAME,
    TOKEN_HEADER_NAME,
    CommandRunRequest,
    DaemonErrorEnvelope,
    HealthResult,
    RuntimeGetResult,
    RuntimePayload,
    WaitCommandPayload,
)
from androidctl_contracts.user_state import ActiveDaemonRecord
from androidctl_contracts.vocabulary import PublicResultFamily
from pydantic import BaseModel, ValidationError

from androidctl import __version__ as ANDROIDCTL_VERSION

ModelT = TypeVar("ModelT", bound=BaseModel)
CommandResultPayload = CommandResultCore | RetainedResultEnvelope | ListAppsResult

_DEFAULT_DAEMON_TIMEOUT_SECONDS = 5.0
_LONG_REQUEST_READ_TIMEOUT_GRACE_SECONDS = 2.0


class DaemonProtocolError(RuntimeError):
    pass


class IncompatibleDaemonError(DaemonProtocolError):
    pass


class IncompatibleDaemonVersionError(IncompatibleDaemonError):
    def __init__(self, *, expected_version: str, actual_version: str) -> None:
        super().__init__(
            "androidctl/androidctld release version mismatch: "
            f"cli={expected_version} daemon={actual_version}"
        )
        self.expected_version = expected_version
        self.actual_version = actual_version


class DaemonApiError(RuntimeError):
    def __init__(
        self,
        *,
        code: str = "DAEMON_API_ERROR",
        message: str = "daemon request failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class DaemonClient:
    def __init__(
        self,
        http_client: httpx.Client,
        *,
        owner_id: str | None = None,
        token: str | None = None,
    ) -> None:
        self._http = http_client
        self._owner_id = owner_id
        self._token = token

    @classmethod
    def from_active_record(
        cls,
        record: ActiveDaemonRecord,
        *,
        owner_id: str,
        http_client: httpx.Client | None = None,
    ) -> DaemonClient:
        base_url = f"http://{record.host}:{record.port}"
        return cls(
            http_client or httpx.Client(base_url=base_url, trust_env=False),
            owner_id=owner_id,
            token=record.token,
        )

    def health(self, record: ActiveDaemonRecord | None = None) -> HealthResult:
        result = self._post_json(
            "/health",
            payload={},
            token=self._resolve_token(record),
        )
        try:
            health = HealthResult.model_validate(result)
        except ValidationError as error:
            if _is_legacy_health_schema_failure(error):
                raise IncompatibleDaemonError(
                    "androidctl/androidctld health payload is incompatible; "
                    "install matching androidctl and androidctld versions"
                ) from error
            raise DaemonProtocolError("invalid health response schema") from error

        if health.service != "androidctld":
            raise DaemonProtocolError(
                f"incompatible daemon service: {health.service!r}"
            )
        if record is not None:
            if health.workspace_root != record.workspace_root:
                raise DaemonProtocolError("health response workspace root mismatch")
            if health.owner_id != record.owner_id:
                raise DaemonProtocolError("health response owner id mismatch")
        if health.version != ANDROIDCTL_VERSION:
            raise IncompatibleDaemonVersionError(
                expected_version=ANDROIDCTL_VERSION,
                actual_version=health.version,
            )
        return health

    def get_runtime(
        self,
        record: ActiveDaemonRecord | None = None,
    ) -> RuntimePayload:
        result = self._post_json(
            "/runtime/get",
            payload={},
            token=self._resolve_token(record),
        )
        return _validate_model(
            RuntimeGetResult,
            result,
            message="invalid runtime/get response schema",
        ).runtime

    def close_runtime(
        self,
        record: ActiveDaemonRecord | None = None,
    ) -> RetainedResultEnvelope:
        result = self._post_json(
            "/runtime/close",
            payload={},
            token=self._resolve_token(record),
        )
        entry = runtime_close_entry()
        close_result = _validate_model(
            RetainedResultEnvelope,
            result,
            message="invalid runtime/close response schema",
        )
        _assert_result_command(close_result, entry.result_command)
        return close_result

    def run_command(
        self,
        *,
        request: CommandRunRequest,
        record: ActiveDaemonRecord | None = None,
    ) -> CommandResultPayload:
        result = self._post_json(
            "/commands/run",
            payload=request.model_dump(exclude_none=True, exclude_defaults=True),
            token=self._resolve_token(record),
            timeout=_command_request_timeout(request),
        )
        entry = entry_for_daemon_kind(request.command.kind)
        if entry is None:
            raise DaemonProtocolError(
                f"unknown command catalog entry for kind={request.command.kind!r}"
            )
        if entry.result_family is PublicResultFamily.SEMANTIC:
            command_result: CommandResultPayload = _validate_model(
                CommandResultCore,
                result,
                message="invalid commands/run response schema",
            )
        elif entry.result_family is PublicResultFamily.RETAINED:
            command_result = _validate_model(
                RetainedResultEnvelope,
                result,
                message="invalid commands/run response schema",
            )
        elif entry.result_family is PublicResultFamily.LIST_APPS:
            command_result = _validate_model(
                ListAppsResult,
                result,
                message="invalid commands/run response schema",
            )
        else:
            raise DaemonProtocolError(
                f"unsupported result family {entry.result_family!r}"
            )
        _assert_result_command(command_result, entry.result_command)
        return command_result

    def _resolve_token(self, record: ActiveDaemonRecord | None) -> str:
        if record is not None:
            return record.token
        if self._token:
            return self._token
        raise DaemonProtocolError("daemon token is not configured")

    def _post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        token: str,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        headers = {TOKEN_HEADER_NAME: token}
        if self._owner_id is not None:
            headers[OWNER_HEADER_NAME] = self._owner_id
        response = self._http.post(
            path,
            headers=headers,
            json=payload,
            timeout=timeout or _default_request_timeout(),
        )
        envelope: dict[str, Any] | None = None
        envelope_error: DaemonProtocolError | None = None
        try:
            envelope = self._parse_json_envelope(response)
        except DaemonProtocolError as error:
            envelope_error = error

        if envelope is None:
            response.raise_for_status()
            assert envelope_error is not None
            raise envelope_error

        ok = envelope.get("ok")
        if ok is False:
            typed_error = _validate_model(
                DaemonErrorEnvelope,
                envelope,
                message="invalid daemon error envelope",
            ).error
            raise DaemonApiError(
                code=typed_error.code.value,
                message=typed_error.message,
                details=dict(typed_error.details),
            )
        if response.is_error:
            response.raise_for_status()
        if ok is not True:
            raise DaemonProtocolError("daemon success envelope missing ok=true")

        result = envelope.get("result")
        if not isinstance(result, dict):
            raise DaemonProtocolError("daemon response missing 'result' payload")
        return result

    def _parse_json_envelope(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise DaemonProtocolError("daemon response is not valid JSON") from error
        if not isinstance(payload, dict):
            raise DaemonProtocolError("daemon response is not a JSON object")
        return payload


def try_get_healthy_daemon(
    client: DaemonClient,
    record: ActiveDaemonRecord,
) -> HealthResult | None:
    try:
        return client.health(record)
    except (DaemonApiError, httpx.RequestError, httpx.HTTPStatusError):
        return None


def _command_request_timeout(request: CommandRunRequest) -> httpx.Timeout:
    return _timeout_with_read_budget(_command_read_budget_ms(request))


def _command_read_budget_ms(request: CommandRunRequest) -> int | None:
    if not isinstance(request.command, WaitCommandPayload):
        return None
    return _optional_non_negative_int(request.command.timeout_ms)


def _timeout_with_read_budget(read_budget_ms: int | None) -> httpx.Timeout:
    read_timeout = _DEFAULT_DAEMON_TIMEOUT_SECONDS
    if read_budget_ms is not None:
        read_timeout = max(
            _DEFAULT_DAEMON_TIMEOUT_SECONDS,
            (read_budget_ms / 1000.0) + _LONG_REQUEST_READ_TIMEOUT_GRACE_SECONDS,
        )
    return httpx.Timeout(
        connect=_DEFAULT_DAEMON_TIMEOUT_SECONDS,
        read=read_timeout,
        write=_DEFAULT_DAEMON_TIMEOUT_SECONDS,
        pool=_DEFAULT_DAEMON_TIMEOUT_SECONDS,
    )


def _default_request_timeout() -> httpx.Timeout:
    return _timeout_with_read_budget(None)


def _optional_non_negative_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _validate_model(
    model_type: type[ModelT],
    payload: object,
    *,
    message: str,
) -> ModelT:
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise DaemonProtocolError(message) from error


def _assert_result_command(
    result: CommandResultPayload,
    expected_command: str,
) -> None:
    if result.command != expected_command:
        raise DaemonProtocolError(
            "daemon result command mismatch: "
            f"expected {expected_command!r}, got {result.command!r}"
        )


def _is_legacy_health_schema_failure(error: ValidationError) -> bool:
    errors = error.errors()
    if len(errors) != 1:
        return False
    first_error = errors[0]
    location = first_error.get("loc")
    return first_error.get("type") == "extra_forbidden" and location == ("apiVersion",)
