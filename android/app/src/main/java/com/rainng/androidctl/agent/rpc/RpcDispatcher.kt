package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.runtime.RuntimeAccess

internal class RpcDispatcher(
    runtimeAccess: RuntimeAccess? = null,
    runtimeAccessProvider: (() -> RuntimeAccess)? = null,
    private val methodCatalog: RpcMethodCatalog,
    private val executionRunner: RpcExecutionRunner,
) {
    private val resolvedRuntimeAccessProvider: () -> RuntimeAccess =
        runtimeAccessProvider ?: { requireNotNull(runtimeAccess) }

    fun dispatch(request: RpcRequestEnvelope): String {
        val method = methodCatalog.find(request.method) ?: return unknownMethodResponse(request)
        if (method.policy.requiresReadyRuntime) {
            val blockingResponse = readyRuntimeBlockingResponse(request.id, method.policy)
            if (blockingResponse != null) {
                return blockingResponse
            }
        }

        return executionRunner.runPrepared(
            id = request.id,
            prepare = { method.prepare(request) },
            timeoutError = executionRunner.timeoutError(method.policy.timeoutError, method.policy.timeoutMessage),
        )
    }

    private fun readyRuntimeBlockingResponse(
        id: String?,
        policy: RpcMethodPolicy,
    ): String? {
        val runtimeAccess = resolvedRuntimeAccessProvider()
        val attachmentHandle = runtimeAccess.currentAccessibilityAttachmentHandle()
        val blockingError =
            runtimeAccess.readiness().blockingError()
                ?: if (
                    policy.requiresAccessibilityHandle &&
                    (attachmentHandle.revoked || attachmentHandle.service == null)
                ) {
                    RpcErrorCode.RUNTIME_NOT_READY
                } else {
                    null
                }
                ?: return null
        return RpcEnvelope.error(
            id = id,
            code = blockingError,
            message =
                when (blockingError) {
                    RpcErrorCode.ACCESSIBILITY_DISABLED -> "accessibility service is not enabled"
                    RpcErrorCode.RUNTIME_NOT_READY -> "accessibility runtime is not connected yet"
                    else -> "runtime is not ready"
                },
            retryable = true,
        )
    }

    private fun unknownMethodResponse(request: RpcRequestEnvelope): String =
        RpcEnvelope.error(
            id = request.id,
            code = RpcErrorCode.INVALID_REQUEST,
            message = "unknown method '${request.method}'",
            retryable = false,
        )
}
