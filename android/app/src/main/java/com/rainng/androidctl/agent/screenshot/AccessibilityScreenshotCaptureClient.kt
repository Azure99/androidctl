package com.rainng.androidctl.agent.screenshot

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.view.Display

internal class AccessibilityScreenshotCaptureClient(
    private val service: AccessibilityService,
) : ScreenshotCaptureClient {
    override fun takeScreenshot(callback: ScreenshotCaptureCallback) {
        service.takeScreenshot(
            Display.DEFAULT_DISPLAY,
            service.mainExecutor,
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(screenshot: AccessibilityService.ScreenshotResult) {
                    callback.onSuccess(AccessibilityCapturedScreenshot(screenshot))
                }

                override fun onFailure(errorCode: Int) {
                    callback.onFailure(errorCode)
                }
            },
        )
    }
}

internal class AccessibilityCapturedScreenshot(
    private val screenshot: AccessibilityService.ScreenshotResult,
) : CapturedScreenshot {
    override fun wrapHardwareBuffer(): Bitmap? = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)

    override fun close() {
        screenshot.hardwareBuffer.close()
    }
}
