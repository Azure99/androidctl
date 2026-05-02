from __future__ import annotations

import pytest
from pydantic import ValidationError

from androidctl_contracts.daemon_api import GlobalActionCommandPayload, RuntimePayload


def test_contract_models_accept_snake_case_kwargs_and_dump_aliases_by_default() -> None:
    payload = RuntimePayload(
        workspace_root="/repo",
        artifact_root="/repo/.androidctl",
        status="ready",
        current_screen_id="screen-00006",
    )

    assert payload.workspace_root == "/repo"
    assert payload.current_screen_id == "screen-00006"
    assert payload.model_dump(exclude_none=True) == {
        "workspaceRoot": "/repo",
        "artifactRoot": "/repo/.androidctl",
        "status": "ready",
        "currentScreenId": "screen-00006",
    }
    assert payload.model_dump_json(exclude_none=True) == (
        '{"workspaceRoot":"/repo","artifactRoot":"/repo/.androidctl",'
        '"status":"ready","currentScreenId":"screen-00006"}'
    )


def test_contract_models_reject_camel_case_kwargs() -> None:
    with pytest.raises(ValidationError, match="workspaceRoot"):
        RuntimePayload(
            workspaceRoot="/repo",
            artifactRoot="/repo/.androidctl",
            status="ready",
        )


def test_contract_validation_errors_keep_wire_alias_locations() -> None:
    with pytest.raises(ValidationError) as error:
        RuntimePayload.model_validate(
            {
                "workspaceRoot": "repo",
                "artifactRoot": "/repo/.androidctl",
                "status": "ready",
            }
        )

    assert error.value.errors()[0]["loc"] == ("workspaceRoot",)


def test_contract_model_copy_rejects_camel_case_update_keys() -> None:
    payload = GlobalActionCommandPayload(kind="home", source_screen_id="screen-0")

    with pytest.raises(ValidationError, match="sourceScreenId"):
        payload.model_copy(update={"sourceScreenId": "screen-1"})


def test_contract_model_copy_accepts_snake_case_update_keys() -> None:
    payload = GlobalActionCommandPayload(kind="home", source_screen_id="screen-0")

    updated = payload.model_copy(update={"source_screen_id": "screen-1"})

    assert updated.source_screen_id == "screen-1"
    assert not hasattr(updated, "sourceScreenId")
    assert updated.model_dump(exclude_none=True) == {
        "kind": "home",
        "sourceScreenId": "screen-1",
    }
