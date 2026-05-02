"""Shared internal text normalization and searchable-text helpers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from androidctld.snapshots.models import RawNode

_WHITESPACE_RE = re.compile(r"\s+")
_STATE_DESCRIPTION_SPLIT_RE = re.compile(r"[,;/]+")
_STATE_ONLY_TOKENS = {
    "on",
    "off",
    "checked",
    "unchecked",
    "not checked",
    "selected",
    "disabled",
    "focused",
    "expanded",
    "collapsed",
    "password",
}


def normalized_text_surface(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return _WHITESPACE_RE.sub(" ", text).strip()


def canonical_text_key(value: Any) -> str:
    return normalized_text_surface(value).casefold()


def semantic_state_description_remainder(value: Any) -> str:
    normalized = normalized_text_surface(value)
    if not normalized:
        return ""
    parts = [
        part.strip()
        for part in _STATE_DESCRIPTION_SPLIT_RE.split(normalized)
        if part.strip()
    ]
    if not parts:
        return normalized
    semantic_parts = [
        part for part in parts if canonical_text_key(part) not in _STATE_ONLY_TOKENS
    ]
    return " ".join(semantic_parts)


def searchable_raw_node_texts(raw_node: RawNode) -> tuple[str, ...]:
    candidates: list[str] = []
    for value in (
        raw_node.text,
        raw_node.content_desc,
        raw_node.hint_text,
        semantic_state_description_remainder(raw_node.state_description),
    ):
        normalized = normalized_text_surface(value)
        if normalized:
            candidates.append(normalized)
    return tuple(candidates)
