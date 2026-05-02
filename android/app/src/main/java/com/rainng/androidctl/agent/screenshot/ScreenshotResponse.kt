package com.rainng.androidctl.agent.screenshot

internal data class ScreenshotResponse(
    val contentType: String,
    val widthPx: Int,
    val heightPx: Int,
    val bodyBase64: String,
)
