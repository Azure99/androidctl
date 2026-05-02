"""Shared schema decoding helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaDecodeError(ValueError):
    field: str
    problem: str

    def __str__(self) -> str:
        return f"{self.field} {self.problem}"


def expect_field(payload: dict[str, Any], key: str, field_name: str) -> object:
    if key not in payload:
        raise SchemaDecodeError(field_name, "is required")
    return payload[key]


def expect_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaDecodeError(field_name, "must be a JSON object")
    return dict(value)


def expect_int(value: object, field_name: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaDecodeError(field_name, "must be an integer")
    if minimum is not None and value < minimum:
        raise SchemaDecodeError(field_name, f"must be an integer >= {minimum}")
    return value
