package com.rainng.androidctl.agent.screenshot

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader

internal object ScreenshotRequestCodec : JsonDecoder<ScreenshotRequest> {
    override fun read(reader: JsonReader): ScreenshotRequest {
        val formatValue =
            reader.optionalString(
                key = "format",
                invalidMessage = "screenshot.capture format must be a string",
            ) ?: "png"
        val scale =
            reader.optionalDouble(
                key = "scale",
                invalidMessage = "screenshot.capture scale must be a number",
            ) ?: 1.0

        val format =
            when (formatValue.lowercase()) {
                "png" -> "png"
                "jpeg" -> "jpeg"
                else -> throw RequestValidationException("screenshot.capture requires format png or jpeg")
            }

        if (scale <= 0.0) {
            throw RequestValidationException("screenshot.capture requires scale > 0")
        }
        if (scale > RequestBudgets.MAX_SCREENSHOT_SCALE) {
            throw RequestValidationException("screenshot.capture requires scale <= ${RequestBudgets.MAX_SCREENSHOT_SCALE}")
        }

        return ScreenshotRequest(
            format = format,
            scale = scale,
        )
    }
}
