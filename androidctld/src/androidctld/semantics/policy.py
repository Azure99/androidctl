"""Semantic screen compilation policy constants."""

from __future__ import annotations

from typing import Final

LABEL_MAX_LENGTH: Final[int] = 48

ROLE_BASE_SCORES: Final[dict[str, int]] = {
    "button": 50,
    "input": 45,
    "switch": 40,
    "checkbox": 35,
    "radio": 35,
    "tab": 30,
    "list-item": 25,
    "keyboard-key": 20,
    "text": 5,
}

TARGETABLE_SCORE_BONUS: Final[int] = 100
FOCUSED_SCORE_BONUS: Final[int] = 10
ENABLED_SCORE_BONUS: Final[int] = 5
