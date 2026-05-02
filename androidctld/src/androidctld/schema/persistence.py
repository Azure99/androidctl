"""Persistence boundary models for androidctld local state."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from androidctld.protocol import RuntimeStatus
from androidctld.schema.base import ApiModel, to_camel
from androidctld.schema.core import (
    SchemaDecodeError,
    expect_field,
    expect_int,
    expect_object,
)
from androidctld.schema.validation_errors import validation_error_to_schema_decode_error

RUNTIME_STATE_SCHEMA_VERSION = 1
RUNTIME_STATE_FILE_NAME = "runtime.json"

EnumT = TypeVar("EnumT", bound=Enum)
PersistenceModelT = TypeVar("PersistenceModelT", bound="PersistenceModel")


def expect_schema_version(
    payload: object,
    field_name: str,
    version: int,
) -> dict[str, Any]:
    parsed = expect_object(payload, field_name)
    actual = expect_int(
        expect_field(parsed, "schemaVersion", f"{field_name}.schemaVersion"),
        f"{field_name}.schemaVersion",
        minimum=1,
    )
    if actual != version:
        raise SchemaDecodeError(
            f"{field_name}.schemaVersion",
            f"must be {version}",
        )
    return parsed


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _coerce_enum(value: object, enum_type: type[EnumT]) -> object:
    normalized = _strip_string(value)
    if isinstance(normalized, enum_type):
        return normalized
    if isinstance(normalized, str):
        try:
            return enum_type(normalized)
        except ValueError:
            return normalized
    return normalized


class PersistenceModel(ApiModel):
    model_config = ConfigDict(
        strict=True,
        extra="ignore",
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        use_enum_values=False,
    )


def validate_persistence_payload(
    model_type: type[PersistenceModelT],
    payload: object,
    *,
    field_name: str,
    schema_version: int | None,
) -> PersistenceModelT:
    if schema_version is None:
        parsed = expect_object(payload, field_name)
    else:
        parsed = expect_schema_version(payload, field_name, schema_version)
        parsed.pop("schemaVersion", None)
    try:
        return model_type.model_validate(parsed)
    except ValidationError as error:
        raise validation_error_to_schema_decode_error(
            error,
            field_name=field_name,
        ) from error


def build_persistence_model(
    model_type: type[PersistenceModelT],
    /,
    **data: Any,
) -> PersistenceModelT:
    return model_type.model_validate(data, by_name=True)


class ActiveDaemonFile(PersistenceModel):
    model_config = ConfigDict(extra="forbid")

    pid: int = Field(ge=0)
    host: str = Field(min_length=1)
    port: int = Field(ge=1)
    token: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)

    @field_validator(
        "host",
        "token",
        "started_at",
        "workspace_root",
        "owner_id",
        mode="before",
    )
    @classmethod
    def _strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)


class RuntimeStateFile(PersistenceModel):
    model_config = ConfigDict(extra="forbid")

    status: RuntimeStatus
    screen_sequence: int = Field(default=0, ge=0)
    updated_at: str = Field(min_length=1)

    @field_validator("status", mode="before")
    @classmethod
    def _strip_status(cls, value: object) -> object:
        return _coerce_enum(value, RuntimeStatus)

    @field_validator("status")
    @classmethod
    def _reject_restart_unsafe_ready_status(
        cls,
        value: RuntimeStatus,
    ) -> RuntimeStatus:
        if value is RuntimeStatus.READY:
            raise ValueError("persisted runtime status cannot be ready")
        return value

    @field_validator(
        "updated_at",
        mode="before",
    )
    @classmethod
    def _strip_required_strings(cls, value: object) -> object:
        return _strip_string(value)
