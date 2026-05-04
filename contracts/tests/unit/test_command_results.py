from __future__ import annotations

import json
from typing import Any

import pytest
from public_screen_payloads import public_screen_payload_base
from pydantic import ValidationError

from androidctl_contracts.command_catalog import (
    SEMANTIC_RESULT_COMMAND_NAMES,
    result_category_for_command,
)
from androidctl_contracts.command_results import (
    ActionTargetPayload,
    ArtifactPayload,
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
    dump_canonical_command_result,
)
from androidctl_contracts.vocabulary import SemanticResultCode


def _public_screen_payload(screen_id: str) -> dict[str, object]:
    return public_screen_payload_base(screen_id)


def _action_target_payload(
    *,
    source_ref: str = "n1",
    source_screen_id: str = "screen-00006",
    subject_ref: str = "n1",
    dispatched_ref: str | None = "n1",
    next_screen_id: str = "screen-00007",
    next_ref: str | None = "n1",
    identity_status: str = "sameRef",
    evidence: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "sourceRef": source_ref,
        "sourceScreenId": source_screen_id,
        "subjectRef": subject_ref,
        "nextScreenId": next_screen_id,
        "identityStatus": identity_status,
        "evidence": (
            ["liveRef", "requestTarget", "focusConfirmation"]
            if evidence is None
            else evidence
        ),
    }
    if dispatched_ref is not None:
        payload["dispatchedRef"] = dispatched_ref
    if next_ref is not None:
        payload["nextRef"] = next_ref
    return payload


def _semantic_with_action_target(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "command": "focus",
        "category": "transition",
        "payloadMode": "full",
        "sourceScreenId": "screen-00006",
        "nextScreenId": "screen-00007",
        "truth": {
            "executionOutcome": "dispatched",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": True,
        },
        "actionTarget": _action_target_payload(),
        "screen": _public_screen_payload("screen-00007"),
        "artifacts": {},
    }
    payload.update(overrides)
    return payload


def _lost_truth_payload(
    *,
    code: str,
    command: str = "tap",
    category: str = "transition",
    execution_outcome: str = "dispatched",
) -> dict[str, object]:
    return {
        "ok": False,
        "command": command,
        "category": category,
        "payloadMode": "none",
        "sourceScreenId": "screen-00006",
        "nextScreenId": None,
        "code": code,
        "message": "No current screen truth is available.",
        "truth": {
            "executionOutcome": execution_outcome,
            "continuityStatus": "none",
            "observationQuality": "none",
            "changed": None,
        },
        "screen": None,
        "uncertainty": [],
        "warnings": [],
        "artifacts": {"screenshotPng": None, "screenXml": None},
    }


def _set_nested_payload_value(
    payload: dict[str, object],
    field_path: tuple[str, ...],
    value: object,
) -> None:
    target: Any = payload
    for field_name in field_path[:-1]:
        target = target[field_name]
    target[field_path[-1]] = value


def test_transition_result_round_trips_public_screen_wire_shape() -> None:
    payload = {
        "ok": True,
        "command": "tap",
        "category": "transition",
        "payloadMode": "full",
        "sourceScreenId": "screen-00006",
        "nextScreenId": "screen-00007",
        "truth": {
            "executionOutcome": "dispatched",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": True,
        },
        "screen": _public_screen_payload("screen-00007"),
        "artifacts": {
            "screenshotPng": "/repo/.androidctl/screenshots/shot-001.png",
        },
    }

    result = CommandResultCore.model_validate(payload)

    assert result.screen is not None
    assert result.screen.screen_id == "screen-00007"
    assert result.model_dump(by_alias=True, exclude_none=True) == {
        **payload,
        "uncertainty": [],
        "warnings": [],
    }


def test_action_target_payload_round_trips_for_focus_result() -> None:
    payload = _semantic_with_action_target()

    result = CommandResultCore.model_validate(payload)

    assert result.action_target is not None
    assert result.action_target.source_ref == "n1"
    assert (
        dump_canonical_command_result(result)["actionTarget"] == payload["actionTarget"]
    )


def test_action_target_explicit_null_is_canonically_omitted() -> None:
    payload = _semantic_with_action_target(actionTarget=None)

    result = CommandResultCore.model_validate(payload)

    assert result.action_target is None
    assert "actionTarget" not in dump_canonical_command_result(result)


def test_action_target_rejects_unknown_nested_extra() -> None:
    action_target = _action_target_payload()
    action_target["rawRid"] = "w1:0.5"

    with pytest.raises(ValidationError, match="extra"):
        CommandResultCore.model_validate(
            _semantic_with_action_target(actionTarget=action_target)
        )


