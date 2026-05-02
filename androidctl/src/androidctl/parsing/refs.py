from __future__ import annotations

import re

REF_PATTERN = re.compile(r"^n[1-9][0-9]*$")


def parse_ref(raw_ref: str) -> str:
    value = raw_ref.strip()
    if not REF_PATTERN.fullmatch(value):
        raise ValueError("ref must match n<number>, for example n3")
    return value
