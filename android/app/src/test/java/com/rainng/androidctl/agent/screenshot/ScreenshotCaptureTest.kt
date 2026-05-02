package com.rainng.androidctl.agent.screenshot

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import com.rainng.androidctl.agent.RequestBudgets
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.TimeUnit

class ScreenshotCaptureTest {
    @Test
    fun screenshotBudgetConstantsMatchPhaseOneLimits() {
        assertEquals(64L * 1024L * 1024L, RequestBudgets.MAX_SCREENSHOT_BITMAP_BYTES)
        assertEquals(32L * 1024L * 1024L, RequestBudgets.MAX_SCREENSHOT_ENCODED_BYTES)
        assertEquals(
            ((RequestBudgets.MAX_SCREENSHOT_ENCODED_BYTES + 2L) / 3L) * 4L,
            RequestBudgets.MAX_SCREENSHOT_BASE64_CHARS,
        )
    }

    @Test
    fun screenshotMethodTimeoutEqualsCapturePlusProcessPlusGraceBudgets() {
        assertEquals(
            RequestBudgets.SCREENSHOT_CAPTURE_TIMEOUT_MS +
                RequestBudgets.SCREENSHOT_PROCESS_TIMEOUT_MS +
                RequestBudgets.SCREENSHOT_TIMEOUT_GRACE_MS,
            RequestBudgets.SCREENSHOT_METHOD_TIMEOUT_MS,
        )
    }

    @Test
    fun captureTimesOutWhenCallbackNeverArrives() {
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { },
                captureTimeoutMs = 0L,
            )