def test_action_target_rejects_unknown_evidence_token() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        CommandResultCore.model_validate(
            _semantic_with_action_target(
                actionTarget=_action_target_payload(evidence=["foregroundChanged"])
            )
        )


@pytest.mark.parametrize(
    "command",
    ["tap", "open", "wait", "observe"],
)
def test_action_target_rejects_non_v1_command_scope(command: str) -> None:
    category = (
        "open"
        if command == "open"
        else (
            "wait"
            if command == "wait"
            else ("observe" if command == "observe" else "transition")
        )
    )
    with pytest.raises(ValidationError, match="focus/type/submit"):
        CommandResultCore.model_validate(
            _semantic_with_action_target(command=command, category=category)
        )


@pytest.mark.parametrize(
    ("target_field", "target_value", "message"),
    [
        ("sourceScreenId", "screen-99999", "sourceScreenId"),
        ("nextScreenId", "screen-99999", "nextScreenId"),
    ],
)
def test_action_target_rejects_root_screen_id_mismatch(
    target_field: str,
    target_value: str,
    message: str,
) -> None:
    action_target = _action_target_payload()
    action_target[target_field] = target_value

    with pytest.raises(ValidationError, match=message):
        CommandResultCore.model_validate(
            _semantic_with_action_target(actionTarget=action_target)
        )


def test_action_target_rejects_payload_mode_none() -> None:
    with pytest.raises(
        ValidationError,
        match="actionTarget requires payloadMode='full'",
    ):
        CommandResultCore.model_validate(
            _semantic_with_action_target(
                ok=False,
                payloadMode="none",
                nextScreenId=None,
                code="TARGET_NOT_ACTIONABLE",
                message="not actionable",
                screen=None,
            )
        )


def test_action_target_rejects_full_failure_result() -> None:
    with pytest.raises(
        ValidationError,
        match="semantic success results",
    ):
        CommandResultCore.model_validate(
            _semantic_with_action_target(
                ok=False,
                code="TARGET_NOT_ACTIONABLE",
                message="not actionable",
            )
        )


@pytest.mark.parametrize(
    ("identity_status", "next_ref", "match"),
    [
        ("sameRef", "n2", "sameRef"),
        ("successor", None, "successor"),
        ("successor", "n1", "successor"),
        ("gone", "n1", "gone/unconfirmed"),
        ("unconfirmed", "n1", "gone/unconfirmed"),
    ],
)
def test_action_target_identity_invariants(
    identity_status: str,
    next_ref: str | None,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        ActionTargetPayload.model_validate(
            _action_target_payload(
                identity_status=identity_status,
                next_ref=next_ref,
            )
        )


def test_action_target_rejects_duplicate_evidence() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ActionTargetPayload.model_validate(
            _action_target_payload(evidence=["liveRef", "liveRef"])
        )


@pytest.mark.parametrize("identity_status", ["gone", "unconfirmed"])
def test_action_target_optional_null_refs_are_canonically_omitted(
    identity_status: str,
) -> None:
    payload = ActionTargetPayload(
        source_ref="n1",
        source_screen_id="screen-00006",
        subject_ref="n2",
        dispatched_ref=None,
        next_screen_id="screen-00007",
        next_ref=None,
        identity_status=identity_status,
        evidence=("liveRef", "submitConfirmation", "ambiguousSuccessor"),
    )

    assert payload.model_dump(by_alias=True, mode="json") == {
        "sourceRef": "n1",
        "sourceScreenId": "screen-00006",
        "subjectRef": "n2",
        "nextScreenId": "screen-00007",
        "identityStatus": identity_status,
        "evidence": ["liveRef", "submitConfirmation", "ambiguousSuccessor"],
    }


@pytest.mark.parametrize(
    "value",
    ["", "w1:0.5", "/tmp/n1", "fingerprint-abcdef"],
)
def test_action_target_rejects_raw_path_fingerprint_snapshot_like_refs(
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="public refs"):
        ActionTargetPayload.model_validate(_action_target_payload(source_ref=value))


@pytest.mark.parametrize(
    "value",
    [
        "screen/0001",
        "https://example.test/screen",
        "screen id with spaces",
    ],
)
def test_action_target_accepts_public_screen_opaque_screen_ids(value: str) -> None:
    payload = ActionTargetPayload.model_validate(
        _action_target_payload(source_screen_id=value, next_screen_id=value)
    )

    assert payload.source_screen_id == value
    assert payload.next_screen_id == value


@pytest.mark.parametrize("field_name", ["source_screen_id", "next_screen_id"])
def test_action_target_rejects_empty_screen_ids(field_name: str) -> None:
    with pytest.raises(ValidationError):
        ActionTargetPayload.model_validate(_action_target_payload(**{field_name: ""}))


@pytest.mark.parametrize("field_name", ["source_screen_id", "next_screen_id"])
def test_action_target_rejects_non_string_screen_ids(
    field_name: str,
) -> None:
    with pytest.raises(ValidationError):
        ActionTargetPayload.model_validate(_action_target_payload(**{field_name: 123}))


def test_close_success_uses_retained_envelope_not_semantic_result() -> None:
    with pytest.raises(ValidationError, match="retained"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "close",
                "category": "lifecycle",
                "payloadMode": "none",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "none",
                },
            }
        )


