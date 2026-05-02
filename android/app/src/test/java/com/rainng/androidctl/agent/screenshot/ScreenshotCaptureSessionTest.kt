package com.rainng.androidctl.agent.screenshot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ScreenshotCaptureSessionTest {
    @Test
    fun awaitResultTimesOutWhenCallbackNeverArrives() {
        val session = ScreenshotCaptureSession(captureTimeoutMs = 0L)

        assertScreenshotException(
            expectedMessage = "screenshot capture timed out",
            expectedRetryable = true,
        ) {
            session.awaitResult()
        }
    }

    @Test
    fun lateSuccessClosesScreenshotAfterTimeout() {
        val session = ScreenshotCaptureSession(captureTimeoutMs = 0L)
        val screenshot = FakeCapturedScreenshot()

        assertScreenshotException(
            expectedMessage = "screenshot capture timed out",
            expectedRetryable = true,
        ) {
            session.awaitResult()
        }

        session.onSuccess(screenshot)

        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun interruptedAwaitRestoresInterruptFlagAndLateSuccessClosesScreenshot() {
        val session = ScreenshotCaptureSession(captureTimeoutMs = 1000L)
        val screenshot = FakeCapturedScreenshot()

        Thread.currentThread().interrupt()
        try {
            assertScreenshotException(
                expectedMessage = "screenshot capture interrupted",
                expectedRetryable = true,
            ) {
                session.awaitResult()
            }

            assertTrue(Thread.currentThread().isInterrupted)
        } finally {
            Thread.interrupted()
        }

        session.onSuccess(screenshot)

        assertEquals(1, screenshot.closeCount)
    }

    @Test
    fun interruptedAwaitClosesStoredScreenshotExactlyOnce() {
        val session = ScreenshotCaptureSession(captureTimeoutMs = 1000L)
        val screenshot = FakeCapturedScreenshot()
        session.onSuccess(screenshot)

        Thread.currentThread().interrupt()
        try {
            assertScreenshotException(
                expectedMessage = "screenshot capture interrupted",
                expectedRetryable = true,
            ) {
                session.awaitResult()
            }

            assertTrue(Thread.currentThread().isInterrupted)
        } finally {
            Thread.interrupted()
        }

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
}
