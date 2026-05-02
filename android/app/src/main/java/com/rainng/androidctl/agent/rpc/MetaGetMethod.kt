package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.AgentConstants
import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.actions.ActionKind
import com.rainng.androidctl.agent.errors.RpcErrorCode

internal class MetaGetMethod(
    private val versionProvider: () -> String,
) : DeviceRpcMethod {
    override val name: String = "meta.get"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            timeoutError = RpcErrorCode.INTERNAL_ERROR,
            timeoutMessage = "meta.get timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
        PreparedRpcMethodSupport.prepareUnit(
            timeoutMs = RequestBudgets.META_METHOD_TIMEOUT_MS,
            execute = {
                MetaResponse(
                    service = AgentConstants.SERVICE_NAME,
                    version = versionProvider(),
                    capabilities =
                        MetaCapabilities(
                            supportsEventsPoll = true,
                            supportsScreenshot = true,
                            actionKinds = ActionKind.capabilityWireNames(),
                        ),
                )
            },
            encoder = MetaResponseCodec,
        )
}