def test_canonical_dump_accepts_explicit_null_and_omits_semantic_absence() -> None:
    payload = {
        "ok": False,
        "command": "wait",
        "category": "wait",
        "payloadMode": "none",
        "sourceScreenId": None,
        "nextScreenId": None,
        "code": "WAIT_TIMEOUT",
        "message": "Condition was not satisfied before timeout.",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "none",
            "changed": None,
        },
        "screen": None,
        "uncertainty": [],
        "warnings": [],
        "artifacts": {"screenshotPng": None, "screenXml": None},
    }

    result = CommandResultCore.model_validate(payload)

    assert dump_canonical_command_result(result) == {
        "ok": False,
        "command": "wait",
        "category": "wait",
        "payloadMode": "none",
        "code": "WAIT_TIMEOUT",
        "message": "Condition was not satisfied before timeout.",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "none",
        },
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


def test_canonical_dump_preserves_failure_code_and_message() -> None:
    payload = {
        "ok": False,
        "command": "wait",
        "category": "wait",
        "payloadMode": "none",
        "sourceScreenId": "screen-00006",
        "nextScreenId": None,
        "code": "WAIT_TIMEOUT",
        "message": "Condition was not satisfied before timeout.",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "none",
            "changed": None,
        },
        "screen": None,
        "uncertainty": [],
        "warnings": [],
        "artifacts": {"screenshotPng": None, "screenXml": None},
    }

    assert dump_canonical_command_result(payload) == {
        "ok": False,
        "command": "wait",
        "category": "wait",
        "payloadMode": "none",
        "sourceScreenId": "screen-00006",
        "code": "WAIT_TIMEOUT",
        "message": "Condition was not satisfied before timeout.",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "none",
        },
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


def test_canonical_dump_preserves_full_payload_and_nested_screen_nulls() -> None:
    screen = _public_screen_payload("screen-00007")
    groups = screen["groups"]
    assert isinstance(groups, list)
    groups[0]["nodes"] = [
        {
            "ref": "n1",
            "role": "button",
            "label": "OK",
            "actions": ["tap"],
        }
    ]
    payload = {
        "ok": True,
        "command": "tap",
        "category": "transition",
        "payloadMode": "full",
        "sourceScreenId": "screen-00006",
        "nextScreenId": "screen-00007",
        "code": None,
        "message": None,
        "truth": {
            "executionOutcome": "dispatched",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": False,
        },
        "screen": screen,
        "uncertainty": [],
        "warnings": [],
        "artifacts": {
            "screenshotPng": "/repo/.androidctl/screenshots/shot-001.png",
            "screenXml": "/repo/.androidctl/artifacts/screens/screen-00007.xml",
        },
    }

    canonical = dump_canonical_command_result(payload)

    assert canonical["sourceScreenId"] == "screen-00006"
    assert canonical["nextScreenId"] == "screen-00007"
    assert canonical["truth"]["changed"] is False
    assert "code" not in canonical
    assert "message" not in canonical
    assert canonical["artifacts"] == {
        "screenshotPng": "/repo/.androidctl/screenshots/shot-001.png",
        "screenXml": "/repo/.androidctl/artifacts/screens/screen-00007.xml",
    }
    node = canonical["screen"]["groups"][0]["nodes"][0]
    assert node["bounds"] is None
    assert node["meta"] == {}


def test_semantic_success_rejects_payload_mode_none() -> None:
    with pytest.raises(ValidationError, match="payloadMode='full'"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "wait",
                "category": "wait",
                "payloadMode": "none",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "none",
                },
            }
        )


def test_close_rejects_full_payload_shape() -> None:
    with pytest.raises(ValidationError, match="retained"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "close",
                "category": "lifecycle",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_payload_mode_none_rejects_next_screen_and_screen_payload() -> None:
    with pytest.raises(ValidationError, match="payloadMode='none'"):
        CommandResultCore.model_validate(
            {
                "ok": False,
                "command": "wait",
                "category": "wait",
                "payloadMode": "none",
                "code": "WAIT_TIMEOUT",
                "message": "Condition was not satisfied before timeout.",
                "nextScreenId": "screen-00007",
                "screen": _public_screen_payload("screen-00007"),
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "none",
                },
            }
        )


