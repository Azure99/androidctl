from __future__ import annotations

import pytest
from pydantic import ValidationError

from androidctld.actions.action_target import (
    build_action_target_payload,
    build_same_or_successor_action_target,
    public_ref_for_handle,
)
from androidctld.refs.models import NodeHandle

from .support.semantic_screen import (
    make_compiled_screen,
    make_contract_screen,
    make_public_node,
    make_semantic_node,
)


def test_same_or_successor_projection_keeps_action_target_refs_separate() -> None:
    payload = build_same_or_successor_action_target(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref="n2",
        dispatched_ref="n9",
        next_screen_id="screen-00002",
        next_ref="n3",
        evidence=("liveRef", "attributedRoute", "submitConfirmation"),
    )

    assert payload is not None
    assert payload.source_ref == "n1"
    assert payload.subject_ref == "n2"
    assert payload.dispatched_ref == "n9"
    assert payload.next_ref == "n3"
    assert payload.identity_status == "successor"
    assert payload.evidence == ("liveRef", "attributedRoute", "submitConfirmation")


def test_public_ref_for_handle_fails_closed_on_snapshot_mismatch() -> None:
    compiled = make_compiled_screen(
        "screen-00001",
        source_snapshot_id=42,
        fingerprint="fingerprint-1",
        targets=[
            make_semantic_node(
                raw_rid="w1:0.5",
                ref="n1",
                role="button",
                label="Search",
                group="targets",
            )
        ],
    )
    public_screen = make_contract_screen(
        screen_id="screen-00001",
        targets=(make_public_node(ref="n1", role="button", label="Search"),),
    )

    ref = public_ref_for_handle(
        compiled_screen=compiled,
        public_screen=public_screen,
        handle=NodeHandle(snapshot_id=43, rid="w1:0.5"),
    )

    assert ref is None


def test_action_target_serialization_contains_no_raw_identity_strings() -> None:
    payload = build_same_or_successor_action_target(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref="n2",
        dispatched_ref="n9",
        next_screen_id="screen-00002",
        next_ref="n3",
        evidence=("liveRef", "focusConfirmation"),
    )

    assert payload is not None
    serialized = payload.model_dump_json()
    assert "w1:" not in serialized
    assert "snapshot" not in serialized
    assert "fingerprint" not in serialized
    assert ".androidctl" not in serialized


def test_build_action_target_payload_returns_none_when_subject_ref_absent() -> None:
    payload = build_action_target_payload(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref=None,
        dispatched_ref="n1",
        next_screen_id="screen-00002",
        next_ref="n2",
        identity_status="successor",
        evidence=("liveRef", "focusConfirmation"),
    )

    assert payload is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence": ("liveRef", "liveRef")},
        {"identity_status": "sameRef", "next_ref": "n2"},
        {"source_ref": "w1:0.5"},
    ],
)
def test_build_action_target_payload_raises_contract_validation_errors(
    overrides: dict[str, object],
) -> None:
    payload_kwargs = {
        "source_ref": "n1",
        "source_screen_id": "screen-00001",
        "subject_ref": "n1",
        "dispatched_ref": "n1",
        "next_screen_id": "screen-00002",
        "next_ref": "n1",
        "identity_status": "sameRef",
        "evidence": ("liveRef", "focusConfirmation"),
    }

    with pytest.raises(ValidationError):
        build_action_target_payload(**(payload_kwargs | overrides))


@pytest.mark.parametrize(
    ("subject_ref", "next_ref"),
    [
        (None, "n1"),
        ("n1", None),
    ],
)
def test_build_same_or_successor_action_target_returns_none_for_missing_refs(
    subject_ref: str | None,
    next_ref: str | None,
) -> None:
    payload = build_same_or_successor_action_target(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref=subject_ref,
        dispatched_ref="n1",
        next_screen_id="screen-00002",
        next_ref=next_ref,
        evidence=("liveRef", "focusConfirmation"),
    )

    assert payload is None


def test_build_action_target_payload_omits_null_optional_refs_for_gone() -> None:
    payload = build_action_target_payload(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref="n2",
        dispatched_ref=None,
        next_screen_id="screen-00002",
        next_ref=None,
        identity_status="gone",
        evidence=("refRepair", "submitConfirmation", "targetGone"),
    )

    assert payload is not None
    assert payload.model_dump(by_alias=True, mode="json") == {
        "sourceRef": "n1",
        "sourceScreenId": "screen-00001",
        "subjectRef": "n2",
        "nextScreenId": "screen-00002",
        "identityStatus": "gone",
        "evidence": ["refRepair", "submitConfirmation", "targetGone"],
    }


def test_build_action_target_payload_omits_null_optional_refs_for_unconfirmed() -> None:
    payload = build_action_target_payload(
        source_ref="n1",
        source_screen_id="screen-00001",
        subject_ref="n2",
        dispatched_ref=None,
        next_screen_id="screen-00002",
        next_ref=None,
        identity_status="unconfirmed",
        evidence=("refRepair", "submitConfirmation", "ambiguousSuccessor"),
    )

    assert payload is not None
    assert payload.model_dump(by_alias=True, mode="json") == {
        "sourceRef": "n1",
        "sourceScreenId": "screen-00001",
        "subjectRef": "n2",
        "nextScreenId": "screen-00002",
        "identityStatus": "unconfirmed",
        "evidence": ["refRepair", "submitConfirmation", "ambiguousSuccessor"],
    }
