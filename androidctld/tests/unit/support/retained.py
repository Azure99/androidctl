from __future__ import annotations

from collections.abc import Mapping

from androidctl_contracts.command_results import RetainedResultEnvelope

SEMANTIC_ONLY_RETAINED_FORBIDDEN_FIELDS = frozenset(
    {
        "category",
        "payloadMode",
        "truth",
        "sourceScreenId",
        "nextScreenId",
        "screen",
        "uncertainty",
        "warnings",
    }
)


def assert_retained_omits_semantic_fields(payload: Mapping[str, object]) -> None:
    RetainedResultEnvelope.model_validate(dict(payload))
    assert SEMANTIC_ONLY_RETAINED_FORBIDDEN_FIELDS.isdisjoint(payload.keys())
