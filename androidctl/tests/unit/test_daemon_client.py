from __future__ import annotations

import json

import httpx
import pytest
from androidctl_contracts.command_results import (
    CommandResultCore,
    ListAppsResult,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import (
    CommandRunRequest,
    ConnectCommandPayload,
    ConnectionPayload,
    IdlePredicatePayload,
    ListAppsCommandPayload,
    ObserveCommandPayload,
    RefActionCommandPayload,
    RuntimePayload,
    ScreenshotCommandPayload,
    WaitCommandPayload,
)
from androidctl_contracts.user_state import ActiveDaemonRecord

from androidctl import __version__ as ANDROIDCTL_VERSION
from androidctl.daemon.client import (
    DaemonApiError,
    DaemonClient,
    DaemonProtocolError,
    IncompatibleDaemonError,
    IncompatibleDaemonVersionError,
)


def _public_screen_payload(screen_id: str) -> dict[str, object]:
    return {
        "screenId": screen_id,
        "app": {"packageName": "com.android.settings"},
        "surface": {
            "keyboardVisible": False,
            "focus": {},
        },
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {"name": "context", "nodes": []},
            {"name": "dialog", "nodes": []},
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [],
    }


def _retained_payload(
    *,
    command: str,
    envelope: str,
    ok: bool = True,
    code: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": ok,
        "command": command,
        "envelope": envelope,
        "artifacts": {},
        "details": {},
    }
    if code is not None:
        payload["code"] = code
    if message is not None:
        payload["message"] = message
    return payload


def test_health_from_active_record_posts_caller_owner_header() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "service": "androidctld",
                    "version": "0.1.0",
                    "workspaceRoot": "/repo",
                    "ownerId": "shell:caller:1",
                },
            },
        )

    record = ActiveDaemonRecord(
        pid=1234,
        host="127.0.0.1",
        port=8765,
        token="secret",
        started_at="2026-03-27T00:00:00Z",
        workspace_root="/repo",
        owner_id="shell:caller:1",
    )
    client = DaemonClient.from_active_record(
        record,
        owner_id="shell:caller:1",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8765",
        ),
    )

    health = client.health(record)

    assert health.workspace_root == "/repo"
    assert health.owner_id == "shell:caller:1"
    assert seen_headers["x-androidctld-token"] == "secret"
    assert seen_headers["x-androidctld-owner"] == "shell:caller:1"


def test_health_rejects_release_version_mismatch() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "service": "androidctld",
                            "version": "0.1.1",
                            "workspaceRoot": "/repo",
                            "ownerId": "shell:self:1",
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        IncompatibleDaemonVersionError,
        match=(
            "androidctl/androidctld release version mismatch: "
            f"cli={ANDROIDCTL_VERSION} daemon=0.1.1"
        ),
    ):
        client.health()


def test_health_checks_active_record_identity_before_release_version_gate() -> None:
    record = ActiveDaemonRecord(
        pid=1234,
        host="127.0.0.1",
        port=8765,
        token="secret",
        started_at="2026-03-27T00:00:00Z",
        workspace_root="/repo",
        owner_id="shell:self:1",
    )
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "service": "androidctld",
                            "version": "0.1.1",
                            "workspaceRoot": "/other-workspace",
                            "ownerId": "shell:other:1",
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        DaemonProtocolError,
        match="health response workspace root mismatch",
    ):
        client.health(record)


def test_health_classifies_extra_api_version_field_as_incompatible_daemon() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "service": "androidctld",
                            "version": ANDROIDCTL_VERSION,
                            "apiVersion": 1,
                            "workspaceRoot": "/repo",
                            "ownerId": "shell:self:1",
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        IncompatibleDaemonError,
        match="health payload is incompatible",
    ):
        client.health()


def test_get_runtime_returns_typed_runtime_payload() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "path": request.url.path,
                "body": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "runtime": {
                        "workspaceRoot": "/repo",
                        "artifactRoot": "/repo/.androidctl",
                        "status": "ready",
                    }
                },
            },
        )

    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    runtime = client.get_runtime()

    assert isinstance(runtime, RuntimePayload)
    assert runtime.workspace_root == "/repo"
    assert seen == [{"path": "/runtime/get", "body": {}}]


def test_get_runtime_rejects_missing_runtime_route_payload() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "status": "ready",
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        DaemonProtocolError,
        match="invalid runtime/get response schema",
    ):
        client.get_runtime()


