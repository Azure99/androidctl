package com.rainng.androidctl.agent.screenshot

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.io.OutputStream
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.TimeUnit

internal fun newScreenshotCapture(
    captureClient: ScreenshotCaptureClient,
    processingRunner: ScreenshotTaskRunner =
        ScreenshotTaskRunner(
            executor = DirectExecutorService(),
            timeoutMs = 1000L,
        ),
    bitmapAdapter: ScreenshotBitmapAdapter =
        FakeBitmapAdapter(
            wrappedBitmap = bitmap(width = 100, height = 100),
            softwareBitmap = bitmap(width = 100, height = 100),
            scaledBitmap = bitmap(width = 100, height = 100),
        ),
    captureTimeoutMs: Long = 1000L,
    maxBitmapBytes: Long = Long.MAX_VALUE,
    maxEncodedBytes: Long = Long.MAX_VALUE,
): ScreenshotCapture =
    ScreenshotCapture(
        service = mock(AccessibilityService::class.java),
        processingRunner = processingRunner,
        captureClient = captureClient,
        payloadEncoder =
            ScreenshotPayloadEncoder(
                bitmapAdapter = bitmapAdapter,
                maxBitmapBytes = maxBitmapBytes,
                maxEncodedBytes = maxEncodedBytes,
            ),
        captureTimeoutMs = captureTimeoutMs,
    )

internal fun bitmap(
    width: Int,
    height: Int,
): Bitmap {
    val bitmap = mock(Bitmap::class.java)
    `when`(bitmap.width).thenReturn(width)
    `when`(bitmap.height).thenReturn(height)
    return bitmap
}

internal class FakeCaptureClient(
    private val behavior: (ScreenshotCaptureCallback) -> Unit,
) : ScreenshotCaptureClient {
    override fun takeScreenshot(callback: ScreenshotCaptureCallback) {
        behavior(callback)
    }
}

internal class FakeCapturedScreenshot(
    private val wrappedBitmap: Bitmap? = null,
) : CapturedScreenshot {
    var closeCount: Int = 0

    override fun wrapHardwareBuffer(): Bitmap? = wrappedBitmap

    override fun close() {
        closeCount += 1
    }
}

internal class FakeBitmapAdapter(
    private val wrappedBitmap: Bitmap?,
    private val softwareBitmap: Bitmap?,
    private val scaledBitmap: Bitmap?,
    private val compressResult: Boolean = true,
    private val compressedBytes: ByteArray = byteArrayOf(1, 2, 3),
) : ScreenshotBitmapAdapter {
    val recycledBitmaps = mutableListOf<Bitmap>()
    val scaleRequests = mutableListOf<Pair<Int, Int>>()
    var lastCompressFormat: Bitmap.CompressFormat? = null
    var lastCompressQuality: Int? = null
    var copyRequests: Int = 0
    var compressRequests: Int = 0

    override fun wrapScreenshot(screenshot: CapturedScreenshot): Bitmap? = wrappedBitmap

    override fun copyToSoftwareBitmap(bitmap: Bitmap): Bitmap? {
        copyRequests += 1
        return softwareBitmap
    }

    override fun scaleBitmap(
        bitmap: Bitmap,
        width: Int,
        height: Int,
    ): Bitmap {
        scaleRequests += width to height
        return scaledBitmap ?: error("scaledBitmap not configured")
    }

    override fun compress(
        bitmap: Bitmap,
        format: Bitmap.CompressFormat,
        quality: Int,
        stream: OutputStream,
    ): Boolean {
        compressRequests += 1
        lastCompressFormat = format
        lastCompressQuality = quality
        if (compressResult) {
            stream.write(compressedBytes)
        }
        return compressResult
    }

    override fun recycle(bitmap: Bitmap) {
        recycledBitmaps += bitmap
    }
}

private class DirectExecutorService : AbstractExecutorService() {
    private var shutdown = false

    override fun shutdown() {
        shutdown = true
    }

    override fun shutdownNow(): MutableList<Runnable> {
        shutdown = true
        return mutableListOf()
    }

    override fun isShutdown(): Boolean = shutdown

    override fun isTerminated(): Boolean = shutdown

    override fun awaitTermination(
        timeout: Long,
        unit: TimeUnit,
    ): Boolean = shutdown

    override fun execute(command: Runnable) {
        command.run()
    }
}
