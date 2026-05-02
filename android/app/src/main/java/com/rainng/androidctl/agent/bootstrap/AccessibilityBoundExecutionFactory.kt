package com.rainng.androidctl.agent.bootstrap

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.runtime.AccessibilityAttachmentHandleSnapshot

internal class AccessibilityBoundExecutionFactory(
    private val environment: RpcEnvironment,
) {
    fun <T> bind(block: (AccessibilityService) -> T): () -> T {
        val boundHandle = requireAccessibilityAttachment()
        return {
            val service = requireBoundAccessibilityService(boundHandle)
            block(service)
        }
    }

    private fun requireAccessibilityAttachment(): AccessibilityAttachmentHandleSnapshot =
        environment.runtimeAccess
            .currentAccessibilityAttachmentHandle()
            .takeIf { handle -> !handle.revoked && handle.service != null }
            ?: throw runtimeNotReady("accessibility runtime is not connected yet")

    private fun requireBoundAccessibilityService(boundHandle: AccessibilityAttachmentHandleSnapshot): AccessibilityService {
        val currentHandle = environment.runtimeAccess.currentAccessibilityAttachmentHandle()
        val boundService = boundHandle.service ?: throw runtimeNotReady("accessibility runtime is not connected yet")
        if (
            currentHandle.revoked ||
            currentHandle.generation != boundHandle.generation ||
            currentHandle.service !== boundService
        ) {
            throw runtimeNotReady("accessibility runtime changed before execution")
        }
        return boundService
    }

    private fun runtimeNotReady(message: String): DeviceRpcException =
        DeviceRpcException(
            code = RpcErrorCode.RUNTIME_NOT_READY,
            message = message,
            retryable = true,
        )
}
