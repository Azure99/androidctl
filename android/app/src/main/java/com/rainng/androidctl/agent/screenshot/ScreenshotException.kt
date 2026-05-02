package com.rainng.androidctl.agent.screenshot

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode

class ScreenshotException(
    message: String,
    retryable: Boolean,
) : DeviceRpcException(RpcErrorCode.SCREENSHOT_UNAVAILABLE, message, retryable)

internal fun screenshotFailure(
    message: String,
    retryable: Boolean,
    cause: Throwable? = null,
): ScreenshotException =
    ScreenshotException(message = message, retryable = retryable).apply {
        cause?.let(::initCause)
    }

internal fun screenshotFailureMessage(errorCode: Int): String =
    when (errorCode) {
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "screenshot failed with internal error"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "screenshot requires accessibility screenshot capability"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "screenshot requests are throttled by the system"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "screenshot requested an invalid display"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_WINDOW -> "screenshot requested an invalid window"
        else -> "screenshot failed with error code $errorCode"
    }

internal fun synchronousCaptureFailureMessage(error: RuntimeException): String =
    error.message?.takeIf(String::isNotBlank)?.let { message ->
        "screenshot capture failed before callback: $message"
    } ?: "screenshot capture failed before callback"
