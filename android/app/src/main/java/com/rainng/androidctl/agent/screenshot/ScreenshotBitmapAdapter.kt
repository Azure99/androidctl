package com.rainng.androidctl.agent.screenshot

import android.graphics.Bitmap
import androidx.core.graphics.scale
import java.io.OutputStream

internal interface ScreenshotBitmapAdapter {
    fun wrapScreenshot(screenshot: CapturedScreenshot): Bitmap?

    fun copyToSoftwareBitmap(bitmap: Bitmap): Bitmap?

    fun scaleBitmap(
        bitmap: Bitmap,
        width: Int,
        height: Int,
    ): Bitmap

    fun compress(
        bitmap: Bitmap,
        format: Bitmap.CompressFormat,
        quality: Int,
        stream: OutputStream,
    ): Boolean

    fun recycle(bitmap: Bitmap)
}

internal object DefaultScreenshotBitmapAdapter : ScreenshotBitmapAdapter {
    override fun wrapScreenshot(screenshot: CapturedScreenshot): Bitmap? = screenshot.wrapHardwareBuffer()

    override fun copyToSoftwareBitmap(bitmap: Bitmap): Bitmap? = bitmap.copy(Bitmap.Config.ARGB_8888, false)

    override fun scaleBitmap(
        bitmap: Bitmap,
        width: Int,
        height: Int,
    ): Bitmap = bitmap.scale(width, height, true)

    override fun compress(
        bitmap: Bitmap,
        format: Bitmap.CompressFormat,
        quality: Int,
        stream: OutputStream,
    ): Boolean = bitmap.compress(format, quality, stream)

    override fun recycle(bitmap: Bitmap) {
        bitmap.recycle()
    }
}
