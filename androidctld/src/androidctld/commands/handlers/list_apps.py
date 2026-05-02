"""Thin list-apps command handler backed by wrapper-backed apps.list RPC."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn, Protocol

from androidctl_contracts.command_results import ListAppsResult
from androidctld.commands.command_models import ListAppsCommand
from androidctld.device.bootstrap import DeviceBootstrapper
from androidctld.device.rpc import DeviceRpcClient
from androidctld.device.types import DeviceEndpoint
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.runtime import RuntimeKernel, RuntimeLifecycleLease
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime_policy import DEVICE_RPC_REQUEST_ID_LIST_APPS

ANDROID_APPS_LIST_METHOD = "apps.list"


class ListAppsRpcClient(Protocol):
    def call_result_payload(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: str,
    ) -> object: ...


ListAppsRpcClientFactory = Callable[[DeviceEndpoint, str], ListAppsRpcClient]


class ListAppsCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        bootstrapper: DeviceBootstrapper,
        rpc_client_factory: ListAppsRpcClientFactory | None = None,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._bootstrapper = bootstrapper
        self._rpc_client_factory = rpc_client_factory or DeviceRpcClient

    def handle(
        self,
        *,
        command: ListAppsCommand,
    ) -> dict[str, object]:
        del command
        runtime = self._runtime_kernel.ensure_runtime()
        query_lane_acquired = False
        try:
            lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
            self._runtime_kernel.acquire_query_lane(runtime)
            query_lane_acquired = True
            client = self._client_without_readiness(
                runtime,
                lifecycle_lease=lifecycle_lease,
            )
            payload = client.call_result_payload(
                ANDROID_APPS_LIST_METHOD,
                {},
                request_id=DEVICE_RPC_REQUEST_ID_LIST_APPS,
            )
            result = build_list_apps_result(payload)
            _raise_runtime_not_connected_if_stale(runtime, lifecycle_lease)
            return result.model_dump(by_alias=True, mode="json")
        finally:
            if query_lane_acquired:
                self._runtime_kernel.release_progress_lane(runtime)

    def _client_without_readiness(
        self,
        runtime: WorkspaceRuntime,
        *,
        lifecycle_lease: RuntimeLifecycleLease,
    ) -> ListAppsRpcClient:
        with runtime.lock:
            if not lifecycle_lease.is_current(runtime):
                raise _runtime_not_connected_error(runtime)
            if runtime.connection is None:
                raise _runtime_not_connected_error(runtime)
            token = _require_device_token(runtime)
            if runtime.transport is not None:
                endpoint = runtime.transport.endpoint
                return self._rpc_client_factory(endpoint, token)

        rebuilt_transport = self._runtime_kernel.rebootstrap_transport(
            runtime,
            bootstrap=self._bootstrapper.bootstrap_rpc_only,
            lease=lifecycle_lease,
        )

        with runtime.lock:
            if not lifecycle_lease.is_current(runtime):
                raise _runtime_not_connected_error(runtime)
            transport = runtime.transport or rebuilt_transport
            token = _require_device_token(runtime)
            return self._rpc_client_factory(
                transport.endpoint,
                token,
            )


def build_list_apps_result(payload: object) -> ListAppsResult:
    if not isinstance(payload, dict):
        _raise_malformed_payload("result")
    raw_apps = payload.get("apps", _MISSING)
    if not isinstance(raw_apps, list):
        _raise_malformed_payload("result.apps")

    apps: list[dict[str, str]] = []
    for index, raw_app in enumerate(raw_apps):
        field_prefix = f"result.apps[{index}]"
        if not isinstance(raw_app, dict):
            _raise_malformed_payload(field_prefix)
        package_name = _required_non_empty_string(
            raw_app.get("packageName", _MISSING),
            field=f"{field_prefix}.packageName",
        )
        app_label = _required_non_empty_string(
            raw_app.get("appLabel", _MISSING),
            field=f"{field_prefix}.appLabel",
        )
        if raw_app.get("launchable", _MISSING) is not True:
            _raise_malformed_payload(f"{field_prefix}.launchable")
        apps.append({"packageName": package_name, "appLabel": app_label})

    try:
        return ListAppsResult.model_validate(
            {"ok": True, "command": "list-apps", "apps": apps},
            strict=True,
        )
    except ValueError as error:
        raise _malformed_payload_error("result") from error


def _required_non_empty_string(value: object, *, field: str) -> str:
    if type(value) is not str:
        _raise_malformed_payload(field)
    normalized = value.strip()
    if not normalized:
        _raise_malformed_payload(field)
    return normalized


def _raise_runtime_not_connected_if_stale(
    runtime: WorkspaceRuntime,
    lifecycle_lease: RuntimeLifecycleLease,
) -> None:
    with runtime.lock:
        if lifecycle_lease.is_current(runtime):
            return
    raise _runtime_not_connected_error(
        runtime,
        details={"reason": "runtime_lifecycle_changed"},
    )


def _require_device_token(runtime: WorkspaceRuntime) -> str:
    token = runtime.device_token
    if not token:
        raise _runtime_not_connected_error(runtime)
    return token


def _runtime_not_connected_error(
    runtime: WorkspaceRuntime,
    *,
    details: dict[str, object] | None = None,
) -> DaemonError:
    error_details: dict[str, object] = {
        "workspaceRoot": runtime.workspace_root.as_posix()
    }
    if details is not None:
        error_details.update(details)
    return DaemonError(
        code=DaemonErrorCode.RUNTIME_NOT_CONNECTED,
        message="runtime is not connected to a device",
        retryable=False,
        details=error_details,
        http_status=200,
    )


def _raise_malformed_payload(field: str) -> NoReturn:
    raise _malformed_payload_error(field)


def _malformed_payload_error(field: str) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.DEVICE_RPC_FAILED,
        message="apps.list returned malformed payload",
        retryable=False,
        details={"field": field, "reason": "invalid_payload"},
        http_status=200,
    )


class _Missing:
    pass


_MISSING = _Missing()


__all__ = [
    "ANDROID_APPS_LIST_METHOD",
    "ListAppsCommandHandler",
    "ListAppsRpcClient",
    "ListAppsRpcClientFactory",
    "build_list_apps_result",
]
