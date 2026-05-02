package com.rainng.androidctl.agent.screenshot

import android.graphics.Bitmap
import com.rainng.androidctl.agent.RequestBudgets
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.util.Base64
import kotlin.math.roundToInt

internal class ScreenshotPayloadEncoder(
    private val bitmapAdapter: ScreenshotBitmapAdapter = DefaultScreenshotBitmapAdapter,
    private val maxBitmapBytes: Long = RequestBudgets.MAX_SCREENSHOT_BITMAP_BYTES,
    private val maxEncodedBytes: Long = RequestBudgets.MAX_SCREENSHOT_ENCODED_BYTES,
) {
    fun encode(
        screenshot: CapturedScreenshot,
        request: ScreenshotRequest,
    ): ScreenshotResponse {
        var wrappedBitmap: Bitmap? = null
        var softwareBitmap: Bitmap? = null
        var outputBitmap: Bitmap? = null

        try {
            wrappedBitmap = wrapScreenshotBitmap(screenshot)
            validateBitmapBudget(
                width = wrappedBitmap.width,
                height = wrappedBitmap.height,
                message = "screenshot bitmap exceeds size budget",
            )

            softwareBitmap = copyToSoftwareBitmap(wrappedBitmap)
            validateBitmapBudget(
                width = softwareBitmap.width,
                height = softwareBitmap.height,
                message = "screenshot bitmap exceeds size budget",
            )

            val outputDimensions =
                scaledDimensions(
                    sourceWidth = softwareBitmap.width,
                    sourceHeight = softwareBitmap.height,
                    scale = request.scale,
                )
            validateBitmapBudget(
                width = outputDimensions.width,
                height = outputDimensions.height,
                message = "screenshot output exceeds size budget",
            )

            outputBitmap =
                if (request.scale == 1.0) {
                    softwareBitmap
                } else {
                    bitmapAdapter.scaleBitmap(softwareBitmap, outputDimensions.width, outputDimensions.height)
                }
            validateBitmapBudget(
                width = outputBitmap.width,
                height = outputBitmap.height,
                message = "screenshot output exceeds size budget",
            )

            val compressionSettings = compressionSettings(request.format)
            val encodedBody = compressBitmap(outputBitmap, compressionSettings)

            return ScreenshotResponse(
                contentType = compressionSettings.contentType,
                widthPx = outputBitmap.width,
                heightPx = outputBitmap.height,
                bodyBase64 = encodedBody,
            )
        } finally {
            if (outputBitmap != null && outputBitmap !== softwareBitmap) {
                bitmapAdapter.recycle(outputBitmap)
            }
            softwareBitmap?.let(bitmapAdapter::recycle)
            wrappedBitmap?.let(bitmapAdapter::recycle)
            screenshot.close()
        }
    }

    private fun wrapScreenshotBitmap(screenshot: CapturedScreenshot): Bitmap =
        bitmapAdapter.wrapScreenshot(screenshot)
            ?: throw screenshotFailure(
                message = "failed to wrap screenshot hardware buffer",
                retryable = true,
            )

    private fun copyToSoftwareBitmap(bitmap: Bitmap): Bitmap =
        bitmapAdapter.copyToSoftwareBitmap(bitmap)
            ?: throw screenshotFailure(
                message = "failed to copy screenshot bitmap",
                retryable = true,
            )

    private fun compressionSettings(format: String): ScreenshotCompressionSettings =
        when (format) {
            "png" ->
                ScreenshotCompressionSettings(
                    format = Bitmap.CompressFormat.PNG,
                    quality = PNG_COMPRESS_QUALITY,
                    contentType = "image/png",
                )

            "jpeg" ->
                ScreenshotCompressionSettings(
                    format = Bitmap.CompressFormat.JPEG,
                    quality = JPEG_COMPRESS_QUALITY,
                    contentType = "image/jpeg",
                )

            else ->
                throw screenshotFailure(
                    message = "unsupported screenshot format '$format'",
                    retryable = false,
                )
        }

    private fun compressBitmap(
        bitmap: Bitmap,
        compressionSettings: ScreenshotCompressionSettings,
    ): String {
        validateBitmapBudget(
            width = bitmap.width,
            height = bitmap.height,
            message = "screenshot output exceeds size budget",
        )

        val stream =
            BoundedByteArrayOutputStream(
                maxBytes = maxEncodedBytes,
                overflowMessage = "screenshot encoded payload exceeds size budget",
            )
        if (!bitmapAdapter.compress(bitmap, compressionSettings.format, compressionSettings.quality, stream)) {
            throw screenshotFailure(
                message = "failed to compress screenshot",
                retryable = true,
            )
        }
        return Base64.getEncoder().encodeToString(stream.toByteArray())
    }

    private fun scaledDimensions(
        sourceWidth: Int,
        sourceHeight: Int,
        scale: Double,
    ): ScreenshotDimensions =
        ScreenshotDimensions(
            width = scaledDimension(sourceWidth, scale),
            height = scaledDimension(sourceHeight, scale),
        )

    private fun scaledDimension(
        source: Int,
        scale: Double,
    ): Int {
        val scaled = source.toDouble() * scale
        if (!scaled.isFinite() || scaled > Int.MAX_VALUE.toDouble()) {
            throw screenshotFailure(
                message = "screenshot output exceeds size budget",
                retryable = false,
            )
        }
        return scaled.roundToInt().coerceAtLeast(1)
    }

    private fun validateBitmapBudget(
        width: Int,
        height: Int,
        message: String,
    ) {
        val bitmapBytes = bitmapByteCount(width, height, message)
        if (bitmapBytes > maxBitmapBytes) {
            throw screenshotFailure(
                message = message,
                retryable = false,
            )
        }
    }

    private fun bitmapByteCount(
        width: Int,
        height: Int,
        message: String,
    ): Long {
        if (width <= 0 || height <= 0) {
            throw screenshotFailure(
                message = message,
                retryable = false,
            )
        }

        val pixels = width.toLong() * height.toLong()
        if (pixels > Long.MAX_VALUE / BYTES_PER_ARGB_8888_PIXEL) {
            throw screenshotFailure(
                message = message,
                retryable = false,
            )
        }
        return pixels * BYTES_PER_ARGB_8888_PIXEL
    }
}

private data class ScreenshotDimensions(
    val width: Int,
    val height: Int,
)

private data class ScreenshotCompressionSettings(
    val format: Bitmap.CompressFormat,
    val quality: Int,
    val contentType: String,
)

private class BoundedByteArrayOutputStream(
    private val maxBytes: Long,
    private val overflowMessage: String,
) : OutputStream() {
    private val delegate = ByteArrayOutputStream()

    override fun write(value: Int) {
        checkCapacity(1)
        delegate.write(value)
    }

    override fun write(
        buffer: ByteArray,
        offset: Int,
        length: Int,
    ) {
        checkCapacity(length)
        delegate.write(buffer, offset, length)
    }

    fun toByteArray(): ByteArray = delegate.toByteArray()

    private fun checkCapacity(bytesToAdd: Int) {
        val requestedSize = delegate.size().toLong() + bytesToAdd.toLong()
        if (bytesToAdd < 0 || requestedSize > maxBytes) {
            throw screenshotFailure(
                message = overflowMessage,
                retryable = false,
            )
        }
    }
}

private const val PNG_COMPRESS_QUALITY = 100
private const val JPEG_COMPRESS_QUALITY = 90
private const val BYTES_PER_ARGB_8888_PIXEL = 4L
