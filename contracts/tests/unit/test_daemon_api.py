from __future__ import annotations

from typing import get_type_hints

import pytest
from public_screen_payloads import public_screen_payload_base
from pydantic import ValidationError

import androidctl_contracts
import androidctl_contracts.daemon_api as daemon_api
from androidctl_contracts.command_catalog import (
    entries_for_route,
    is_public_command,
    runtime_close_entry,
)
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import (
    OWNER_HEADER_NAME,
    TOKEN_HEADER_NAME,
    CommandRunRequest,
    DaemonErrorEnvelope,
    DaemonSuccessEnvelope,
    HealthResult,
    RuntimeGetResult,
    RuntimePayload,
)
from androidctl_contracts.errors import DaemonErrorCode


def _public_screen_payload(screen_id: str) -> dict[str, object]:
    payload = public_screen_payload_base(screen_id)
    app = payload["app"]
    assert isinstance(app, dict)
    app["activityName"] = "SettingsActivity"
    return payload


_CANONICAL_COMMAND_RUN_SAMPLES: dict[str, dict[str, object]] = {
    "connect": {
        "kind": "connect",
        "connection": {
            "mode": "adb",
            "token": "device-token",
            "serial": "emulator-5554",
        },
    },
    "observe": {"kind": "observe"},
    "open": {
        "kind": "open",
        "target": {"kind": "app", "value": "com.android.settings"},
    },
    "tap": {"kind": "tap", "ref": "n1", "sourceScreenId": "screen-1"},
    "longTap": {"kind": "longTap", "ref": "n1", "sourceScreenId": "screen-1"},
    "focus": {"kind": "focus", "ref": "n1", "sourceScreenId": "screen-1"},
    "type": {
        "kind": "type",
        "ref": "n1",
        "text": "hello",
        "sourceScreenId": "screen-1",
    },
    "submit": {"kind": "submit", "ref": "n1", "sourceScreenId": "screen-1"},
    "scroll": {
        "kind": "scroll",
        "ref": "n1",
        "direction": "down",
        "sourceScreenId": "screen-1",
    },
    "back": {"kind": "back", "sourceScreenId": "screen-1"},
    "home": {"kind": "home", "sourceScreenId": "screen-1"},
    "recents": {"kind": "recents", "sourceScreenId": "screen-1"},
    "notifications": {"kind": "notifications", "sourceScreenId": "screen-1"},
    "wait": {"kind": "wait", "predicate": {"kind": "idle"}, "timeoutMs": 100},
    "listApps": {"kind": "listApps"},
    "screenshot": {"kind": "screenshot"},
}


def test_public_command_helper_accepts_current_surface_names() -> None:
    for name in {"connect", "observe", "wait", "list-apps", "close"}:
        assert is_public_command(name) is True

    for name in {"raw", "snapshot", "watch", "wait-text", "swipe"}:
        assert is_public_command(name) is False


def test_daemon_command_payload_and_run_request_are_canonical_exports() -> None:
    from androidctl_contracts import CommandRunRequest as package_command_run_request
    from androidctl_contracts import (
        DaemonCommandPayload as package_daemon_command_payload,
    )
    from androidctl_contracts.daemon_api import (
        CommandRunRequest as module_command_run_request,
    )
    from androidctl_contracts.daemon_api import (
        DaemonCommandPayload as module_daemon_command_payload,
    )

    assert package_command_run_request is module_command_run_request
    assert package_daemon_command_payload is module_daemon_command_payload
    assert CommandRunRequest.__annotations__["command"] == "DaemonCommandPayload"
    assert (
        get_type_hints(
            package_command_run_request,
            globalns=vars(daemon_api),
            localns=vars(daemon_api),
            include_extras=True,
        )["command"]
        == module_daemon_command_payload
    )
    assert "DaemonCommandPayload" in daemon_api.__all__
    assert "DaemonCommandPayload" in androidctl_contracts.__all__


@pytest.mark.parametrize(
    "command_payload",
    [
        {"kind": "tap", "ref": "n3", "sourceScreenId": "screen-1"},
        {"kind": "type", "ref": "n3", "text": "wifi", "sourceScreenId": "screen-1"},
        {
            "kind": "scroll",
            "ref": "n3",
            "direction": "down",
            "sourceScreenId": "screen-1",
        },
    ],
)
def test_ref_bound_command_requests_require_source_screen_id(
    command_payload: dict[str, object],
) -> None:
    request = CommandRunRequest.model_validate({"command": command_payload})

    assert request.model_dump()["command"]["sourceScreenId"] == "screen-1"

    invalid_payload = dict(command_payload)
    invalid_payload.pop("sourceScreenId")
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate({"command": invalid_payload})


