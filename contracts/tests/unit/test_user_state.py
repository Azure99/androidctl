from androidctl_contracts.user_state import ActiveDaemonRecord, DaemonInstanceIdentity


def _active_record_payload() -> dict[str, object]:
    return {
        "pid": 41200,
        "host": "127.0.0.1",
        "port": 17631,
        "token": "token",
        "startedAt": "2026-03-14T20:05:12Z",
        "workspaceRoot": "/repo",
        "ownerId": "owner-1",
    }


def test_active_record_round_trips_documented_fields() -> None:
    payload = _active_record_payload()

    record = ActiveDaemonRecord.model_validate(payload)

    assert record.model_dump() == payload


def test_active_record_identity_uses_pid_and_started_at() -> None:
    record = ActiveDaemonRecord.model_validate(_active_record_payload())

    assert record.identity == DaemonInstanceIdentity(
        pid=41200,
        started_at="2026-03-14T20:05:12Z",
    )


def test_active_record_accepts_snake_case_kwargs_and_dumps_wire_aliases() -> None:
    record = ActiveDaemonRecord(
        pid=41200,
        host="127.0.0.1",
        port=17631,
        token="token",
        started_at="2026-03-14T20:05:12Z",
        workspace_root="/repo",
        owner_id="owner-1",
    )

    assert record.identity == DaemonInstanceIdentity(
        pid=41200,
        started_at="2026-03-14T20:05:12Z",
    )
    assert record.model_dump() == _active_record_payload()