def test_failure_result_payload_mode_none_preserves_source_screen_id() -> None:
    payload = {
        "ok": False,
        "command": "wait",
        "category": "wait",
        "payloadMode": "none",
        "sourceScreenId": "screen-00006",
        "code": "WAIT_TIMEOUT",
        "message": "Condition was not satisfied before timeout.",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "none",
        },
    }

    result = CommandResultCore.model_validate(payload)

    assert result.source_screen_id == "screen-00006"
    assert result.code is SemanticResultCode.WAIT_TIMEOUT
    assert result.model_dump(by_alias=True, exclude_none=True) == {
        **payload,
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


@pytest.mark.parametrize(
    ("command", "envelope"),
    [
        ("connect", "bootstrap"),
        ("screenshot", "artifact"),
        ("close", "lifecycle"),
    ],
)
def test_retained_commands_accept_mapped_envelope_kind(
    command: str,
    envelope: str,
) -> None:
    payload = {
        "ok": True,
        "command": command,
        "envelope": envelope,
        "artifacts": {"sample": "/repo/.androidctl/artifacts/sample.txt"},
        "details": {"route": "sample"},
    }

    result = RetainedResultEnvelope.model_validate(payload)

    assert result.command == command
    assert result.envelope.value == envelope
    assert result.model_dump(by_alias=True, exclude_none=True) == payload


def test_retained_failure_accepts_public_code_with_source_code_detail() -> None:
    payload = {
        "ok": False,
        "command": "screenshot",
        "envelope": "artifact",
        "code": "WORKSPACE_STATE_UNWRITABLE",
        "message": "artifact write failed",
        "artifacts": {},
        "details": {
            "sourceCode": "ARTIFACT_WRITE_FAILED",
            "sourceKind": "workspace",
            "reason": "permission-denied",
        },
    }

    result = RetainedResultEnvelope.model_validate(payload)

    assert result.code == "WORKSPACE_STATE_UNWRITABLE"
    assert result.details["sourceCode"] == "ARTIFACT_WRITE_FAILED"
    assert result.model_dump(by_alias=True, exclude_none=True) == payload


@pytest.mark.parametrize("command", ["connect", "screenshot", "close"])
def test_command_result_core_rejects_retained_commands(command: str) -> None:
    with pytest.raises(ValidationError, match="retained"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": command,
                "category": "observe",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


@pytest.mark.parametrize("command", ["observe", "tap"])
def test_retained_result_envelope_rejects_semantic_commands(command: str) -> None:
    with pytest.raises(ValidationError, match="semantic"):
        RetainedResultEnvelope.model_validate(
            {
                "ok": True,
                "command": command,
                "envelope": "artifact",
                "artifacts": {},
                "details": {},
            }
        )


def test_list_apps_result_round_trips_public_success_shape() -> None:
    payload = {
        "ok": True,
        "command": "list-apps",
        "apps": [
            {
                "packageName": "com.android.settings",
                "appLabel": "Settings",
            },
            {
                "packageName": "com.example.mail",
                "appLabel": "Mail",
            },
        ],
    }

    result = ListAppsResult.model_validate(payload, strict=True)

    assert result.ok is True
    assert result.command == "list-apps"
    assert result.apps[0].package_name == "com.android.settings"
    assert result.apps[0].app_label == "Settings"
    assert result.model_dump(by_alias=True) == payload


def test_list_apps_result_accepts_empty_app_list() -> None:
    result = ListAppsResult.model_validate(
        {"ok": True, "command": "list-apps", "apps": []},
        strict=True,
    )

    assert result.apps == []


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "apps": []},
    ],
)
def test_list_apps_result_rejects_missing_wire_discriminators(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ListAppsResult.model_validate(payload)


@pytest.mark.parametrize(
    "json_payload",
    [
        '{"ok":1,"command":"list-apps","apps":[]}',
        '{"ok":"true","command":"list-apps","apps":[]}',
    ],
)
def test_list_apps_result_rejects_non_boolean_ok_wire_values(
    json_payload: str,
) -> None:
    with pytest.raises(ValidationError):
        ListAppsResult.model_validate_json(json_payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "command": "list-apps"},
        {"ok": True, "command": "list-apps", "apps": "not-a-list"},
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"appLabel": "Settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "com.android.settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "", "appLabel": "Settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": 123, "appLabel": "Settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "com.android.settings", "appLabel": False}],
        },
    ],
)
def test_list_apps_result_rejects_malformed_app_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ListAppsResult.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "command": "list-apps", "apps": []},
        {"ok": True, "command": "observe", "apps": []},
        {
            "ok": True,
            "command": "list-apps",
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                    "launchable": True,
                }
            ],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "category": "observe",
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "truth": {},
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "envelope": "artifact",
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "details": {},
        },
    ],
)
def test_list_apps_result_rejects_non_list_apps_result_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ListAppsResult.model_validate(payload)


