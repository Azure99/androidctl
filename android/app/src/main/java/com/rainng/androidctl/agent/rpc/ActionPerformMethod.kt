package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.actions.ActionRequest
import com.rainng.androidctl.agent.actions.ActionRequestCodec
import com.rainng.androidctl.agent.actions.ActionResult
import com.rainng.androidctl.agent.actions.ActionResultCodec
import com.rainng.androidctl.agent.errors.RpcErrorCode

internal class ActionPerformMethod(
    private val actionExecutionFactory: (ActionRequest) -> () -> ActionResult,
) : DeviceRpcMethod {
    override val name: String = "action.perform"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            requiresReadyRuntime = true,
            requiresAccessibilityHandle = true,
            timeoutError = RpcErrorCode.ACTION_TIMEOUT,
            timeoutMessage = "action.perform timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
        val decoded = PreparedRpcMethodSupport.decodeRequest(request, ActionRequestCodec)
        val execute = actionExecutionFactory(decoded)
        return PreparedRpcMethodSupport.prepareUnit(
            timeoutMs = decoded.timeoutMs + RequestBudgets.ACTION_TIMEOUT_GRACE_MS,
            execute = execute,
            encoder = ActionResultCodec,
        )
    }
}
