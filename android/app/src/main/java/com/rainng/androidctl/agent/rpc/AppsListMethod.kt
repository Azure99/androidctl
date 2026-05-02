package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.device.AppsListResponse
import com.rainng.androidctl.agent.device.AppsListResponseCodec
import com.rainng.androidctl.agent.errors.RpcErrorCode

internal class AppsListMethod(
    private val appsListProvider: () -> AppsListResponse,
) : DeviceRpcMethod {
    override val name: String = "apps.list"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            timeoutError = RpcErrorCode.INTERNAL_ERROR,
            timeoutMessage = "apps.list timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
        PreparedRpcMethodSupport.prepareUnit(
            timeoutMs = RequestBudgets.APPS_LIST_METHOD_TIMEOUT_MS,
            execute = appsListProvider,
            encoder = AppsListResponseCodec,
        )
}
