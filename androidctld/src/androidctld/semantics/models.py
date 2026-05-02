"""Typed semantic compiler support models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticMeta:
    resource_id: str | None
    class_name: str


@dataclass(frozen=True)
class RelationScopeNode:
    rid: str
    window_id: str
    parent_rid: str | None
    bounds: tuple[int, int, int, int]
    resource_id: str
    class_name: str
    text: str
    content_desc: str
    pane_title: str
    is_window_root: bool
