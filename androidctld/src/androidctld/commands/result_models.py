"""Canonical command success result models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, cast

from pydantic import (
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
)

from androidctl_contracts.command_catalog import (
    entry_for_result_command,
)
from androidctl_contracts.command_results import (
    ActionTargetPayload,
    CommandResultCore,
    RetainedResultEnvelope,
    TruthPayload,
)
from androidctl_contracts.command_results import (
    ArtifactPayload as SemanticArtifactPayload,
)
from androidctl_contracts.vocabulary import SemanticResultCode
from androidctld.artifacts.models import ScreenArtifacts
from androidctld.schema import ApiModel
from androidctld.schema.base import dump_api_model
from androidctld.semantics.public_models import PublicScreen, dump_public_screen

TrimmedString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]


def _strip_optional_string(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        return normalized
    return value


class CommandResultModel(ApiModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        alias_generator=ApiModel.model_config["alias_generator"],
        validate_by_alias=True,
        validate_by_name=True,
        use_enum_values=False,
        frozen=True,
    )


@dataclass(frozen=True, slots=True)
class SemanticResultAssemblyInput:
    """Internal host-owned inputs for public semantic result assembly."""

    app_payload: CommandAppPayload | None = None
    action_target: ActionTargetPayload | None = None
    execution_outcome: Literal[
        "dispatched",
        "notAttempted",
        "notApplicable",
        "unknown",
    ] = "dispatched"
    warnings: tuple[str, ...] = field(default_factory=tuple)


class CommandAppPayload(CommandResultModel):
    package_name: TrimmedString | None
    activity_name: str | None = None
    requested_package_name: TrimmedString | None = None
    resolved_package_name: TrimmedString | None = None
    match_type: Literal["exact", "alias"] | None = None

    @field_validator(
        "activity_name",
        "requested_package_name",
        "resolved_package_name",
        mode="before",
    )
    @classmethod
    def _normalize_activity_name(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_serializer(mode="wrap")
    def _serialize_model(self, handler: Any) -> dict[str, Any]:
        payload = cast(dict[str, Any], handler(self))
        if self.requested_package_name is None:
            payload.pop("requestedPackageName", None)
        if self.resolved_package_name is None:
            payload.pop("resolvedPackageName", None)
        if self.match_type is None:
            payload.pop("matchType", None)
        return payload


class CommandScreenPayload(CommandResultModel):
    screen_id: TrimmedString
    sequence: NonNegativeInt
    path_json: str | None = None

    @field_validator("path_json", mode="before")
    @classmethod
    def _normalize_paths(cls, value: object) -> object:
        return _strip_optional_string(value)


def semantic_artifact_payload(
    artifacts: ScreenArtifacts | None,
) -> SemanticArtifactPayload:
    if artifacts is None:
        return SemanticArtifactPayload()
    return SemanticArtifactPayload(
        screenshot_png=artifacts.screenshot_png,
        screen_xml=artifacts.screen_xml,
    )


_PAYLOAD_LIGHT_LOST_TRUTH_CODES = frozenset(
    {
        SemanticResultCode.DEVICE_UNAVAILABLE,
        SemanticResultCode.POST_ACTION_OBSERVATION_LOST,
    }
)


def semantic_screen_payload(
    public_screen: PublicScreen | None,
    *,
    app_payload: CommandAppPayload | None = None,
) -> dict[str, object] | None:
    if public_screen is None:
        return None
    payload = dump_public_screen(public_screen)
    if app_payload is None:
        return payload

    app = payload.get("app")
    if isinstance(app, dict):
        app.update(dump_api_model(app_payload))
    return payload


def retained_artifact_payload(artifacts: ScreenArtifacts | None) -> dict[str, Any]:
    if artifacts is None or artifacts.screenshot_png is None:
        return {}
    return {"screenshotPng": artifacts.screenshot_png}


def retained_result_envelope_kind(command: str) -> str:
    entry = entry_for_result_command(command)
    if entry is None:
        raise ValueError(f"unknown retained result command: {command!r}")
    if entry.retained_envelope_kind is None:
        raise ValueError(f"semantic command is not retained: {command!r}")
    return entry.retained_envelope_kind.value


def build_retained_success_result(
    *,
    command: str,
    artifacts: ScreenArtifacts | dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> RetainedResultEnvelope:
    return RetainedResultEnvelope(
        ok=True,
        command=command,
        envelope=retained_result_envelope_kind(command),
        artifacts=_coerce_retained_artifacts(artifacts),
        details={} if details is None else dict(details),
    )


def build_retained_failure_result(
    *,
    command: str,
    code: object,
    message: str,
    artifacts: ScreenArtifacts | dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> RetainedResultEnvelope:
    code_value = getattr(code, "value", code)
    return RetainedResultEnvelope(
        ok=False,
        command=command,
        envelope=retained_result_envelope_kind(command),
        code=str(code_value),
        message=message,
        artifacts=_coerce_retained_artifacts(artifacts),
        details={} if details is None else dict(details),
    )


_RETAINED_FAILURE_PROJECTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("screenshot", "ARTIFACT_ROOT_UNWRITABLE"): (
        "WORKSPACE_STATE_UNWRITABLE",
        "workspace",
    ),
    ("screenshot", "ARTIFACT_WRITE_FAILED"): (
        "WORKSPACE_STATE_UNWRITABLE",
        "workspace",
    ),
    ("connect", "DEVICE_AGENT_UNAUTHORIZED"): (
        "DEVICE_AGENT_UNAUTHORIZED",
        "device",
    ),
    ("connect", "DEVICE_AGENT_VERSION_MISMATCH"): (
        "DEVICE_AGENT_VERSION_MISMATCH",
        "device",
    ),
    ("screenshot", "DEVICE_AGENT_VERSION_MISMATCH"): (
        "DEVICE_AGENT_VERSION_MISMATCH",
        "device",
    ),
}

_RETAINED_REASON_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RETAINED_RELEASE_VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$"
)
_DEVICE_SERIAL_REASON_RE = re.compile(r"^(?:emulator-\d+|[A-Z0-9]{6,})$")
_FINGERPRINT_REASON_RE = re.compile(r"^(?:[0-9a-f]{16,}|[0-9a-f]{2}:){7,}[0-9a-f]{2}$")
_SAFE_TOKEN_REASON_VALUES = frozenset({"wrong-token"})


def build_projected_retained_failure_result(
    *,
    command: str,
    code: object,
    message: str,
    artifacts: ScreenArtifacts | dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    source_kind: str | None = None,
    operation: str | None = None,
) -> RetainedResultEnvelope:
    source_code = str(getattr(code, "value", code))
    public_code, mapped_source_kind = _project_retained_failure_code(
        command=command,
        source_code=source_code,
    )
    projected_source_kind = source_kind or mapped_source_kind
    return build_retained_failure_result(
        command=command,
        code=public_code,
        message=_project_retained_failure_message(
            command=command,
            source_code=source_code,
            message=message,
        ),
        artifacts=artifacts,
        details=_project_retained_failure_details(
            command=command,
            source_code=source_code,
            public_code=public_code,
            source_kind=projected_source_kind,
            operation=operation,
            details=details,
        ),
    )


def build_projected_retained_failure_result_for_error(
    *,
    command: str,
    error: Any,
    artifacts: ScreenArtifacts | dict[str, Any] | None = None,
    source_kind: str | None = None,
    operation: str | None = None,
) -> RetainedResultEnvelope:
    return build_projected_retained_failure_result(
        command=command,
        code=error.code,
        message=error.message,
        artifacts=artifacts,
        details=error.details,
        source_kind=source_kind,
        operation=operation,
    )


def _project_retained_failure_code(
    *,
    command: str,
    source_code: str,
) -> tuple[str, str | None]:
    projected = _RETAINED_FAILURE_PROJECTIONS.get((command, source_code))
    if projected is not None:
        return projected
    return source_code, None


def _project_retained_failure_message(
    *,
    command: str,
    source_code: str,
    message: str,
) -> str:
    del command, source_code
    return message


def _project_retained_failure_details(
    *,
    command: str,
    source_code: str,
    public_code: str,
    source_kind: str | None,
    operation: str | None,
    details: dict[str, Any] | None,
) -> dict[str, Any]:
    projected = _sanitize_retained_failure_details(details, command=command)
    normalized_operation = _stable_detail_scalar(operation)
    if normalized_operation is not None:
        projected["operation"] = normalized_operation
    normalized_source_kind = _stable_detail_scalar(source_kind)
    if public_code != source_code or normalized_source_kind is not None:
        projected["sourceCode"] = source_code
    if normalized_source_kind is not None:
        projected["sourceKind"] = normalized_source_kind
    return projected


def _sanitize_retained_failure_details(
    details: dict[str, Any] | None,
    *,
    command: str,
) -> dict[str, Any]:
    if not details:
        return {}
    projected: dict[str, Any] = {}
    reason = _stable_reason_detail(details.get("reason"), command=command)
    if reason is not None:
        projected["reason"] = reason
    expected_release_version = _stable_release_version_detail(
        details.get("expectedReleaseVersion")
    )
    if expected_release_version is not None:
        projected["expectedReleaseVersion"] = expected_release_version
    actual_release_version = _stable_release_version_detail(
        details.get("actualReleaseVersion")
    )
    if actual_release_version is not None:
        projected["actualReleaseVersion"] = actual_release_version
    return projected


def _stable_reason_detail(value: object, *, command: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized != value:
        return None
    lower = normalized.lower()
    if not _RETAINED_REASON_RE.fullmatch(normalized):
        return None
    if _DEVICE_SERIAL_REASON_RE.fullmatch(normalized):
        return None
    if _FINGERPRINT_REASON_RE.fullmatch(lower):
        return None
    if "token" in lower and lower not in _SAFE_TOKEN_REASON_VALUES:
        return None
    if any(
        marker in lower
        for marker in (
            "bearer",
            "://",
            "www.",
            ".androidctl",
            "artifact-root",
            "artifact_path",
            "artifact-path",
            "raw-rid",
            "raw_rid",
            "rawrid",
            "snapshot",
            "fingerprint",
        )
    ):
        return None
    if lower.startswith(("rid-", "rid_", "snapshot-", "snapshot_")):
        return None
    return normalized


def _stable_detail_scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float)):
        return value
    return None


def _stable_release_version_detail(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized != value:
        return None
    if len(normalized) > 32:
        return None
    if _RETAINED_RELEASE_VERSION_RE.fullmatch(normalized) is None:
        return None
    return normalized


def dump_retained_result_envelope(
    payload: RetainedResultEnvelope | dict[str, Any],
) -> dict[str, Any]:
    result = (
        payload
        if isinstance(payload, RetainedResultEnvelope)
        else RetainedResultEnvelope.model_validate(payload)
    )
    dumped = result.model_dump(by_alias=True, mode="json")
    if dumped.get("code") is None:
        dumped.pop("code", None)
    if dumped.get("message") is None:
        dumped.pop("message", None)
    return dumped


def _coerce_retained_artifacts(
    artifacts: ScreenArtifacts | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(artifacts, ScreenArtifacts):
        return retained_artifact_payload(artifacts)
    if artifacts is None:
        return {}
    return dict(artifacts)


def build_semantic_success_result(
    *,
    command: str,
    category: str,
    source_screen_id: str | None,
    next_screen: PublicScreen | None,
    next_screen_id: str | None = None,
    screen_payload: dict[str, object] | None = None,
    app_payload: CommandAppPayload | None = None,
    action_target: ActionTargetPayload | dict[str, object] | None = None,
    artifacts: ScreenArtifacts | None,
    continuity_status: str,
    execution_outcome: str,
    observation_quality: str = "authoritative",
    changed: bool | None = None,
    warnings: list[str] | None = None,
) -> CommandResultCore:
    resolved_screen_payload = (
        semantic_screen_payload(next_screen, app_payload=app_payload)
        if screen_payload is None
        else screen_payload
    )
    resolved_next_screen_id = (
        (None if next_screen is None else next_screen.screen_id)
        if next_screen_id is None
        else next_screen_id
    )
    truth_payload_kwargs: dict[str, object] = {
        "execution_outcome": execution_outcome,
        "continuity_status": continuity_status,
        "observation_quality": observation_quality,
    }
    if changed is not None:
        truth_payload_kwargs["changed"] = changed
    result_kwargs = {
        "ok": True,
        "command": command,
        "category": category,
        "payload_mode": "full",
        "truth": TruthPayload(**truth_payload_kwargs),
        "screen": resolved_screen_payload,
        "warnings": [] if warnings is None else warnings,
        "artifacts": semantic_artifact_payload(artifacts),
    }
    if source_screen_id is not None:
        result_kwargs["source_screen_id"] = source_screen_id
    if resolved_next_screen_id is not None:
        result_kwargs["next_screen_id"] = resolved_next_screen_id
    if action_target is not None:
        result_kwargs["action_target"] = action_target
    return CommandResultCore(**result_kwargs)


def build_semantic_failure_result(
    *,
    command: str,
    category: str,
    code: SemanticResultCode,
    message: str,
    execution_outcome: str = "notApplicable",
    source_screen_id: str | None,
    current_screen: PublicScreen | None,
    artifacts: ScreenArtifacts | None,
    continuity_status: str = "none",
    observation_quality: str = "none",
    changed: bool | None = None,
) -> CommandResultCore:
    effective_current_screen = (
        None if code in _PAYLOAD_LIGHT_LOST_TRUTH_CODES else current_screen
    )
    semantic_artifacts = (
        SemanticArtifactPayload()
        if code in _PAYLOAD_LIGHT_LOST_TRUTH_CODES
        else semantic_artifact_payload(artifacts)
    )
    if effective_current_screen is None:
        result_kwargs: dict[str, object] = {
            "ok": False,
            "command": command,
            "category": category,
            "payload_mode": "none",
            "code": code,
            "message": message,
            "truth": TruthPayload(
                execution_outcome=execution_outcome,
                continuity_status="none",
                observation_quality="none",
            ),
            "artifacts": semantic_artifacts,
        }
        if source_screen_id is not None:
            result_kwargs["source_screen_id"] = source_screen_id
        return CommandResultCore(**result_kwargs)
    truth_payload_kwargs: dict[str, object] = {
        "execution_outcome": execution_outcome,
        "continuity_status": continuity_status,
        "observation_quality": observation_quality,
    }
    if changed is not None:
        truth_payload_kwargs["changed"] = changed
    result_kwargs = {
        "ok": False,
        "command": command,
        "category": category,
        "code": code,
        "message": message,
        "payload_mode": "full",
        "next_screen_id": effective_current_screen.screen_id,
        "truth": TruthPayload(**truth_payload_kwargs),
        "screen": semantic_screen_payload(effective_current_screen),
        "warnings": [],
        "artifacts": semantic_artifacts,
    }
    if source_screen_id is not None:
        result_kwargs["source_screen_id"] = source_screen_id
    return CommandResultCore(**result_kwargs)