def test_list_apps_result_schema_freezes_public_fields() -> None:
    schema = ListAppsResult.model_json_schema(by_alias=True)
    schema_text = json.dumps(schema, sort_keys=True)

    assert {"ok", "command", "apps"} <= set(schema["required"])
    assert "packageName" in schema_text
    assert "appLabel" in schema_text
    assert "launchable" not in schema_text


def test_command_result_core_rejects_list_apps_result_family() -> None:
    with pytest.raises(ValidationError, match="semantic result family"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "list-apps",
                "category": "observe",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_retained_result_envelope_rejects_list_apps_result_family() -> None:
    with pytest.raises(ValidationError, match="retained result family"):
        RetainedResultEnvelope.model_validate(
            {
                "ok": True,
                "command": "list-apps",
                "envelope": "artifact",
                "artifacts": {},
                "details": {},
            }
        )


def test_retained_result_envelope_rejects_command_kind_mismatch() -> None:
    with pytest.raises(ValidationError, match="envelope"):
        RetainedResultEnvelope.model_validate(
            {
                "ok": True,
                "command": "screenshot",
                "envelope": "bootstrap",
                "artifacts": {},
                "details": {},
            }
        )


@pytest.mark.parametrize("command", sorted(SEMANTIC_RESULT_COMMAND_NAMES))
def test_command_result_accepts_every_semantic_result_command(command: str) -> None:
    category = result_category_for_command(command)
    assert category is not None

    payload = {
        "ok": True,
        "command": command,
        "category": category.value,
        "payloadMode": "full",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "none",
            "observationQuality": "authoritative",
        },
    }
    payload["nextScreenId"] = "screen-00007"
    payload["screen"] = _public_screen_payload("screen-00007")

    result = CommandResultCore.model_validate(payload)

    assert result.command == command
    assert result.category.value == category.value


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("observe", "wait"),
        ("wait", "observe"),
        ("tap", "open"),
    ],
)
def test_command_result_rejects_command_category_mismatches(
    command: str,
    category: str,
) -> None:
    with pytest.raises(ValidationError, match="category"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": command,
                "category": category,
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_command_result_rejects_daemon_only_error_codes() -> None:
    with pytest.raises(ValidationError, match="Input should be"):
        CommandResultCore.model_validate(
            {
                "ok": False,
                "command": "observe",
                "category": "observe",
                "payloadMode": "none",
                "code": "RUNTIME_NOT_CONNECTED",
                "message": "runtime is not connected to a device",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "none",
                },
            }
        )


def test_command_result_accepts_action_not_confirmed_with_dispatched_truth() -> None:
    result = CommandResultCore.model_validate(
        {
            "ok": False,
            "command": "long-tap",
            "category": "transition",
            "payloadMode": "full",
            "sourceScreenId": "screen-00006",
            "nextScreenId": "screen-00007",
            "code": "ACTION_NOT_CONFIRMED",
            "message": "action was not confirmed on the refreshed screen",
            "truth": {
                "executionOutcome": "dispatched",
                "continuityStatus": "stable",
                "observationQuality": "authoritative",
                "changed": False,
            },
            "screen": _public_screen_payload("screen-00007"),
        }
    )

    assert result.code is SemanticResultCode.ACTION_NOT_CONFIRMED


