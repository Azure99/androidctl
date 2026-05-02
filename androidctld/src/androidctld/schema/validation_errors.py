"""Validation error adapters for boundary models."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any, cast

from pydantic import ValidationError

from androidctld.device.errors import DeviceBootstrapError, device_rpc_failed
from androidctld.errors import DaemonError, bad_request
from androidctld.schema.core import SchemaDecodeError

_COMMAND_UNION_TAGS = {
    "connect",
    "observe",
    "listApps",
    "open",
    "tap",
    "longTap",
    "focus",
    "type",
    "submit",
    "scroll",
    "back",
    "home",
    "recents",
    "notifications",
    "wait",
    "screenshot",
}
_OPEN_TARGET_UNION_TAGS = {"app", "url"}
_WAIT_PREDICATE_UNION_TAGS = {
    "screen-change",
    "text-present",
    "gone",
    "app",
    "idle",
}


def validation_error_field_path(location: Sequence[Any]) -> str:
    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            if not parts:
                parts.append(f"[{item}]")
            else:
                parts[-1] = f"{parts[-1]}[{item}]"
            continue
        parts.append(str(item))
    if not parts:
        return "root"
    return ".".join(parts)


def _validation_error_leaf(location: Sequence[Any]) -> str:
    if not location:
        return "root"
    return str(location[-1])


def _validation_error_container(location: Sequence[Any]) -> tuple[Any, ...]:
    if location:
        return tuple(location[:-1])
    return ()


def _prefix_field_name(field_name: str | None, field: str) -> str:
    if field_name is None:
        return field
    if field == "root":
        return field_name
    if field.startswith("["):
        return f"{field_name}{field}"
    return f"{field_name}.{field}"


def _normalize_discriminated_union_location(
    location: Sequence[Any],
    *,
    error_type: str | None = None,
    ctx: dict[str, Any] | None = None,
) -> Sequence[Any]:
    parts = list(location)
    if error_type == "union_tag_invalid" and ctx is not None:
        discriminator = ctx.get("discriminator")
        if isinstance(discriminator, str):
            parts.append(discriminator.strip("'"))

    normalized: list[Any] = []
    index = 0
    while index < len(parts):
        item = parts[index]
        normalized.append(item)
        if (
            item == "command"
            and index + 1 < len(parts)
            and parts[index + 1] in _COMMAND_UNION_TAGS
        ):
            index += 2
            continue
        if (
            item == "target"
            and index + 1 < len(parts)
            and parts[index + 1] in _OPEN_TARGET_UNION_TAGS
        ):
            index += 2
            continue
        if (
            item == "predicate"
            and index + 1 < len(parts)
            and parts[index + 1] in _WAIT_PREDICATE_UNION_TAGS
        ):
            index += 2
            continue
        index += 1
    return tuple(normalized)


def _validation_error_problem(error_type: str, ctx: dict[str, Any] | None) -> str:
    if error_type in {"bool_type", "bool_parsing"}:
        return "must be a boolean"
    if error_type in {"int_type", "int_parsing"}:
        return "must be an integer"
    if error_type == "is_instance_of" and ctx is not None:
        class_name = ctx.get("class")
        if class_name == "CommandKind":
            return "must be a supported command kind"
        if class_name == "RuntimeStatus":
            return "must be a supported runtime status"
    if error_type == "list_type":
        return "must be a list"
    if error_type in {"dict_type", "model_type"}:
        return "must be a JSON object"
    if error_type in {"string_type", "string_sub_type", "string_unicode"}:
        return "must be a string"
    if error_type == "value_error" and ctx is not None:
        error = ctx.get("error")
        if isinstance(error, ValueError):
            return str(error)
    if error_type == "literal_error" and ctx is not None:
        expected = ctx.get("expected")
        if expected == "'done', 'partial' or 'timeout'":
            return "must be one of done|partial|timeout"
    if error_type == "union_tag_invalid" and ctx is not None:
        expected_tags = ctx.get("expected_tags")
        if expected_tags == "'app', 'url'":
            return "must be app or url"
    if error_type == "missing":
        return "is required"
    if error_type == "extra_forbidden":
        return "has unsupported fields"
    if error_type == "greater_than_equal" and ctx is not None:
        minimum = ctx.get("ge")
        if isinstance(minimum, int) and not isinstance(minimum, bool):
            return f"must be an integer >= {minimum}"
    if error_type == "greater_than" and ctx is not None:
        minimum = ctx.get("gt")
        if isinstance(minimum, int) and not isinstance(minimum, bool):
            return f"must be an integer > {minimum}"
    if error_type == "string_too_short" and ctx is not None:
        min_length = ctx.get("min_length")
        if isinstance(min_length, int) and not isinstance(min_length, bool):
            if min_length == 1:
                return "must be a non-empty string"
            return f"must be a string with at least {min_length} characters"
    return "is invalid"


def _validation_error_field(
    location: Sequence[Any],
    *,
    error_type: str | None = None,
    ctx: dict[str, Any] | None = None,
    field_name: str | None = None,
) -> str:
    location = _normalize_discriminated_union_location(
        location,
        error_type=error_type,
        ctx=ctx,
    )
    return _prefix_field_name(field_name, validation_error_field_path(location))


def _validation_error_extra_fields(
    errors: Sequence[Any],
    *,
    field_name: str | None = None,
) -> tuple[str, list[str]] | None:
    grouped: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    order: list[tuple[Any, ...]] = []
    for raw_item in errors:
        item = cast(dict[str, Any], raw_item)
        if str(item["type"]) != "extra_forbidden":
            continue
        location = _normalize_discriminated_union_location(
            cast(Sequence[Any], item["loc"])
        )
        container = _validation_error_container(location)
        if container not in grouped:
            order.append(container)
        grouped[container].append(_validation_error_leaf(location))
    if not grouped:
        return None
    chosen = min(order, key=len)  # Same-depth ties intentionally keep first seen.
    unknown_fields = sorted(grouped[chosen])
    return (
        _prefix_field_name(field_name, validation_error_field_path(chosen)),
        unknown_fields,
    )


def validation_error_to_schema_decode_error(
    error: ValidationError,
    *,
    field_name: str | None = None,
) -> SchemaDecodeError:
    errors = error.errors()
    extra_fields = _validation_error_extra_fields(errors, field_name=field_name)
    if extra_fields is not None:
        field, _unknown_fields = extra_fields
        return SchemaDecodeError(field, "has unsupported fields")
    first_error = cast(dict[str, Any], errors[0])
    location = cast(Sequence[Any], first_error["loc"])
    field = _validation_error_field(
        location,
        error_type=str(first_error["type"]),
        ctx=first_error.get("ctx"),
        field_name=field_name,
    )
    problem = _validation_error_problem(
        str(first_error["type"]),
        first_error.get("ctx"),
    )
    return SchemaDecodeError(field, problem)


def validation_error_to_bad_request(
    error: ValidationError,
    *,
    field_name: str | None = None,
) -> DaemonError:
    errors = error.errors()
    extra_fields = _validation_error_extra_fields(errors, field_name=field_name)
    if extra_fields is not None:
        field, unknown_fields = extra_fields
        return bad_request(
            f"{field} has unsupported fields",
            {
                "field": field,
                "unknownFields": unknown_fields,
            },
        )
    first_error = cast(dict[str, Any], errors[0])
    location = cast(Sequence[Any], first_error["loc"])
    field = _validation_error_field(
        location,
        error_type=str(first_error["type"]),
        ctx=first_error.get("ctx"),
        field_name=field_name,
    )
    problem = _validation_error_problem(
        str(first_error["type"]),
        first_error.get("ctx"),
    )
    return bad_request(
        f"{field} {problem}",
        {"field": field},
    )


def validation_error_to_device_bootstrap_error(
    error: ValidationError,
    *,
    field_name: str | None = None,
    retryable: bool = False,
) -> DeviceBootstrapError:
    errors = error.errors()
    extra_fields = _validation_error_extra_fields(errors, field_name=field_name)
    if extra_fields is not None:
        field, unknown_fields = extra_fields
        return device_rpc_failed(
            f"device RPC {field} has unsupported fields",
            {
                "field": field,
                "reason": "invalid_payload",
                "unknownFields": unknown_fields,
            },
            retryable=retryable,
        )
    first_error = cast(dict[str, Any], errors[0])
    location = cast(Sequence[Any], first_error["loc"])
    field = _validation_error_field(
        location,
        error_type=str(first_error["type"]),
        ctx=first_error.get("ctx"),
        field_name=field_name,
    )
    problem = _validation_error_problem(
        str(first_error["type"]),
        first_error.get("ctx"),
    )
    return device_rpc_failed(
        f"device RPC {field} {problem}",
        {"field": field, "reason": "invalid_payload"},
        retryable=retryable,
    )
