package com.rainng.androidctl.agent.screenshot

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.RequestBudgets

internal class ScreenshotCapture internal constructor(
    service: AccessibilityService,
    private val processingRunner: ScreenshotTaskRunner,
    private val captureClient: ScreenshotCaptureClient = AccessibilityScreenshotCaptureClient(service),
    private val payloadEncoder: ScreenshotPayloadEncoder = ScreenshotPayloadEncoder(),
    private val captureTimeoutMs: Long = RequestBudgets.SCREENSHOT_CAPTURE_TIMEOUT_MS,
) {
    fun capture(request: ScreenshotRequest): ScreenshotResponse {
        val session = ScreenshotCaptureSession(captureTimeoutMs = captureTimeoutMs)
        requestScreenshot(session)
        val screenshot = session.awaitResult()
        return processingRunner.run(
            task = { payloadEncoder.encode(screenshot, request) },
            onRejected = screenshot::close,
            onCancelledBeforeStart = screenshot::close,
        )
    }

    @Suppress("TooGenericExceptionCaught")
    private fun requestScreenshot(session: ScreenshotCaptureSession) {
        // AccessibilityService.takeScreenshot may synchronously throw different RuntimeException
        // subclasses before the callback boundary; they are normalized into ScreenshotException.
        try {
            captureClient.takeScreenshot(session.callback)
        } catch (error: RuntimeException) {
            throw screenshotFailure(
                message = synchronousCaptureFailureMessage(error),
                retryable = true,
                cause = error,
            )
        }
    }
}
