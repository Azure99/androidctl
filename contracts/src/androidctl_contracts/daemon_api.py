"""Typed shared daemon API transport and runtime-route models."""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from ._wire_helpers import _drop_unset_keys, _validate_absolute_path
from .base import DaemonWireModel
from .errors import DaemonError
from .vocabulary import RuntimeStatus

TOKEN_HEADER_NAME = "X-Androidctld-Token"
OWNER_HEADER_NAME = "X-Androidctld-Owner"

ResultT = TypeVar("ResultT")
TrimmedString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
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


def _normalize_discriminator_payload(
    value: object,
    *,
    nested_field: str | None = None,
) -> object:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    kind = payload.get("kind")
    if isinstance(kind, str):
        payload["kind"] = kind.strip()
    if nested_field is None:
        return payload
    nested_value = payload.get(nested_field)
    if isinstance(nested_value, dict):
        payload[nested_field] = _normalize_discriminator_payload(nested_value)
    return payload


class HealthResult(DaemonWireModel):
    """Typed health payload exposed by ``POST /health``."""

    service: str
    version: str
    workspace_root: str
    owner_id: str

    _validate_workspace_root = field_validator("workspace_root")(
        _validate_absolute_path
    )


class RuntimePayload(DaemonWireModel):
    """Stable runtime projection used by runtime endpoints."""

    workspace_root: str
    artifact_root: str
    status: RuntimeStatus
    current_screen_id: str | None = None

    _validate_workspace_root = field_validator("workspace_root")(
        _validate_absolute_path
    )
    _validate_artifact_root = field_validator("artifact_root")(_validate_absolute_path)

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:
        return _drop_unset_keys(
            handler(self),
            fields_set=self.model_fields_set,
            optional_fields={"current_screen_id"},
        )


class DaemonSuccessEnvelope(DaemonWireModel, Generic[ResultT]):
    """Shared success envelope for daemon endpoints."""

    ok: Literal[True] = True
    result: ResultT


class DaemonErrorEnvelope(DaemonWireModel):
    """Shared error envelope for daemon endpoints."""

    ok: Literal[False] = False
    error: DaemonError


class RuntimeGetResult(DaemonWireModel):
    """Typed runtime/get result payload."""

    runtime: RuntimePayload


