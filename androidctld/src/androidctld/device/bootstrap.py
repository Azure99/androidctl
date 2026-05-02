"""Bootstrap and readiness probing for the Android device agent."""

from __future__ import annotations

from androidctld import __version__ as ANDROIDCTLD_VERSION
from androidctld.device.connectors import ConnectorHandle, DeviceConnectorFactory
from androidctld.device.errors import (
    DeviceBootstrapError,
    accessibility_not_ready,
    capability_mismatch,
    version_mismatch,
)
from androidctld.device.rpc import DeviceRpcClient
from androidctld.device.types import (
    BootstrapResult,
    ConnectionConfig,
    MetaInfo,
    RuntimeTransport,
)
from androidctld.errors import DaemonError
from androidctld.protocol import DeviceRpcErrorCode

MINIMUM_CONNECT_ACTION_KINDS = frozenset(
    {
        "tap",
        "type",
        "global",
        "launchApp",
    }
)


class DeviceBootstrapper:
    def __init__(self, connector_factory: DeviceConnectorFactory | None = None) -> None:
        self._connector_factory = connector_factory or DeviceConnectorFactory()

    def bootstrap(self, config: ConnectionConfig) -> BootstrapResult:
        handle = self.establish_transport(config)
        try:
            return self.bootstrap_runtime(handle, config)
        except Exception:
            handle.close()
            raise

    def bootstrap_rpc_only(self, config: ConnectionConfig) -> BootstrapResult:
        handle = self.establish_transport(config)
        try:
            return self.bootstrap_runtime_rpc_only(handle, config)
        except Exception:
            handle.close()
            raise

    def establish_transport(self, config: ConnectionConfig) -> ConnectorHandle:
        return self._connector_factory.connect(config)

    def bootstrap_runtime(
        self,
        handle: ConnectorHandle,
        config: ConnectionConfig,
    ) -> BootstrapResult:
        client = DeviceRpcClient(endpoint=handle.endpoint, token=config.token)
        meta = self._fetch_release_compatible_meta(client)
        self._validate_capabilities(meta)
        self._probe_readiness(client)
        return BootstrapResult(
            connection=handle.connection,
            transport=RuntimeTransport(
                endpoint=handle.endpoint,
                close=handle.close,
            ),
            meta=meta,
        )

    def bootstrap_runtime_rpc_only(
        self,
        handle: ConnectorHandle,
        config: ConnectionConfig,
    ) -> BootstrapResult:
        client = DeviceRpcClient(endpoint=handle.endpoint, token=config.token)
        meta = self._fetch_release_compatible_meta(client)
        return BootstrapResult(
            connection=handle.connection,
            transport=RuntimeTransport(
                endpoint=handle.endpoint,
                close=handle.close,
            ),
            meta=meta,
        )

    def _fetch_release_compatible_meta(self, client: DeviceRpcClient) -> MetaInfo:
        try:
            meta = client.meta_get()
        except DeviceBootstrapError as error:
            if _is_legacy_rpc_version_schema_failure(error):
                raise version_mismatch(
                    "device agent meta.get payload is incompatible with this "
                    "androidctld release; install matching androidctld and Android "
                    "agent/APK versions",
                    {"reason": "legacy_rpc_version_field"},
                ) from error
            raise
        if meta.version != ANDROIDCTLD_VERSION:
            raise version_mismatch(
                "device agent release version mismatch: "
                f"daemon={ANDROIDCTLD_VERSION} agent={meta.version}; "
                "install matching androidctld and Android agent/APK versions",
                {
                    "expectedReleaseVersion": ANDROIDCTLD_VERSION,
                    "actualReleaseVersion": meta.version,
                },
            )
        return meta

    def _validate_capabilities(self, meta: MetaInfo) -> None:
        missing_capabilities: list[str] = []
        if not meta.capabilities.supports_events_poll:
            missing_capabilities.append("supportsEventsPoll")
        missing_action_kinds = sorted(
            MINIMUM_CONNECT_ACTION_KINDS.difference(meta.capabilities.action_kinds)
        )
        if not missing_capabilities and not missing_action_kinds:
            return
        raise capability_mismatch(
            "device agent capability handshake failed",
            {
                "missingCapabilities": missing_capabilities,
                "missingActionKinds": missing_action_kinds,
            },
        )

    def _probe_readiness(self, client: DeviceRpcClient) -> None:
        try:
            client.snapshot_get()
        except DaemonError as error:
            device_code = error.details.get("deviceCode")
            if device_code in (
                DeviceRpcErrorCode.RUNTIME_NOT_READY.value,
                DeviceRpcErrorCode.ACCESSIBILITY_DISABLED.value,
            ):
                raise accessibility_not_ready(
                    "accessibility runtime is not ready",
                    {"deviceCode": device_code},
                ) from error
            raise


def _is_legacy_rpc_version_schema_failure(error: DeviceBootstrapError) -> bool:
    return (
        error.code == "DEVICE_RPC_FAILED"
        and error.details.get("field") == "result"
        and error.details.get("reason") == "invalid_payload"
        and error.details.get("unknownFields") == ["rpcVersion"]
    )