@pytest.mark.parametrize("execution_outcome", ["notAttempted", "notApplicable"])
def test_command_result_rejects_action_not_confirmed_without_dispatched_truth(
    execution_outcome: str,
) -> None:
    with pytest.raises(ValidationError, match="ACTION_NOT_CONFIRMED"):
        CommandResultCore.model_validate(
            {
                "ok": False,
                "command": "long-tap",
                "category": "transition",
                "payloadMode": "full",
                "sourceScreenId": "screen-00006",
                "nextScreenId": "screen-00007",
                "code": "ACTION_NOT_CONFIRMED",
                "message": "action was not confirmed on the refreshed screen",
                "truth": {
                    "executionOutcome": execution_outcome,
                    "continuityStatus": "stable",
                    "observationQuality": "authoritative",
                    "changed": False,
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


@pytest.mark.parametrize(
    ("field_path", "value", "remove_keys"),
    [
        (("payloadMode",), "none", {"screen", "nextScreenId"}),
        (("screen",), None, {"screen"}),
        (("nextScreenId",), None, {"nextScreenId"}),
        (("truth", "observationQuality"), "none", set()),
    ],
)
def test_command_result_rejects_action_not_confirmed_non_authoritative_shape(
    field_path: tuple[str, ...],
    value: object,
    remove_keys: set[str],
) -> None:
    payload: dict[str, object] = {
        "ok": False,
        "command": "long-tap",
        "category": "transition",
        "payloadMode": "full",
        "sourceScreenId": "screen-00006",
        "nextScreenId": "screen-00007",
        "code": "ACTION_NOT_CONFIRMED",
        "message": "action was not confirmed on the refreshed screen",
        "truth": {
            "executionOutcome": "dispatched",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": False,
        },
        "screen": _public_screen_payload("screen-00007"),
    }
    for key in remove_keys:
        payload.pop(key, None)
    if field_path == ("payloadMode",):
        payload["payloadMode"] = value
    elif field_path == ("screen",):
        payload["screen"] = value
    elif field_path == ("nextScreenId",):
        payload["nextScreenId"] = value
    else:
        _set_nested_payload_value(payload, field_path, value)

    with pytest.raises(ValidationError, match="ACTION_NOT_CONFIRMED"):
        CommandResultCore.model_validate(payload)


def test_post_action_observation_lost_accepts_documented_payload_light_shape() -> None:
    result = CommandResultCore.model_validate(
        _lost_truth_payload(code="POST_ACTION_OBSERVATION_LOST")
    )

    assert result.code is SemanticResultCode.POST_ACTION_OBSERVATION_LOST
    assert dump_canonical_command_result(result) == {
        "ok": False,
        "command": "tap",
        "category": "transition",
        "payloadMode": "none",
        "sourceScreenId": "screen-00006",
        "code": "POST_ACTION_OBSERVATION_LOST",
        "message": "No current screen truth is available.",
        "truth": {
            "executionOutcome": "dispatched",
            "continuityStatus": "none",
            "observationQuality": "none",
        },
        "uncertainty": [],
        "warnings": [],
        "artifacts": {},
    }


@pytest.mark.parametrize(
    ("field_path", "value", "match"),
    [
        (("payloadMode",), "full", "payloadMode='none'"),
        (("truth", "executionOutcome"), "notApplicable", "executionOutcome"),
        (("truth", "continuityStatus"), "stable", "continuityStatus"),
        (("truth", "observationQuality"), "authoritative", "observationQuality"),
        (("truth", "changed"), True, "changed"),
        (
            ("artifacts", "screenshotPng"),
            "/repo/.androidctl/screenshots/shot-001.png",
            "artifact",
        ),
    ],
)
def test_post_action_observation_lost_rejects_non_documented_shape(
    field_path: tuple[str, ...],
    value: object,
    match: str,
) -> None:
    payload = _lost_truth_payload(code="POST_ACTION_OBSERVATION_LOST")
    if field_path == ("payloadMode",):
        payload["payloadMode"] = value
        payload["nextScreenId"] = "screen-00007"
        payload["screen"] = _public_screen_payload("screen-00007")
    else:
        _set_nested_payload_value(payload, field_path, value)

    with pytest.raises(ValidationError, match=match):
        CommandResultCore.model_validate(payload)


@pytest.mark.parametrize(
    "execution_outcome",
    ["notApplicable", "notAttempted", "dispatched"],
)
def test_device_unavailable_accepts_documented_payload_light_shape(
    execution_outcome: str,
) -> None:
    result = CommandResultCore.model_validate(
        _lost_truth_payload(
            code="DEVICE_UNAVAILABLE",
            command="observe",
            category="observe",
            execution_outcome=execution_outcome,
        )
    )

    assert result.code is SemanticResultCode.DEVICE_UNAVAILABLE
    canonical = dump_canonical_command_result(result)
    assert canonical["truth"]["executionOutcome"] == execution_outcome
    assert canonical["sourceScreenId"] == "screen-00006"
    assert "nextScreenId" not in canonical
    assert "screen" not in canonical
    assert "changed" not in canonical["truth"]
    assert canonical["artifacts"] == {}


@pytest.mark.parametrize(
    ("field_path", "value", "match"),
    [
        (("payloadMode",), "full", "payloadMode='none'"),
        (("truth", "continuityStatus"), "stable", "continuityStatus"),
        (("truth", "observationQuality"), "authoritative", "observationQuality"),
        (("truth", "changed"), True, "changed"),
        (
            ("artifacts", "screenshotPng"),
            "/repo/.androidctl/screenshots/shot-001.png",
            "artifact",
        ),
    ],
)
def test_device_unavailable_rejects_non_documented_shape(
    field_path: tuple[str, ...],
    value: object,
    match: str,
) -> None:
    payload = _lost_truth_payload(
        code="DEVICE_UNAVAILABLE",
        command="observe",
        category="observe",
        execution_outcome="notApplicable",
    )
    if field_path == ("payloadMode",):
        payload["payloadMode"] = value
        payload["nextScreenId"] = "screen-00007"
        payload["screen"] = _public_screen_payload("screen-00007")
    else:
        _set_nested_payload_value(payload, field_path, value)

    with pytest.raises(ValidationError, match=match):
        CommandResultCore.model_validate(payload)


@pytest.mark.parametrize(
    "code",
    [
        "TARGET_NOT_ACTIONABLE",
        "OPEN_FAILED",
        "TYPE_NOT_CONFIRMED",
        "SUBMIT_NOT_CONFIRMED",
    ],
)
def test_command_result_accepts_extra_semantic_result_codes(code: str) -> None:
    result = CommandResultCore.model_validate(
        {
            "ok": False,
            "command": "observe",
            "category": "observe",
            "payloadMode": "none",
            "code": code,
            "message": "semantic command failed",
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "none",
                "observationQuality": "none",
            },
        }
    )

    assert result.code is SemanticResultCode(code)


@pytest.mark.parametrize("quality", ["partial", "degraded"])
def test_command_result_rejects_public_partial_or_degraded_quality(
    quality: str,
) -> None:
    with pytest.raises(ValidationError, match="observationQuality"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "observe",
                "category": "observe",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": quality,
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_command_result_rejects_daemon_kind_name_for_long_tap() -> None:
    with pytest.raises(ValidationError, match="command must be one of"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "longTap",
                "category": "transition",
                "payloadMode": "full",
                "sourceScreenId": "screen-00006",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "dispatched",
                    "continuityStatus": "stable",
                    "observationQuality": "authoritative",
                    "changed": True,
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_lifecycle_category_is_reserved_for_close_results() -> None:
    with pytest.raises(ValidationError, match="category"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "wait",
                "category": "lifecycle",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
            }
        )


def test_artifact_payload_enforces_public_screenshot_namespace() -> None:
    with pytest.raises(ValueError, match="screenshotPng"):
        ArtifactPayload.model_validate(
            {
                "screenshotPng": "/repo/.androidctl/artifacts/shot-00001.png",
            }
        )

    payload = ArtifactPayload.model_validate(
        {
            "screenshotPng": "/repo/.androidctl/screenshots/shot-00001.png",
        }
    )

    assert payload.screenshot_png == "/repo/.androidctl/screenshots/shot-00001.png"


def test_artifact_payload_accepts_screen_xml_public_namespace() -> None:
    payload = ArtifactPayload.model_validate(
        {
            "screenXml": "/repo/.androidctl/artifacts/screens/screen-00001.xml",
        }
    )

    assert payload.screen_xml == (
        "/repo/.androidctl/artifacts/screens/screen-00001.xml"
    )
    assert payload.model_dump(exclude_none=True) == {
        "screenXml": "/repo/.androidctl/artifacts/screens/screen-00001.xml",
    }


@pytest.mark.parametrize(
    "screen_xml_path",
    [
        ".androidctl/artifacts/screens/screen-00001.xml",
        "/repo/.androidctl/screens/screen-00001.xml",
        "/repo/.androidctl/screenshots/screen-00001.xml",
        "/repo/.androidctl/artifacts/other/screen-00001.xml",
        "/repo/.androidctl/artifacts/screens/../other/screen-00001.xml",
    ],
)
def test_artifact_payload_rejects_invalid_screen_xml_namespace(
    screen_xml_path: str,
) -> None:
    with pytest.raises(ValueError, match="screenXml|absolute"):
        ArtifactPayload.model_validate({"screenXml": screen_xml_path})


def test_artifact_payload_rejects_screen_md() -> None:
    with pytest.raises(ValidationError, match="screenMd"):
        ArtifactPayload.model_validate(
            {
                "screenMd": "/repo/.androidctl/screens/screen-00001.md",
            }
        )


@pytest.mark.parametrize(
    "warning",
    [
        "ARTIFACT_SCREEN_XML_MISSING",
        "ARTIFACT_SCREEN_XML_GARBAGE_COLLECTED",
        "artifactMissing",
        "artifactGarbageCollected",
    ],
)
def test_command_result_rejects_artifact_warning_tokens_after_registry_update(
    warning: str,
) -> None:
    with pytest.raises(ValidationError, match="warnings"):
        CommandResultCore.model_validate(
            {
                "ok": True,
                "command": "observe",
                "category": "observe",
                "payloadMode": "full",
                "nextScreenId": "screen-00007",
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "none",
                    "observationQuality": "authoritative",
                },
                "screen": _public_screen_payload("screen-00007"),
                "warnings": [warning],
            }
        )


def test_command_result_accepts_ordinary_warning_strings() -> None:
    result = CommandResultCore.model_validate(
        {
            "ok": True,
            "command": "observe",
            "category": "observe",
            "payloadMode": "full",
            "nextScreenId": "screen-00007",
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "none",
                "observationQuality": "authoritative",
            },
            "screen": _public_screen_payload("screen-00007"),
            "warnings": ["screen changed while rendering"],
        }
    )

    assert result.warnings == ["screen changed while rendering"]


@pytest.mark.parametrize(
    "screenshot_path",
    [
        "/repo/.androidctl/screenshots/shot-00001.png",
        r"\repo\.androidctl\screenshots\shot-00001.png",
        "D:/repo/.androidctl/screenshots/shot-00001.png",
        r"D:\repo\.androidctl\screenshots\shot-00001.png",
    ],
)
def test_artifact_payload_accepts_cross_platform_absolute_paths(
    screenshot_path: str,
) -> None:
    payload = ArtifactPayload.model_validate({"screenshotPng": screenshot_path})

    assert payload.screenshot_png == screenshot_path


def test_artifact_payload_rejects_relative_screenshot_path() -> None:
    with pytest.raises(ValueError, match="daemon-wire paths must be absolute"):
        ArtifactPayload.model_validate(
            {"screenshotPng": ".androidctl/screenshots/shot-00001.png"}
        )


@pytest.mark.parametrize(
    "screenshot_path",
    [
        "/repo/.androidctl/screenshots/../screens/shot-00001.png",
        "/repo/.androidctl/screenshots/../../outside.png",
        r"D:\repo\.androidctl\screenshots\..\screens\shot-00001.png",
    ],
)
def test_artifact_payload_rejects_screenshot_namespace_escape(
    screenshot_path: str,
) -> None:
    with pytest.raises(ValueError, match="screenshotPng"):
        ArtifactPayload.model_validate({"screenshotPng": screenshot_path})


@pytest.mark.parametrize(
    "screen_xml_path",
    [
        "/repo/.androidctl/artifacts/screens/screen-00001.xml",
        r"\repo\.androidctl\artifacts\screens\screen-00001.xml",
        "D:/repo/.androidctl/artifacts/screens/screen-00001.xml",
        r"D:\repo\.androidctl\artifacts\screens\screen-00001.xml",
    ],
)
def test_artifact_payload_accepts_cross_platform_screen_xml_paths(
    screen_xml_path: str,
) -> None:
    payload = ArtifactPayload.model_validate({"screenXml": screen_xml_path})

    assert payload.screen_xml == screen_xml_path


def test_changed_true_and_false_require_source_screen_id() -> None:
    for changed in (True, False):
        with pytest.raises(ValidationError, match="sourceScreenId"):
            CommandResultCore.model_validate(
                {
                    "ok": True,
                    "command": "observe",
                    "category": "observe",
                    "payloadMode": "full",
                    "nextScreenId": "screen-00007",
                    "truth": {
                        "executionOutcome": "notApplicable",
                        "continuityStatus": "none",
                        "observationQuality": "authoritative",
                        "changed": changed,
                    },
                    "screen": _public_screen_payload("screen-00007"),
                }
            )


@pytest.mark.parametrize("changed", [True, False])
def test_changed_true_and_false_preserve_with_source_screen_id(
    changed: bool,
) -> None:
    payload = {
        "ok": True,
        "command": "observe",
        "category": "observe",
        "payloadMode": "full",
        "sourceScreenId": "screen-00006",
        "nextScreenId": "screen-00007",
        "truth": {
            "executionOutcome": "notApplicable",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": changed,
        },
        "screen": _public_screen_payload("screen-00007"),
    }

    canonical = dump_canonical_command_result(payload)

    assert canonical["truth"]["changed"] is changed


def test_changed_explicit_null_canonicalizes_to_omission_without_source() -> None:
    canonical = dump_canonical_command_result(
        {
            "ok": True,
            "command": "observe",
            "category": "observe",
            "payloadMode": "full",
            "nextScreenId": "screen-00007",
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "none",
                "observationQuality": "authoritative",
                "changed": None,
            },
            "screen": _public_screen_payload("screen-00007"),
        }
    )

    assert "sourceScreenId" not in canonical
    assert "changed" not in canonical["truth"]


def test_changed_explicit_null_canonicalizes_to_omission_with_source() -> None:
    canonical = dump_canonical_command_result(
        {
            "ok": True,
            "command": "observe",
            "category": "observe",
            "payloadMode": "full",
            "sourceScreenId": "screen-00006",
            "nextScreenId": "screen-00007",
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "stable",
                "observationQuality": "authoritative",
                "changed": None,
            },
            "screen": _public_screen_payload("screen-00007"),
        }
    )

    assert canonical["sourceScreenId"] == "screen-00006"
    assert "changed" not in canonical["truth"]