class ConnectionPayload(DaemonWireModel):
    """Connection options for the retained ``connect`` daemon command."""

    mode: Literal["adb", "lan"]
    token: TrimmedString
    serial: str | None = None
    host: str | None = None
    port: PositiveInt | None = None

    @field_validator("serial", "host", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @model_validator(mode="after")
    def validate_shape(self) -> ConnectionPayload:
        if self.mode == "adb":
            if self.host is not None:
                raise ValueError("host is only allowed for lan connect mode")
            if self.port is not None:
                raise ValueError("port is only allowed for lan connect mode")
            return self

        if self.host is None:
            raise ValueError("host is required for lan connect mode")
        if self.port is None:
            raise ValueError("port is required for lan connect mode")
        if self.serial is not None:
            raise ValueError("serial is only allowed for adb connect mode")
        return self


class ConnectCommandPayload(DaemonWireModel):
    kind: Literal["connect"]
    connection: ConnectionPayload


class ObserveCommandPayload(DaemonWireModel):
    kind: Literal["observe"]


class OpenAppTargetPayload(DaemonWireModel):
    kind: Literal["app"]
    value: TrimmedString


class OpenUrlTargetPayload(DaemonWireModel):
    kind: Literal["url"]
    value: TrimmedString


OpenTargetPayload: TypeAlias = Annotated[
    OpenAppTargetPayload | OpenUrlTargetPayload,
    Field(discriminator="kind"),
]


class OpenCommandPayload(DaemonWireModel):
    kind: Literal["open"]
    target: OpenTargetPayload

    @model_validator(mode="before")
    @classmethod
    def normalize_target_discriminator(cls, value: object) -> object:
        return _normalize_discriminator_payload(value, nested_field="target")


class RefActionCommandPayload(DaemonWireModel):
    kind: Literal["tap", "longTap", "focus", "submit"]
    ref: TrimmedString
    source_screen_id: TrimmedString


class TypeCommandPayload(DaemonWireModel):
    kind: Literal["type"]
    ref: TrimmedString
    source_screen_id: TrimmedString
    text: str


class ScrollCommandPayload(DaemonWireModel):
    kind: Literal["scroll"]
    ref: TrimmedString
    source_screen_id: TrimmedString
    direction: Literal["up", "down", "left", "right", "backward"]


class GlobalActionCommandPayload(DaemonWireModel):
    kind: Literal["back", "home", "recents", "notifications"]
    source_screen_id: TrimmedString | None = None


class ScreenChangePredicatePayload(DaemonWireModel):
    kind: Literal["screen-change"]
    source_screen_id: TrimmedString


class TextPresentPredicatePayload(DaemonWireModel):
    kind: Literal["text-present"]
    text: TrimmedString


class GonePredicatePayload(DaemonWireModel):
    kind: Literal["gone"]
    source_screen_id: TrimmedString
    ref: TrimmedString


class AppPredicatePayload(DaemonWireModel):
    kind: Literal["app"]
    package_name: TrimmedString


class IdlePredicatePayload(DaemonWireModel):
    kind: Literal["idle"]


LiveScreenBoundCommandPayload: TypeAlias = (
    RefActionCommandPayload
    | TypeCommandPayload
    | ScrollCommandPayload
    | GlobalActionCommandPayload
)


ScreenRelativeWaitPredicatePayload: TypeAlias = (
    ScreenChangePredicatePayload | GonePredicatePayload
)


WaitPredicatePayload: TypeAlias = Annotated[
    (
        ScreenChangePredicatePayload
        | TextPresentPredicatePayload
        | GonePredicatePayload
        | AppPredicatePayload
        | IdlePredicatePayload
    ),
    Field(discriminator="kind"),
]


class WaitCommandPayload(DaemonWireModel):
    kind: Literal["wait"]
    predicate: WaitPredicatePayload
    timeout_ms: NonNegativeInt | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_predicate_discriminator(cls, value: object) -> object:
        return _normalize_discriminator_payload(value, nested_field="predicate")


class ScreenshotCommandPayload(DaemonWireModel):
    kind: Literal["screenshot"]


class ListAppsCommandPayload(DaemonWireModel):
    kind: Literal["listApps"]


DaemonCommandPayload: TypeAlias = Annotated[
    (
        ConnectCommandPayload
        | ObserveCommandPayload
        | OpenCommandPayload
        | RefActionCommandPayload
        | TypeCommandPayload
        | ScrollCommandPayload
        | GlobalActionCommandPayload
        | WaitCommandPayload
        | ListAppsCommandPayload
        | ScreenshotCommandPayload
    ),
    Field(discriminator="kind"),
]


class CommandRunRequest(DaemonWireModel):
    """Typed ``POST /commands/run`` request."""

    command: DaemonCommandPayload

    @model_validator(mode="before")
    @classmethod
    def normalize_command_discriminator(cls, value: object) -> object:
        return _normalize_discriminator_payload(value, nested_field="command")


__all__ = [
    "OWNER_HEADER_NAME",
    "TOKEN_HEADER_NAME",
    "AppPredicatePayload",
    "CommandRunRequest",
    "ConnectCommandPayload",
    "ConnectionPayload",
    "DaemonCommandPayload",
    "DaemonErrorEnvelope",
    "DaemonSuccessEnvelope",
    "GlobalActionCommandPayload",
    "GonePredicatePayload",
    "HealthResult",
    "IdlePredicatePayload",
    "LiveScreenBoundCommandPayload",
    "ListAppsCommandPayload",
    "ObserveCommandPayload",
    "OpenAppTargetPayload",
    "OpenCommandPayload",
    "OpenTargetPayload",
    "OpenUrlTargetPayload",
    "RefActionCommandPayload",
    "RuntimeGetResult",
    "RuntimePayload",
    "ScreenshotCommandPayload",
    "ScreenRelativeWaitPredicatePayload",
    "ScreenChangePredicatePayload",
    "ScrollCommandPayload",
    "TextPresentPredicatePayload",
    "TypeCommandPayload",
    "WaitCommandPayload",
    "WaitPredicatePayload",
]