@pytest.mark.parametrize(
    "predicate_payload",
    [
        {"kind": "screen-change", "sourceScreenId": "screen-1"},
        {"kind": "gone", "ref": "n3", "sourceScreenId": "screen-1"},
    ],
)
def test_screen_relative_wait_predicates_require_source_screen_id(
    predicate_payload: dict[str, object],
) -> None:
    command = daemon_api.WaitCommandPayload.model_validate(
        {"kind": "wait", "predicate": predicate_payload}
    )

    assert command.model_dump()["predicate"]["sourceScreenId"] == "screen-1"

    invalid_payload = dict(predicate_payload)
    invalid_payload.pop("sourceScreenId")
    with pytest.raises(ValidationError):
        daemon_api.WaitCommandPayload.model_validate(
            {"kind": "wait", "predicate": invalid_payload}
        )


@pytest.mark.parametrize(
    "predicate_payload",
    [
        {"kind": "text-present", "text": "wifi"},
        {"kind": "app", "packageName": "com.android.settings"},
        {"kind": "idle"},
    ],
)
def test_non_screen_relative_wait_predicates_remain_screen_id_free(
    predicate_payload: dict[str, object],
) -> None:
    command = daemon_api.WaitCommandPayload.model_validate(
        {"kind": "wait", "predicate": predicate_payload}
    )

    assert command.model_dump()["predicate"] == predicate_payload


def test_header_names_match_documented_contract() -> None:
    assert TOKEN_HEADER_NAME == "X-Androidctld-Token"
    assert OWNER_HEADER_NAME == "X-Androidctld-Owner"


def test_health_result_round_trips_documented_fields() -> None:
    payload = {
        "service": "androidctld",
        "version": "0.1.0",
        "workspaceRoot": "/repo",
        "ownerId": "owner-1",
    }

    result = HealthResult.model_validate(payload)

    assert result.model_dump() == payload


def test_runtime_get_result_round_trips_documented_payload() -> None:
    runtime = RuntimeGetResult.model_validate(
        {
            "runtime": {
                "workspaceRoot": "/repo",
                "artifactRoot": "/repo/.androidctl",
                "status": "ready",
                "currentScreenId": "screen-00006",
            }
        }
    )

    assert runtime.runtime == RuntimePayload(
        workspace_root="/repo",
        artifact_root="/repo/.androidctl",
        status="ready",
        current_screen_id="screen-00006",
    )


def test_runtime_close_response_uses_retained_result() -> None:
    payload = {
        "ok": True,
        "result": {
            "ok": True,
            "command": "close",
            "envelope": "lifecycle",
            "artifacts": {},
            "details": {},
        },
    }

    envelope = DaemonSuccessEnvelope[RetainedResultEnvelope].model_validate(payload)

    assert isinstance(envelope.result, RetainedResultEnvelope)
    assert envelope.result.command == "close"
    assert envelope.result.envelope.value == "lifecycle"
    assert envelope.model_dump(by_alias=True, exclude_none=True) == payload


def test_daemon_success_envelope_can_carry_retained_result_model_sample() -> None:
    payload = {
        "ok": True,
        "result": {
            "ok": True,
            "command": "screenshot",
            "envelope": "artifact",
            "artifacts": {},
            "details": {"format": "png"},
        },
    }

    envelope = DaemonSuccessEnvelope[RetainedResultEnvelope].model_validate(payload)

    assert isinstance(envelope.result, RetainedResultEnvelope)
    assert envelope.result.command == "screenshot"
    assert envelope.result.envelope.value == "artifact"
    assert envelope.model_dump(by_alias=True, exclude_none=True) == payload


def test_daemon_success_envelope_can_carry_list_apps_result_model_sample() -> None:
    payload = {
        "ok": True,
        "result": {
            "ok": True,
            "command": "list-apps",
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                }
            ],
        },
    }

    envelope = DaemonSuccessEnvelope[ListAppsResult].model_validate(payload)

    assert isinstance(envelope.result, ListAppsResult)
    assert envelope.result.command == "list-apps"
    assert envelope.result.apps[0].package_name == "com.android.settings"
    assert envelope.model_dump(by_alias=True, exclude_none=True) == payload