def test_run_command_posts_semantic_payload_without_client_command_id() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "body": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "ok": True,
                    "command": "tap",
                    "category": "transition",
                    "payloadMode": "full",
                    "sourceScreenId": "screen-00001",
                    "nextScreenId": "screen-00002",
                    "truth": {
                        "executionOutcome": "dispatched",
                        "continuityStatus": "stable",
                        "observationQuality": "authoritative",
                        "changed": False,
                    },
                    "screen": _public_screen_payload("screen-00002"),
                    "warnings": [],
                    "artifacts": {},
                },
            },
        )

    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    result = client.run_command(
        request=CommandRunRequest(
            command=RefActionCommandPayload(
                kind="tap",
                ref="n3",
                source_screen_id="screen-00001",
            )
        ),
    )

    assert isinstance(result, CommandResultCore)
    assert result.command == "tap"
    assert seen == [
        {
            "body": {
                "command": {
                    "kind": "tap",
                    "ref": "n3",
                    "sourceScreenId": "screen-00001",
                },
            }
        }
    ]


@pytest.mark.parametrize(
    ("command_request", "expected_command", "expected_envelope"),
    [
        (
            CommandRunRequest(
                command=ConnectCommandPayload(
                    kind="connect",
                    connection=ConnectionPayload(mode="adb", token="abc"),
                )
            ),
            "connect",
            "bootstrap",
        ),
        (
            CommandRunRequest(command=ScreenshotCommandPayload(kind="screenshot")),
            "screenshot",
            "artifact",
        ),
    ],
)
def test_run_command_returns_typed_retained_results_for_retained_commands(
    command_request: CommandRunRequest,
    expected_command: str,
    expected_envelope: str,
) -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command=expected_command,
                            envelope=expected_envelope,
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    result = client.run_command(request=command_request)

    assert isinstance(result, RetainedResultEnvelope)
    assert result.command == expected_command
    assert result.envelope.value == expected_envelope


def test_run_command_returns_typed_list_apps_result() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"body": json.loads(request.content.decode("utf-8"))})
        return httpx.Response(
            200,
            json={
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
            },
        )

    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    result = client.run_command(
        request=CommandRunRequest(command=ListAppsCommandPayload(kind="listApps"))
    )

    assert isinstance(result, ListAppsResult)
    assert result.command == "list-apps"
    assert result.apps[0].package_name == "com.android.settings"
    assert seen == [{"body": {"command": {"kind": "listApps"}}}]


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "command": "list-apps"},
        {"ok": True, "command": "list-apps", "apps": "bad"},
    ],
)
def test_list_apps_command_rejects_malformed_result_payload(
    payload: dict[str, object],
) -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"ok": True, "result": payload},
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(command=ListAppsCommandPayload(kind="listApps"))
        )


def test_semantic_command_rejects_retained_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command="screenshot",
                            envelope="artifact",
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(command=ObserveCommandPayload(kind="observe"))
        )


def test_retained_command_rejects_semantic_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "ok": True,
                            "command": "screenshot",
                            "category": "artifact",
                            "payloadMode": "full",
                            "sourceScreenId": "screen-00001",
                            "nextScreenId": "screen-00002",
                            "truth": {
                                "executionOutcome": "notApplicable",
                                "continuityStatus": "stable",
                                "observationQuality": "authoritative",
                                "changed": False,
                            },
                            "screen": _public_screen_payload("screen-00002"),
                            "warnings": [],
                            "artifacts": {},
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(
                command=ScreenshotCommandPayload(kind="screenshot")
            )
        )


def test_list_apps_command_rejects_semantic_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "ok": True,
                            "command": "list-apps",
                            "category": "observe",
                            "payloadMode": "full",
                            "truth": {
                                "executionOutcome": "notApplicable",
                                "continuityStatus": "stable",
                                "observationQuality": "authoritative",
                                "changed": False,
                            },
                            "screen": _public_screen_payload("screen-00002"),
                            "warnings": [],
                            "artifacts": {},
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(command=ListAppsCommandPayload(kind="listApps"))
        )


def test_list_apps_command_rejects_retained_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command="screenshot",
                            envelope="artifact",
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(command=ListAppsCommandPayload(kind="listApps"))
        )


def test_semantic_command_rejects_list_apps_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "ok": True,
                            "command": "list-apps",
                            "apps": [],
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(command=ObserveCommandPayload(kind="observe"))
        )


