"""Private helpers for shared daemon wire model behavior."""

from __future__ import annotations

from typing import Any

from .base import to_camel


def _drop_unset_keys(
    data: dict[str, Any],
    *,
    fields_set: set[str],
    optional_fields: set[str],
) -> dict[str, Any]:
    for field_name in optional_fields:
        if field_name in fields_set:
            continue
        data.pop(field_name, None)
        data.pop(to_camel(field_name), None)
    return data


def _validate_absolute_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(("/", "\\")) and not (
        len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}
    ):
        raise ValueError("daemon-wire paths must be absolute")
    return value
