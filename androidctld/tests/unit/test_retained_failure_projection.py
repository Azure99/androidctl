from __future__ import annotations

from androidctld.commands.result_models import (
    build_projected_retained_failure_result,
    build_retained_failure_result,
    dump_retained_result_envelope,
)
from androidctld.errors import DaemonErrorCode


def test_low_level_retained_failure_builder_preserves_source_code() -> None:
    result = build_retained_failure_result(
        command="screenshot",
        code=DaemonErrorCode.ARTIFACT_WRITE_FAILED,
        message="artifact write failed",
        details={"path": "/repo/.androidctl/screenshots/shot-00001.png"},
    )

    assert result.code == "ARTIFACT_WRITE_FAILED"
    assert result.details == {"path": "/repo/.androidctl/screenshots/shot-00001.png"}


def test_projected_retained_failure_maps_artifact_source_code() -> None:
    for source_code in (
        DaemonErrorCode.ARTIFACT_ROOT_UNWRITABLE,
        DaemonErrorCode.ARTIFACT_WRITE_FAILED,
    ):
        result = build_projected_retained_failure_result(
            command="screenshot",
            code=source_code,
            message="artifact write failed",
            details={
                "reason": "permission-denied",
                "path": "/repo/.androidctl/screenshots/shot-00001.png",
                "token": "Bearer secret",
                "nested": {"rawRid": "rid-1"},
            },
            operation="screenshot",
        )

        assert result.code == "WORKSPACE_STATE_UNWRITABLE"
        assert result.details == {
            "sourceCode": source_code.value,
            "sourceKind": "workspace",
            "operation": "screenshot",
            "reason": "permission-denied",
        }


def test_projected_non_raw_retained_failure_keeps_safe_wrong_token_reason() -> None:
    result = build_projected_retained_failure_result(
        command="connect",
        code=DaemonErrorCode.DEVICE_AGENT_UNAUTHORIZED,
        message="device agent rejected token",
        details={"reason": "wrong-token"},
    )

    payload = dump_retained_result_envelope(result)

    assert payload["message"] == "device agent rejected token"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_UNAUTHORIZED",
        "sourceKind": "device",
        "reason": "wrong-token",
    }


def test_projected_connect_retained_failure_keeps_release_version_details() -> None:
    result = build_projected_retained_failure_result(
        command="connect",
        code=DaemonErrorCode.DEVICE_AGENT_VERSION_MISMATCH,
        message="device agent release version mismatch",
        details={
            "expectedReleaseVersion": "0.1.0",
            "actualReleaseVersion": "0.1.1",
            "token": "Bearer secret",
        },
    )

    payload = dump_retained_result_envelope(result)

    assert payload["code"] == "DEVICE_AGENT_VERSION_MISMATCH"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
        "expectedReleaseVersion": "0.1.0",
        "actualReleaseVersion": "0.1.1",
    }
    assert "Bearer" not in str(payload)


def test_projected_screenshot_failure_omits_noncanonical_release_versions() -> None:
    result = build_projected_retained_failure_result(
        command="screenshot",
        code=DaemonErrorCode.DEVICE_AGENT_VERSION_MISMATCH,
        message="device agent release version mismatch",
        details={
            "expectedReleaseVersion": "v0.1.0",
            "actualReleaseVersion": "0.1.1 ",
        },
    )

    payload = dump_retained_result_envelope(result)

    assert payload["code"] == "DEVICE_AGENT_VERSION_MISMATCH"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
    }


def test_projected_connect_failure_keeps_rpc_version_reason_only() -> None:
    result = build_projected_retained_failure_result(
        command="connect",
        code=DaemonErrorCode.DEVICE_AGENT_VERSION_MISMATCH,
        message="device agent meta.get payload is incompatible",
        details={
            "reason": "legacy_rpc_version_field",
            "unknownFields": ["rpcVersion"],
            "expectedRpcVersion": 1,
            "actualRpcVersion": 1,
        },
    )

    payload = dump_retained_result_envelope(result)

    assert payload["code"] == "DEVICE_AGENT_VERSION_MISMATCH"
    assert payload["details"] == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
        "reason": "legacy_rpc_version_field",
    }


def test_projected_connect_failure_keeps_generic_device_rpc_failed_code() -> None:
    result = build_projected_retained_failure_result(
        command="connect",
        code=DaemonErrorCode.DEVICE_RPC_FAILED,
        message="device RPC result has unsupported fields",
        details={
            "reason": "invalid_payload",
            "unknownFields": ["extraField", "rpcVersion"],
        },
    )

    payload = dump_retained_result_envelope(result)

    assert payload["code"] == "DEVICE_RPC_FAILED"
    assert payload["details"] == {"reason": "invalid_payload"}


def test_projected_retained_failure_omits_unsafe_reason_from_json() -> None:
    unsafe_reasons = (
        "emulator-5554",
        "Bearer secret",
        "/repo/.androidctl/screenshots/shot-00001.png",
        "https://example.test/shot.png",
        "rid-1",
        "snapshot-0001",
        "0123456789abcdef0123456789abcdef",
        "api-token",
    )

    for reason in unsafe_reasons:
        result = build_projected_retained_failure_result(
            command="screenshot",
            code=DaemonErrorCode.ARTIFACT_WRITE_FAILED,
            message="artifact write failed",
            details={"reason": reason},
        )

        payload = dump_retained_result_envelope(result)

        assert payload["code"] == "WORKSPACE_STATE_UNWRITABLE"
        assert payload["details"] == {
            "sourceCode": "ARTIFACT_WRITE_FAILED",
            "sourceKind": "workspace",
        }
        assert reason not in str(payload)