        assertScreenshotException(
            expectedMessage = "screenshot capture timed out",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }
    }

    @Test
    fun captureNormalizesSynchronousFailuresBeforeCallback() {
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { throw IllegalStateException("boom") },
            )

        assertScreenshotException(
            expectedMessage = "screenshot capture failed before callback: boom",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }
    }

    @Test
    fun captureMapsKnownFailureCodesToMessages() {
        val expectations =
            listOf(
                AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR to "screenshot failed with internal error",
                AccessibilityService.ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS to
                    "screenshot requires accessibility screenshot capability",
                AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT to
                    "screenshot requests are throttled by the system",
                AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY to "screenshot requested an invalid display",
                AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_WINDOW to "screenshot requested an invalid window",
                999 to "screenshot failed with error code 999",
            )

        expectations.forEach { (errorCode, message) ->
            val capture =
                newScreenshotCapture(
                    captureClient = FakeCaptureClient { callback -> callback.onFailure(errorCode) },
                )

            assertScreenshotException(
                expectedMessage = message,
                expectedRetryable = true,
            ) {
                capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
            }
        }
    }

    @Test
    fun captureSurfacesInterruptedCaptureAndRestoresThreadFlag() {
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { },
            )

        Thread.currentThread().interrupt()
        try {
            assertScreenshotException(
                expectedMessage = "screenshot capture interrupted",
                expectedRetryable = true,
            ) {
                capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
            }
            assertTrue(Thread.currentThread().isInterrupted)
        } finally {
            Thread.interrupted()
        }
    }

    @Test
    fun rejectedProcessingSubmissionClosesScreenshotExactlyOnce() {
        val screenshot = FakeCapturedScreenshot()
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                processingRunner =
                    ScreenshotTaskRunner(
                        executor = RejectingExecutorService(),
                        timeoutMs = 1000L,
                    ),
            )

        assertScreenshotException(
            expectedMessage = "screenshot processing is busy",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }

        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureFailsWhenWrappedBitmapCannotBeCreated() {
        val screenshot = FakeCapturedScreenshot()
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = null,
                softwareBitmap = null,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        assertScreenshotException(
            expectedMessage = "failed to wrap screenshot hardware buffer",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }

        assertEquals(1, screenshot.closeCount)
        assertTrue(adapter.recycledBitmaps.isEmpty())
    }

    @Test
    fun captureFailsWhenSoftwareCopyCannotBeCreated() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 50)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = null,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        assertScreenshotException(
            expectedMessage = "failed to copy screenshot bitmap",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }

        assertEquals(listOf(wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureKeepsOriginalDimensionsForPngWhenScaleIsOne() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 50)
        val software = bitmap(width = 100, height = 50)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        val payload: ScreenshotResponse = capture.capture(ScreenshotRequest(format = "png", scale = 1.0))

        assertEquals("image/png", payload.contentType)
        assertEquals(100, payload.widthPx)
        assertEquals(50, payload.heightPx)
        assertEquals(Bitmap.CompressFormat.PNG, adapter.lastCompressFormat)
        assertEquals(100, adapter.lastCompressQuality)
        assertTrue(adapter.scaleRequests.isEmpty())
        assertEquals(listOf(software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
        assertFalse(payload.bodyBase64.isBlank())
    }

    @Test
    fun captureRejectsOutputBitmapBudgetBeforeScaling() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
                maxBitmapBytes = 100L * 40L * 4L,
            )

        assertScreenshotException(
            expectedMessage = "screenshot output exceeds size budget",
            expectedRetryable = false,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 2.0))
        }

        assertTrue(adapter.scaleRequests.isEmpty())
        assertEquals(0, adapter.compressRequests)
        assertEquals(listOf(software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureRejectsActualScaledBitmapBudgetBeforeCompressing() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val scaled = bitmap(width = 300, height = 160)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = scaled,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
                maxBitmapBytes = 200L * 80L * 4L,
            )

        assertScreenshotException(
            expectedMessage = "screenshot output exceeds size budget",
            expectedRetryable = false,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 2.0))
        }

        assertEquals(listOf(200 to 80), adapter.scaleRequests)
        assertEquals(0, adapter.compressRequests)
        assertEquals(listOf(scaled, software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureScalesBitmapAndReturnsJpegMetadata() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val scaled = bitmap(width = 150, height = 60)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = scaled,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        val payload: ScreenshotResponse = capture.capture(ScreenshotRequest(format = "jpeg", scale = 1.5))

        assertEquals("image/jpeg", payload.contentType)
        assertEquals(150, payload.widthPx)
        assertEquals(60, payload.heightPx)
        assertEquals(listOf(150 to 60), adapter.scaleRequests)
        assertEquals(Bitmap.CompressFormat.JPEG, adapter.lastCompressFormat)
        assertEquals(90, adapter.lastCompressQuality)
        assertEquals(listOf(scaled, software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureRejectsUnsupportedFormatsWithoutRetrying() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        assertScreenshotException(
            expectedMessage = "unsupported screenshot format 'gif'",
            expectedRetryable = false,
        ) {
            capture.capture(ScreenshotRequest(format = "gif", scale = 1.0))
        }

        assertEquals(listOf(software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureRejectsEncodedPayloadBudgetDuringCompression() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = null,
                compressedBytes = byteArrayOf(1, 2, 3),
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
                maxEncodedBytes = 2L,
            )

        assertScreenshotException(
            expectedMessage = "screenshot encoded payload exceeds size budget",
            expectedRetryable = false,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }

        assertEquals(Bitmap.CompressFormat.PNG, adapter.lastCompressFormat)
        assertEquals(100, adapter.lastCompressQuality)
        assertEquals(listOf(software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureRejectsSourceBitmapBudgetBeforeCopying() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = null,
                scaledBitmap = null,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
                maxBitmapBytes = (100L * 40L * 4L) - 1L,
            )

        assertScreenshotException(
            expectedMessage = "screenshot bitmap exceeds size budget",
            expectedRetryable = false,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 1.0))
        }

        assertEquals(0, adapter.copyRequests)
        assertEquals(0, adapter.compressRequests)
        assertEquals(listOf(wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun captureSurfacesCompressionFailureAndReleasesAllBitmaps() {
        val screenshot = FakeCapturedScreenshot()
        val wrapped = bitmap(width = 100, height = 40)
        val software = bitmap(width = 100, height = 40)
        val scaled = bitmap(width = 200, height = 80)
        val adapter =
            FakeBitmapAdapter(
                wrappedBitmap = wrapped,
                softwareBitmap = software,
                scaledBitmap = scaled,
                compressResult = false,
            )
        val capture =
            newScreenshotCapture(
                captureClient = FakeCaptureClient { callback -> callback.onSuccess(screenshot) },
                bitmapAdapter = adapter,
            )

        assertScreenshotException(
            expectedMessage = "failed to compress screenshot",
            expectedRetryable = true,
        ) {
            capture.capture(ScreenshotRequest(format = "png", scale = 2.0))
        }

        assertEquals(listOf(200 to 80), adapter.scaleRequests)
        assertEquals(listOf(scaled, software, wrapped), adapter.recycledBitmaps)
        assertEquals(1, screenshot.closeCount)
    }

    private fun assertScreenshotException(
        expectedMessage: String,
        expectedRetryable: Boolean,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals(expectedMessage, error.message)
            assertEquals(expectedRetryable, error.retryable)
        }
    }

    private class RejectingExecutorService : AbstractExecutorService() {
        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable): Unit = throw java.util.concurrent.RejectedExecutionException("busy")
    }
}
