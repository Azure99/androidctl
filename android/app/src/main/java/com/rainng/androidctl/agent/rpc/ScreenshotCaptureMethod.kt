package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.screenshot.ScreenshotRequest
import com.rainng.androidctl.agent.screenshot.ScreenshotRequestCodec
import com.rainng.androidctl.agent.screenshot.ScreenshotResponse
import com.rainng.androidctl.agent.screenshot.ScreenshotResponseCodec

internal class ScreenshotCaptureMethod(
    private val screenshotExecutionFactory: (ScreenshotRequest) -> () -> ScreenshotResponse,
) : DeviceRpcMethod {
    override val name: String = "screenshot.capture"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            requiresReadyRuntime = true,
            requiresAccessibilityHandle = true,
            timeoutError = RpcErrorCode.SCREENSHOT_UNAVAILABLE,
            timeoutMessage = "screenshot.capture timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
        val decoded = PreparedRpcMethodSupport.decodeRequest(request, ScreenshotRequestCodec)
        val execute = screenshotExecutionFactory(decoded)
        return PreparedRpcMethodSupport.prepareUnit(
            timeoutMs = RequestBudgets.SCREENSHOT_METHOD_TIMEOUT_MS,
            execute = execute,
            encoder = ScreenshotResponseCodec,
        )
    }
}
