from __future__ import annotations

import re

_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>ms|s)$")


def parse_duration_ms(raw: str) -> int:
    normalized = raw.strip().lower()
    match = _DURATION_RE.fullmatch(normalized)
    if match is None:
        raise ValueError("duration must use an integer followed by ms or s")
    value = int(match.group("value"))
    unit = match.group("unit")
    if unit == "ms":
        return value
    return value * 1000
