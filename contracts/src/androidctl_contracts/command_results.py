"""Shared semantic command result payload models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BeforeValidator,
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from ._wire_helpers import _drop_unset_keys, _validate_absolute_path
from .base import DaemonWireModel
from .command_catalog import (
    RETAINED_RESULT_COMMAND_NAMES,
    SEMANTIC_RESULT_COMMAND_NAMES,
    entry_for_retained_result_command,
    entry_for_semantic_result_command,
    result_family_for_command,
)
from .public_screen import PUBLIC_REF_RE, PublicScreen
from .vocabulary import (
    ContinuityStatus,
    ExecutionOutcome,
    ObservationQuality,
    PayloadMode,
    PublicResultCategory,
    PublicResultFamily,
    RetainedEnvelopeKind,
    SemanticResultCode,
)


def _validate_json_true(value: Any) -> Any:
    if value is not True:
        raise ValueError("ok must be JSON boolean true")
    return value


_TrimmedString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
_StrictTrue: TypeAlias = Annotated[Literal[True], BeforeValidator(_validate_json_true)]

_ARTIFACT_WARNING_TOKENS = {
    "ARTIFACT_SCREEN_XML_GARBAGE_COLLECTED",
    "ARTIFACT_SCREEN_XML_MISSING",
    "artifactGarbageCollected",
    "artifactMissing",
}
ActionTargetIdentityStatus: TypeAlias = Literal[
    "sameRef",
    "successor",
    "gone",
    "unconfirmed",
]
ActionTargetEvidence: TypeAlias = Literal[
    "liveRef",
    "refRepair",
    "requestTarget",
    "resolvedTarget",
    "reusedRef",
    "fingerprintRematch",
    "focusConfirmation",
    "typeConfirmation",
    "submitConfirmation",
    "attributedRoute",
    "targetGone",
    "publicChange",
    "ambiguousSuccessor",
]

_ACTION_TARGET_COMMANDS = {"focus", "type", "submit"}
_PAYLOAD_LIGHT_LOST_TRUTH_CODES = {
    SemanticResultCode.DEVICE_UNAVAILABLE,
    SemanticResultCode.POST_ACTION_OBSERVATION_LOST,
}


def _drop_none_alias_keys(
    data: dict[str, Any],
    *,
    aliases: set[str],
) -> dict[str, Any]:
    for alias in aliases:
        if data.get(alias) is None:
            data.pop(alias, None)
    return data


def _validate_frozen_value(
    field_name: str,
    value: str,
    *,
    allowed: set[str],
) -> str:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {allowed_values}")
    return value


def _validate_screenshot_png(value: str | None) -> str | None:
    path = _validate_absolute_path(value)
    if path is None:
        return None

    normalized = path.replace("\\", "/")
    if not _path_is_in_namespace(normalized, ".androidctl/screenshots"):
        raise ValueError("screenshotPng must point into .androidctl/screenshots")

    return path


def _validate_screen_xml(value: str | None) -> str | None:
    path = _validate_absolute_path(value)
    if path is None:
        return None

    normalized = path.replace("\\", "/")
    if not _path_is_in_namespace(normalized, ".androidctl/artifacts/screens"):
        raise ValueError("screenXml must point into .androidctl/artifacts/screens")

    return path


def _path_is_in_namespace(normalized_path: str, namespace: str) -> bool:
    segments = [segment for segment in normalized_path.split("/") if segment]
    namespace_segments = namespace.split("/")
    if ".." in segments:
        return False
    return any(
        segments[index : index + len(namespace_segments)] == namespace_segments
        for index in range(len(segments) - len(namespace_segments) + 1)
    )


def _find_snake_case_key_path(value: object, *, path: str) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            key_path = f"{path}.{key}"
            if "_" in key:
                return key_path
            nested_path = _find_snake_case_key_path(item, path=key_path)
            if nested_path is not None:
                return nested_path
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            nested_path = _find_snake_case_key_path(item, path=f"{path}[{index}]")
            if nested_path is not None:
                return nested_path
    return None


def _artifact_payload_is_empty(artifacts: ArtifactPayload) -> bool:
    return artifacts.screenshot_png is None and artifacts.screen_xml is None


class ArtifactPayload(DaemonWireModel):
    """Published artifact pointers for a semantic command result."""

    screenshot_png: str | None = None
    screen_xml: str | None = None

    @field_validator("screenshot_png")
    @classmethod
    def validate_screenshot_png(cls, value: str | None) -> str | None:
        return _validate_screenshot_png(value)

    @field_validator("screen_xml")
    @classmethod
    def validate_screen_xml(cls, value: str | None) -> str | None:
        return _validate_screen_xml(value)

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        return _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={"screen_xml", "screenshot_png"},
        )


class RetainedResultEnvelope(DaemonWireModel):
    """Stable retained envelope shape for non-semantic public command results."""

    ok: bool
    command: str
    envelope: RetainedEnvelopeKind
    code: str | None = None
    message: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        family = result_family_for_command(value)
        if family is PublicResultFamily.SEMANTIC:
            raise ValueError("semantic commands are not retained envelopes")
        if family is PublicResultFamily.LIST_APPS:
            raise ValueError(
                "list-apps command is not a retained result family; "
                "use ListAppsResult"
            )
        return _validate_frozen_value(
            "command",
            value,
            allowed=RETAINED_RESULT_COMMAND_NAMES,
        )

    @model_validator(mode="after")
    def validate_envelope_kind(self) -> RetainedResultEnvelope:
        catalog_entry = entry_for_retained_result_command(self.command)
        if catalog_entry is None or catalog_entry.retained_envelope_kind is None:
            raise ValueError(f"command={self.command!r} is not a retained command")
        if self.envelope != catalog_entry.retained_envelope_kind:
            raise ValueError(
                "envelope must match retained command catalog mapping for "
                "command="
                f"{self.command!r}: expected "
                f"{catalog_entry.retained_envelope_kind.value!r}"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        return _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={"code", "message"},
        )


class TruthPayload(DaemonWireModel):
    execution_outcome: ExecutionOutcome
    continuity_status: ContinuityStatus
    observation_quality: ObservationQuality
    changed: bool | None = None

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        return _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={"changed"},
        )


class ActionTargetPayload(DaemonWireModel):
    """Public-safe identity outcome for semantic focus/type/submit actions."""

    source_ref: str
    source_screen_id: str = Field(min_length=1, strict=True)
    subject_ref: str
    next_screen_id: str = Field(min_length=1, strict=True)
    identity_status: ActionTargetIdentityStatus
    evidence: tuple[ActionTargetEvidence, ...]
    dispatched_ref: str | None = None
    next_ref: str | None = None

    @field_validator("source_ref", "subject_ref", "dispatched_ref", "next_ref")
    @classmethod
    def validate_public_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not PUBLIC_REF_RE.fullmatch(value):
            raise ValueError("actionTarget refs must be public refs like n1")
        return value

    @field_validator("evidence", mode="before")
    @classmethod
    def coerce_evidence(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("evidence")
    @classmethod
    def validate_evidence(
        cls, value: tuple[ActionTargetEvidence, ...]
    ) -> tuple[ActionTargetEvidence, ...]:
        if not value:
            raise ValueError("actionTarget evidence must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("actionTarget evidence entries must be unique")
        return value

    @model_validator(mode="after")
    def validate_identity_invariants(self) -> ActionTargetPayload:
        if self.identity_status == "sameRef":
            if self.next_ref != self.subject_ref:
                raise ValueError("sameRef requires nextRef to equal subjectRef")
        elif self.identity_status == "successor":
            if self.next_ref is None:
                raise ValueError("successor requires nextRef")
            if self.next_ref == self.subject_ref:
                raise ValueError("successor requires nextRef to differ from subjectRef")
        elif (
            self.identity_status in {"gone", "unconfirmed"}
            and self.next_ref is not None
        ):
            raise ValueError("gone/unconfirmed require nextRef to be absent")
        return self

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        dumped = _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={"dispatched_ref", "next_ref"},
        )
        _drop_none_alias_keys(dumped, aliases={"dispatchedRef", "nextRef"})
        return dumped


class CommandResultCore(DaemonWireModel):
    """Stable semantic result shape shared across CLI and daemon."""

    ok: bool
    command: str
    category: PublicResultCategory
    payload_mode: PayloadMode
    source_screen_id: str | None = None
    next_screen_id: str | None = None
    code: SemanticResultCode | None = None
    message: str | None = None
    truth: TruthPayload
    action_target: ActionTargetPayload | None = None
    screen: PublicScreen | None = None
    uncertainty: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: ArtifactPayload = Field(default_factory=ArtifactPayload)

    @field_validator("screen", mode="before")
    @classmethod
    def validate_screen_alias_form_nested_payload(cls, value: object) -> object:
        snake_case_path = _find_snake_case_key_path(value, path="screen")
        if snake_case_path is not None:
            raise ValueError(
                f"screen payload must use alias-form keys; found {snake_case_path}"
            )
        return value

    @field_validator("action_target", mode="before")
    @classmethod
    def validate_action_target_alias_form_nested_payload(cls, value: object) -> object:
        snake_case_path = _find_snake_case_key_path(value, path="actionTarget")
        if snake_case_path is not None:
            raise ValueError(
                "actionTarget payload must use alias-form keys; "
                f"found {snake_case_path}"
            )
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        family = result_family_for_command(value)
        if family is PublicResultFamily.RETAINED:
            raise ValueError("retained commands must use RetainedResultEnvelope")
        if family is PublicResultFamily.LIST_APPS:
            raise ValueError(
                "list-apps command is not a semantic result family; "
                "use ListAppsResult"
            )
        return _validate_frozen_value(
            "command",
            value,
            allowed=SEMANTIC_RESULT_COMMAND_NAMES,
        )

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        rejected = sorted(set(value) & _ARTIFACT_WARNING_TOKENS)
        if rejected:
            rejected_values = ", ".join(rejected)
            raise ValueError(
                f"warnings must not use artifact lifecycle tokens: {rejected_values}"
            )
        return value

    @model_validator(mode="after")
    def validate_payload_mode(self) -> CommandResultCore:
        catalog_entry = entry_for_semantic_result_command(self.command)
        if catalog_entry is None:
            raise ValueError(
                f"unknown semantic command catalog entry for command={self.command!r}"
            )
        if catalog_entry.result_category is None:
            raise ValueError(
                f"semantic command={self.command!r} must have a result category"
            )
        if self.category != catalog_entry.result_category:
            raise ValueError(
                "category must match command catalog mapping for "
                "command="
                f"{self.command!r}: expected "
                f"{catalog_entry.result_category.value!r}"
            )

        if self.ok:
            if self.code is not None or self.message is not None:
                raise ValueError("code/message are failure-only fields")
        else:
            if self.code is None or not self.code.strip():
                raise ValueError("failure results require a non-empty code")
            if self.message is None or not self.message.strip():
                raise ValueError("failure results require a non-empty message")
            if self.code is SemanticResultCode.ACTION_NOT_CONFIRMED:
                self._validate_action_not_confirmed_shape()
            if self.code in _PAYLOAD_LIGHT_LOST_TRUTH_CODES:
                self._validate_payload_light_lost_truth_shape()

        if self.payload_mode == PayloadMode.NONE:
            if self.ok:
                raise ValueError("semantic success results must use payloadMode='full'")
            if self.next_screen_id is not None or self.screen is not None:
                raise ValueError(
                    "payloadMode='none' requires nextScreenId and screen to be absent"
                )
        elif self.payload_mode == PayloadMode.FULL and self.screen is None:
            raise ValueError("payloadMode='full' requires screen")

        if self.screen is not None:
            screen_id = self.screen.screen_id
            if self.next_screen_id is None:
                raise ValueError("screen requires nextScreenId")
            if self.next_screen_id != screen_id:
                raise ValueError("nextScreenId must match screen.screenId")

        if self.action_target is not None:
            if self.command not in _ACTION_TARGET_COMMANDS:
                raise ValueError(
                    "actionTarget is only allowed for focus/type/submit results"
                )
            if self.payload_mode != PayloadMode.FULL:
                raise ValueError("actionTarget requires payloadMode='full'")
            if not self.ok:
                raise ValueError(
                    "actionTarget is only allowed for semantic success results"
                )
            if self.next_screen_id is None:
                raise ValueError("actionTarget requires nextScreenId")
            if self.source_screen_id is None:
                raise ValueError("actionTarget requires sourceScreenId")
            if self.action_target.source_screen_id != self.source_screen_id:
                raise ValueError(
                    "actionTarget.sourceScreenId must match root sourceScreenId"
                )
            if self.action_target.next_screen_id != self.next_screen_id:
                raise ValueError(
                    "actionTarget.nextScreenId must match root nextScreenId"
                )

        if self.source_screen_id is None:
            if self.truth.continuity_status != ContinuityStatus.NONE:
                raise ValueError(
                    "sourceScreenId is required when continuityStatus is not 'none'"
                )
            if self.truth.changed is not None:
                raise ValueError(
                    "sourceScreenId is required when truth.changed is present"
                )

        return self

    def _validate_action_not_confirmed_shape(self) -> None:
        if self.truth.execution_outcome is not ExecutionOutcome.DISPATCHED:
            raise ValueError(
                "ACTION_NOT_CONFIRMED requires truth.executionOutcome='dispatched'"
            )
        if self.payload_mode is not PayloadMode.FULL:
            raise ValueError("ACTION_NOT_CONFIRMED requires payloadMode='full'")
        if self.screen is None:
            raise ValueError("ACTION_NOT_CONFIRMED requires screen")
        if self.next_screen_id is None:
            raise ValueError("ACTION_NOT_CONFIRMED requires nextScreenId")
        if self.truth.observation_quality is not ObservationQuality.AUTHORITATIVE:
            raise ValueError(
                "ACTION_NOT_CONFIRMED requires "
                "truth.observationQuality='authoritative'"
            )

    def _validate_payload_light_lost_truth_shape(self) -> None:
        if self.payload_mode is not PayloadMode.NONE:
            raise ValueError(
                f"{self.code.value} requires payloadMode='none'"
                if self.code is not None
                else "lost-truth failures require payloadMode='none'"
            )
        if self.next_screen_id is not None or self.screen is not None:
            raise ValueError(
                f"{self.code.value} requires nextScreenId and screen to be absent"
                if self.code is not None
                else "lost-truth failures require nextScreenId and screen to be absent"
            )
        if self.action_target is not None:
            raise ValueError(
                f"{self.code.value} requires actionTarget to be absent"
                if self.code is not None
                else "lost-truth failures require actionTarget to be absent"
            )
        if self.truth.continuity_status is not ContinuityStatus.NONE:
            raise ValueError(
                f"{self.code.value} requires truth.continuityStatus='none'"
                if self.code is not None
                else "lost-truth failures require truth.continuityStatus='none'"
            )
        if self.truth.observation_quality is not ObservationQuality.NONE:
            raise ValueError(
                f"{self.code.value} requires truth.observationQuality='none'"
                if self.code is not None
                else "lost-truth failures require truth.observationQuality='none'"
            )
        if self.truth.changed is not None:
            raise ValueError(
                f"{self.code.value} requires truth.changed to be absent"
                if self.code is not None
                else "lost-truth failures require truth.changed to be absent"
            )
        if not _artifact_payload_is_empty(self.artifacts):
            raise ValueError(
                f"{self.code.value} requires semantic artifact pointers to be absent"
                if self.code is not None
                else (
                    "lost-truth failures require semantic artifact pointers "
                    "to be absent"
                )
            )
        if (
            self.code is SemanticResultCode.POST_ACTION_OBSERVATION_LOST
            and self.truth.execution_outcome is not ExecutionOutcome.DISPATCHED
        ):
            raise ValueError(
                "POST_ACTION_OBSERVATION_LOST requires "
                "truth.executionOutcome='dispatched'"
            )

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        return _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={
                "source_screen_id",
                "next_screen_id",
                "code",
                "message",
                "action_target",
                "screen",
            },
        )


class ListAppEntry(DaemonWireModel):
    """Public app list entry exposed by the list-apps result family."""

    package_name: _TrimmedString
    app_label: _TrimmedString


class ListAppsResult(DaemonWireModel):
    """Success-only public result for the list-apps command."""

    ok: _StrictTrue
    command: Literal["list-apps"]
    apps: list[ListAppEntry]

    @model_validator(mode="after")
    def validate_catalog_family(self) -> ListAppsResult:
        if result_family_for_command(self.command) is not PublicResultFamily.LIST_APPS:
            raise ValueError("command='list-apps' must map to listApps result family")
        return self


def dump_canonical_command_result(
    payload: CommandResultCore | Mapping[str, Any],
) -> dict[str, Any]:
    """Dump command-result output with semantic absence represented by omission."""

    result = (
        payload
        if isinstance(payload, CommandResultCore)
        else CommandResultCore.model_validate(payload)
    )
    dumped = result.model_dump(by_alias=True, mode="json")
    _drop_none_alias_keys(
        dumped,
        aliases={
            "sourceScreenId",
            "nextScreenId",
            "code",
            "message",
            "actionTarget",
            "screen",
        },
    )

    truth = dumped.get("truth")
    if isinstance(truth, dict):
        _drop_none_alias_keys(truth, aliases={"changed"})

    artifacts = dumped.get("artifacts")
    if isinstance(artifacts, dict):
        _drop_none_alias_keys(artifacts, aliases={"screenXml", "screenshotPng"})

    return dumped


__all__ = [
    "ArtifactPayload",
    "ActionTargetPayload",
    "CommandResultCore",
    "ListAppEntry",
    "ListAppsResult",
    "RetainedResultEnvelope",
    "TruthPayload",
    "dump_canonical_command_result",
]