def test_command_run_request_accepts_commands_run_payloads_and_rejects_close() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "open",
                "target": {"kind": "app", "value": "com.android.settings"},
            },
        }
    )

    assert request.command.kind == "open"
    assert request.command.target.kind == "app"
    assert not hasattr(request, "options")

    with pytest.raises(ValidationError, match="kind"):
        CommandRunRequest.model_validate(
            {
                "command": {"kind": "close"},
            }
        )


def test_command_run_request_uses_daemon_kind_spelling_for_list_apps() -> None:
    request = CommandRunRequest.model_validate(
        {"command": {"kind": " listApps "}},
        strict=True,
    )

    assert isinstance(request.command, daemon_api.ListAppsCommandPayload)
    assert request.command.kind == "listApps"
    assert request.model_dump() == {"command": {"kind": "listApps"}}

    with pytest.raises(ValidationError, match="kind"):
        CommandRunRequest.model_validate(
            {"command": {"kind": "list-apps"}},
            strict=True,
        )

    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {"command": {"kind": "listApps", "includeSystem": True}},
            strict=True,
        )


def test_connect_wire_adb_omits_port_and_uses_no_wire_default() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "connect",
                "connection": {
                    "mode": "adb",
                    "token": "device-token",
                    "serial": "emulator-5554",
                },
            }
        },
        strict=True,
    )

    assert isinstance(request.command, daemon_api.ConnectCommandPayload)
    assert request.command.connection.port is None
    assert request.model_dump(exclude_none=True)["command"]["connection"] == {
        "mode": "adb",
        "token": "device-token",
        "serial": "emulator-5554",
    }


@pytest.mark.parametrize(
    "connection",
    [
        {
            "mode": "adb",
            "token": "device-token",
            "host": "127.0.0.1",
        },
        {
            "mode": "adb",
            "token": "device-token",
            "port": 17171,
        },
        {
            "mode": "lan",
            "token": "device-token",
            "host": "127.0.0.1",
        },
        {
            "mode": "lan",
            "token": "device-token",
            "host": "127.0.0.1",
            "port": 17171,
            "serial": "emulator-5554",
        },
    ],
)
def test_connect_wire_enforces_mode_specific_shape(
    connection: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {"command": {"kind": "connect", "connection": connection}},
            strict=True,
        )


def test_connect_wire_lan_requires_explicit_positive_port() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "connect",
                "connection": {
                    "mode": "lan",
                    "token": "device-token",
                    "host": "127.0.0.1",
                    "port": 17171,
                },
            }
        },
        strict=True,
    )

    assert isinstance(request.command, daemon_api.ConnectCommandPayload)
    assert request.command.connection.host == "127.0.0.1"
    assert request.command.connection.port == 17171

    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "lan",
                        "token": "device-token",
                        "host": "127.0.0.1",
                        "port": 0,
                    },
                }
            },
            strict=True,
        )


def test_connect_wire_strictly_rejects_bool_port() -> None:
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {
                "command": {
                    "kind": "connect",
                    "connection": {
                        "mode": "lan",
                        "token": "device-token",
                        "host": "127.0.0.1",
                        "port": True,
                    },
                }
            },
            strict=True,
        )


def test_command_run_catalog_entries_have_canonical_shared_request_samples() -> None:
    commands_run_entries = entries_for_route("commands_run")

    assert set(_CANONICAL_COMMAND_RUN_SAMPLES) == {
        entry.daemon_kind for entry in commands_run_entries
    }

    for entry in commands_run_entries:
        assert entry.daemon_kind is not None
        sample = _CANONICAL_COMMAND_RUN_SAMPLES[entry.daemon_kind]
        request = CommandRunRequest.model_validate({"command": sample}, strict=True)

        assert request.command.kind == entry.daemon_kind


def test_close_is_the_only_runtime_close_catalog_entry_and_not_commands_run() -> None:
    runtime_close = runtime_close_entry()

    assert runtime_close.public_name == "close"
    assert runtime_close.daemon_kind is None

    with pytest.raises(ValidationError, match="kind"):
        CommandRunRequest.model_validate({"command": {"kind": "close"}}, strict=True)


def test_command_run_request_uses_daemon_kind_spelling_for_long_tap() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "longTap",
                "ref": "submit_button",
                "sourceScreenId": "screen-00006",
            },
        }
    )

    assert request.command.kind == "longTap"

    with pytest.raises(ValidationError, match="kind"):
        CommandRunRequest.model_validate(
            {
                "command": {
                    "kind": "long-tap",
                    "ref": "submit_button",
                    "sourceScreenId": "screen-00006",
                },
            }
        )