def test_retained_command_rejects_list_apps_result_shape() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "ok": True,
                            "command": "list-apps",
                            "apps": [],
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(
                command=ScreenshotCommandPayload(kind="screenshot")
            )
        )


def test_retained_command_envelope_mismatch_raises_protocol_error() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command="connect",
                            envelope="artifact",
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="invalid commands/run"):
        client.run_command(
            request=CommandRunRequest(
                command=ConnectCommandPayload(
                    kind="connect",
                    connection=ConnectionPayload(mode="adb", token="abc"),
                )
            )
        )


def test_retained_result_command_mismatch_raises_protocol_error() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command="screenshot",
                            envelope="artifact",
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonProtocolError, match="result command mismatch"):
        client.run_command(
            request=CommandRunRequest(
                command=ConnectCommandPayload(
                    kind="connect",
                    connection=ConnectionPayload(mode="adb", token="abc"),
                )
            )
        )


def test_close_runtime_returns_typed_retained_lifecycle_result() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": _retained_payload(
                            command="close",
                            envelope="lifecycle",
                        ),
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    result = client.close_runtime()

    assert isinstance(result, RetainedResultEnvelope)
    assert result.command == "close"
    assert result.envelope.value == "lifecycle"


def test_daemon_api_error_envelope_raises_daemon_api_error() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "error": {
                            "code": "DAEMON_BAD_REQUEST",
                            "message": "request body must be a JSON object",
                            "retryable": False,
                            "details": {},
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(DaemonApiError, match="request body must be a JSON object"):
        client.run_command(
            request=CommandRunRequest(
                command=WaitCommandPayload(
                    kind="wait",
                    predicate=IdlePredicatePayload(kind="idle"),
                )
            )
        )


def test_run_command_rejects_semantic_code_in_daemon_error_envelope() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "error": {
                            "code": "REF_STALE",
                            "message": (
                                "ref n3 is no longer valid on the current " "screen"
                            ),
                            "retryable": False,
                            "details": {},
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        DaemonProtocolError,
        match="invalid daemon error envelope",
    ):
        client.run_command(
            request=CommandRunRequest(command=ObserveCommandPayload(kind="observe"))
        )


def test_run_command_rejects_daemon_only_code_in_semantic_result_payload() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
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
                            "warnings": [],
                            "artifacts": {},
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        DaemonProtocolError,
        match="invalid commands/run response schema",
    ):
        client.run_command(
            request=CommandRunRequest(command=ObserveCommandPayload(kind="observe"))
        )


def test_malformed_daemon_error_envelope_raises_protocol_error() -> None:
    client = DaemonClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "error": {
                            "code": "DAEMON_BAD_REQUEST",
                            "message": "request body must be a JSON object",
                            "retryable": "no",
                            "details": [],
                        },
                    },
                )
            ),
            base_url="http://127.0.0.1:8765",
        ),
        owner_id="shell:self:1",
        token="secret",
    )

    with pytest.raises(
        DaemonProtocolError,
        match="invalid daemon error envelope",
    ):
        client.run_command(
            request=CommandRunRequest(command=ObserveCommandPayload(kind="observe"))
        )


def test_run_command_uses_wait_timeout_ms_for_read_timeout_budget() -> None:
    seen_timeouts: list[httpx.Timeout] = []

    class FakeHttpClient:
        def post(self, path, *, headers, json, timeout):  # noqa: ANN001
            del path, headers, json
            assert isinstance(timeout, httpx.Timeout)
            seen_timeouts.append(timeout)
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://127.0.0.1:8765/commands/run"),
                json={
                    "ok": True,
                    "result": {
                        "ok": True,
                        "command": "wait",
                        "category": "wait",
                        "payloadMode": "full",
                        "truth": {
                            "executionOutcome": "dispatched",
                            "continuityStatus": "none",
                            "observationQuality": "authoritative",
                        },
                        "screen": _public_screen_payload("screen-00002"),
                        "nextScreenId": "screen-00002",
                        "warnings": [],
                        "artifacts": {},
                    },
                },
            )

    client = DaemonClient(
        FakeHttpClient(),  # type: ignore[arg-type]
        owner_id="shell:self:1",
        token="secret",
    )

    client.run_command(
        request=CommandRunRequest(
            command=WaitCommandPayload(
                kind="wait",
                predicate=IdlePredicatePayload(kind="idle"),
                timeout_ms=12_000,
            )
        )
    )

    assert len(seen_timeouts) == 1
    assert seen_timeouts[0].read == 14.0
