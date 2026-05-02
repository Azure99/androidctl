from __future__ import annotations


def parse_screen_id_override(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("--screen-id must be non-empty")
    return normalized
