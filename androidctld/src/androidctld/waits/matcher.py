"""Text matching helpers for wait commands."""

from __future__ import annotations

from typing import Any

from androidctld.snapshots.models import RawSnapshot
from androidctld.text_equivalence import canonical_text_key, searchable_raw_node_texts


def matches_text(
    snapshot: RawSnapshot,
    query: str,
) -> bool:
    normalized_query = normalize_wait_match_text(query)
    if not normalized_query:
        return False
    seen_candidates: set[str] = set()
    for value in text_candidates(snapshot):
        candidate = normalize_wait_match_text(value)
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        if normalized_query in candidate:
            return True
    return False


def normalize_wait_match_text(value: Any) -> str:
    return canonical_text_key(value)


def text_candidates(
    snapshot: RawSnapshot,
) -> list[str]:
    candidates: list[str] = []
    for raw_node in snapshot.nodes:
        if not raw_node.visible_to_user:
            continue
        candidates.extend(searchable_raw_node_texts(raw_node))
    return candidates
