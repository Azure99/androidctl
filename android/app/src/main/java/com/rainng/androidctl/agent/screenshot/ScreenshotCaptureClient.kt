package com.rainng.androidctl.agent.screenshot

import android.graphics.Bitmap

internal interface ScreenshotCaptureCallback {
    fun onSuccess(screenshot: CapturedScreenshot)

    fun onFailure(errorCode: Int)
}

internal interface ScreenshotCaptureClient {
    fun takeScreenshot(callback: ScreenshotCaptureCallback)
}

internal interface CapturedScreenshot {
    fun wrapHardwareBuffer(): Bitmap?

    fun close()
}