def test_commands_run_wait_idle_can_omit_source_screen_id() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "wait",
                "predicate": {"kind": "idle"},
                "timeoutMs": 100,
            },
        }
    )

    assert request.command.kind == "wait"
    assert request.command.predicate.kind == "idle"


def test_global_actions_with_explicit_source_screen_id_preserve_it() -> None:
    request = CommandRunRequest.model_validate(
        {
            "command": {
                "kind": "back",
                "sourceScreenId": "screen-1",
            },
        }
    )

    assert request.command.kind == "back"
    assert request.model_dump()["command"]["sourceScreenId"] == "screen-1"


@pytest.mark.parametrize("source_screen_id", ["", "   "])
def test_global_actions_reject_blank_source_screen_id(
    source_screen_id: str,
) -> None:
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {
                "command": {
                    "kind": "recents",
                    "sourceScreenId": source_screen_id,
                },
            }
        )


def test_global_actions_can_omit_source_screen_id_after_p2_4() -> None:
    request = CommandRunRequest.model_validate({"command": {"kind": "home"}})

    assert request.command.kind == "home"
    assert "sourceScreenId" not in request.model_dump(exclude_none=True)["command"]


def test_daemon_error_envelope_rejects_semantic_result_codes() -> None:
    with pytest.raises(ValidationError):
        DaemonErrorEnvelope.model_validate(
            {
                "ok": False,
                "error": {
                    "code": "WAIT_TIMEOUT",
                    "message": "Condition was not satisfied before timeout.",
                    "retryable": False,
                    "details": {},
                },
            }
        )


def test_models_accept_snake_case_kwargs_but_dump_camel_case_wire_keys() -> None:
    health = HealthResult(
        service="androidctld",
        version="0.1.0",
        workspace_root="/repo",
        owner_id="owner-1",
    )
    runtime = RuntimePayload(
        workspace_root="/repo",
        artifact_root="/repo/.androidctl",
        status="ready",
        current_screen_id="screen-00006",
    )
    request = CommandRunRequest(
        command={
            "kind": "wait",
            "predicate": {"kind": "idle"},
            "timeoutMs": 2000,
        },
    )

    assert health.model_dump() == {
        "service": "androidctld",
        "version": "0.1.0",
        "workspaceRoot": "/repo",
        "ownerId": "owner-1",
    }
    assert runtime.model_dump(exclude_none=True) == {
        "workspaceRoot": "/repo",
        "artifactRoot": "/repo/.androidctl",
        "status": "ready",
        "currentScreenId": "screen-00006",
    }
    assert request.model_dump(exclude_none=True) == {
        "command": {
            "kind": "wait",
            "predicate": {"kind": "idle"},
            "timeoutMs": 2000,
        },
    }


def test_command_run_request_rejects_unknown_root_fields() -> None:
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {
                "command": {
                    "kind": "observe",
                },
                "unexpected": "value",
            },
            strict=True,
        )


def test_command_run_request_rejects_removed_raw_kind() -> None:
    removed_kind = "ra" + "w"
    with pytest.raises(ValidationError):
        CommandRunRequest.model_validate(
            {"command": {"kind": removed_kind, "subcommand": "rpc"}},
            strict=True,
        )


def test_command_result_core_round_trips_via_daemon_success_envelope() -> None:
    payload = {
        "ok": True,
        "result": {
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
        },
    }

    envelope = DaemonSuccessEnvelope[CommandResultCore].model_validate(payload)

    assert envelope.result.command == "observe"
    assert envelope.model_dump(by_alias=True, exclude_none=True) == {
        "ok": True,
        "result": {
            **payload["result"],
            "uncertainty": [],
            "warnings": [],
            "artifacts": {},
        },
    }


def test_daemon_error_codes_remain_boundary_only() -> None:
    codes = {code.value for code in DaemonErrorCode}

    assert DaemonErrorCode.DAEMON_BAD_REQUEST.value == "DAEMON_BAD_REQUEST"
    assert DaemonErrorCode.DAEMON_UNAUTHORIZED.value == "DAEMON_UNAUTHORIZED"
    assert (
        DaemonErrorCode.DEVICE_RPC_TRANSPORT_RESET.value == "DEVICE_RPC_TRANSPORT_RESET"
    )
    assert (
        DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED.value == "DEVICE_AGENT_UNAUTHORIZED"
    )
    assert "COMMAND_NOT_FOUND" in codes
    assert "COMMAND_CANCELLED" in codes
