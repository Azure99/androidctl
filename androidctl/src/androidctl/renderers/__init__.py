"""Public renderer entrypoints and semantic projection helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeAlias

from androidctl_contracts.command_catalog import entry_for_result_command
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
    dump_canonical_command_result,
)
from androidctl_contracts.vocabulary import PublicResultFamily
from pydantic import BaseModel

from androidctl.renderers._paths import normalize_public_path

_PATH_KEYS = {"screenshotPng", "screenXml"}
RenderPayload: TypeAlias = BaseModel | Mapping[str, object]
ProjectionValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["ProjectionValue"]
    | dict[str, "ProjectionValue"]
)
ProjectionDict: TypeAlias = dict[str, ProjectionValue]


def projection_dict(
    payload: RenderPayload,
) -> ProjectionDict:
    validated = _validated_payload(payload)
    if isinstance(validated, CommandResultCore):
        dumped = dump_canonical_command_result(validated)
    elif isinstance(validated, (RetainedResultEnvelope, ListAppsResult)):
        dumped = validated.model_dump(by_alias=True, mode="json", exclude_none=True)
    else:
        dumped = validated.model_dump(mode="json", exclude_none=True)
    return _normalize_mapping(dumped)


def _validated_payload(payload: RenderPayload) -> BaseModel:
    string_keyed = _string_keyed_payload(payload)
    command = string_keyed.get("command")
    if isinstance(command, str):
        entry = entry_for_result_command(command)
        if entry is not None and entry.result_family is PublicResultFamily.RETAINED:
            return RetainedResultEnvelope.model_validate(string_keyed)
        if entry is not None and entry.result_family is PublicResultFamily.LIST_APPS:
            return ListAppsResult.model_validate(string_keyed)
    return CommandResultCore.model_validate(string_keyed)


def _string_keyed_payload(payload: RenderPayload) -> dict[str, object]:
    if isinstance(payload, BaseModel):
        dumped = payload.model_dump(mode="json", by_alias=True)
        if not isinstance(dumped, Mapping):
            raise TypeError("renderer payload model must dump to a mapping")
        return {key: item for key, item in dumped.items() if isinstance(key, str)}
    if isinstance(payload, Mapping):
        return {key: item for key, item in payload.items() if isinstance(key, str)}
    raise TypeError("renderer payload must be a pydantic model or mapping")


def _normalize_mapping(value: Mapping[str, object]) -> ProjectionDict:
    normalized: ProjectionDict = {}
    for key, item in value.items():
        if key in _PATH_KEYS and isinstance(item, str):
            normalized[key] = _normalize_path(item)
            continue
        normalized[key] = _normalize_value(item)
    return normalized


def _normalize_sequence(value: Sequence[object]) -> list[ProjectionValue]:
    return [_normalize_value(item) for item in value]


def _normalize_value(value: object) -> ProjectionValue:
    if isinstance(value, Mapping):
        string_keyed = {
            key: item for key, item in value.items() if isinstance(key, str)
        }
        return _normalize_mapping(string_keyed)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return _normalize_sequence(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported renderer projection value: {type(value).__name__}")


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    marker = "/.androidctl/"
    marker_index = normalized.rfind(marker)
    workspace_root = normalized[:marker_index] if marker_index >= 0 else None
    artifact_root = (
        f"{workspace_root}/.androidctl" if workspace_root is not None else None
    )
    public_path = normalize_public_path(
        normalized,
        workspace_root=workspace_root,
        artifact_root=artifact_root,
    )
    return public_path or normalized
