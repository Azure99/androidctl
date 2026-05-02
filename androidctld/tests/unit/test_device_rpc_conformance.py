from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from androidctld.device.action_models import (
    CoordinatesTarget,
    DeviceActionRequest,
    DeviceActionTarget,
    GlobalActionRequest,
    HandleTarget,
    LaunchAppActionRequest,
    LongTapActionRequest,
    NodeActionRequest,
    NoneTarget,
    OpenUrlActionRequest,
    ScrollActionRequest,
    SwipeActionRequest,
    TapActionRequest,
    TypeActionRequest,
    required_action_kind_for_request,
)
from androidctld.device.action_serialization import (
    dump_device_action_request,
    dump_device_action_target,
)
from androidctld.protocol import DeviceRpcErrorCode, DeviceRpcMethod
from androidctld.refs.models import NodeHandle

FIXTURE_NAME = "contracts/tests/fixtures/android_rpc_raw_boundary_tokens.json"


def _repo_fixture_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / FIXTURE_NAME
        if candidate.is_file():
            return candidate
    raise AssertionError(f"could not locate {FIXTURE_NAME}")


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(_repo_fixture_path().read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _tokens(key: str) -> list[str]:
    value = _load_fixture()[key]
    assert isinstance(value, list)
    assert all(isinstance(token, str) for token in value)
    return value


def test_daemon_typed_device_rpc_methods_match_raw_boundary_fixture() -> None:
    daemon_typed_methods = [method.value for method in DeviceRpcMethod]
    daemon_wrapper_backed_methods = _tokens("daemonWrapperBackedMethods")

    assert daemon_typed_methods == _tokens("daemonTypedMethods")
    assert set(daemon_typed_methods) < set(_tokens("hostRawCallableMethods"))
    assert set(daemon_wrapper_backed_methods) < set(_tokens("hostRawCallableMethods"))
    assert set(daemon_typed_methods).isdisjoint(daemon_wrapper_backed_methods)
    assert set(daemon_typed_methods) | set(daemon_wrapper_backed_methods) == set(
        _tokens("hostRawCallableMethods")
    )
    assert daemon_wrapper_backed_methods == ["apps.list"]
    assert "apps.list" not in daemon_typed_methods


def test_daemon_device_rpc_error_codes_are_android_raw_boundary_subset() -> None:
    android_raw_boundary_codes = set(_tokens("androidRpcErrorCodes"))
    daemon_codes = [code.value for code in DeviceRpcErrorCode]

    assert set(daemon_codes) < android_raw_boundary_codes
    assert daemon_codes == [
        "STALE_TARGET",
        "TARGET_NOT_ACTIONABLE",
        "ACTION_FAILED",
        "ACTION_TIMEOUT",
        "RUNTIME_NOT_READY",
        "ACCESSIBILITY_DISABLED",
    ]


def test_action_not_confirmed_is_not_android_rpc_error_code() -> None:
    assert "ACTION_NOT_CONFIRMED" not in {code.value for code in DeviceRpcErrorCode}
    assert "ACTION_NOT_CONFIRMED" not in set(_tokens("androidRpcErrorCodes"))


def test_host_action_top_level_kinds_match_raw_boundary_fixture() -> None:
    handle = HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.1"))
    none = NoneTarget()
    requests: list[DeviceActionRequest] = [
        TapActionRequest(target=handle, timeout_ms=5000),
        LongTapActionRequest(target=CoordinatesTarget(x=10, y=20), timeout_ms=5000),
        TypeActionRequest(target=handle, text="hello", timeout_ms=5000),
        NodeActionRequest(target=handle, action="focus", timeout_ms=5000),
        ScrollActionRequest(target=handle, direction="down", timeout_ms=5000),
        GlobalActionRequest(target=none, action="back", timeout_ms=5000),
        SwipeActionRequest(target=none, direction="down", timeout_ms=5000),
        LaunchAppActionRequest(
            target=none,
            package_name="com.android.settings",
            timeout_ms=5000,
        ),
        OpenUrlActionRequest(
            target=none,
            url="https://example.com",
            timeout_ms=5000,
        ),
    ]

    expected_action_kinds = _tokens("actionKinds")

    assert [
        required_action_kind_for_request(request) for request in requests
    ] == expected_action_kinds
    assert [
        dump_device_action_request(request)["kind"] for request in requests
    ] == expected_action_kinds


def test_host_action_target_kinds_match_raw_boundary_fixture() -> None:
    targets: list[DeviceActionTarget] = [
        HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.1")),
        CoordinatesTarget(x=10, y=20),
        NoneTarget(),
    ]

    assert [dump_device_action_target(target)["kind"] for target in targets] == _tokens(
        "targetKinds"
    )


def test_host_action_nested_tokens_match_raw_boundary_fixture() -> None:
    handle = HandleTarget(handle=NodeHandle(snapshot_id=42, rid="w1:0.1"))
    none = NoneTarget()

    assert [
        dump_device_action_request(
            NodeActionRequest(target=handle, action=action, timeout_ms=5000)
        )["node"]["action"]
        for action in _tokens("nodeActions")
    ] == _tokens("nodeActions")
    assert [
        dump_device_action_request(
            GlobalActionRequest(target=none, action=action, timeout_ms=5000)
        )["global"]["action"]
        for action in _tokens("globalActions")
    ] == _tokens("globalActions")
    assert [
        dump_device_action_request(
            ScrollActionRequest(target=handle, direction=direction, timeout_ms=5000)
        )["scroll"]["direction"]
        for direction in _tokens("scrollDirections")
    ] == _tokens("scrollDirections")
    assert [
        dump_device_action_request(
            SwipeActionRequest(target=none, direction=direction, timeout_ms=5000)
        )["gesture"]["direction"]
        for direction in _tokens("gestureDirections")
    ] == _tokens("gestureDirections")
