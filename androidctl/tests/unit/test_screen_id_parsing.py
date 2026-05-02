from __future__ import annotations

import pytest

from androidctl.parsing.screen_id import parse_screen_id_override


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("  screen-1  ", "screen-1"),
    ],
)
def test_parse_screen_id_override_accepts_absent_or_non_empty_values(
    raw_value: str | None,
    expected: str | None,
) -> None:
    assert parse_screen_id_override(raw_value) == expected


def test_parse_screen_id_override_rejects_blank_explicit_value() -> None:
    with pytest.raises(ValueError, match="--screen-id must be non-empty"):
        parse_screen_id_override("   ")
