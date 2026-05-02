package com.rainng.androidctl.agent.screenshot

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

internal class ScreenshotCaptureSession(
    private val captureTimeoutMs: Long,
) {
    private val latch = CountDownLatch(1)
    private val stateLock = Any()
    private var screenshotResult: CapturedScreenshot? = null
    private var failure: ScreenshotException? = null
    private var abandoned: Boolean = false

    val callback =
        object : ScreenshotCaptureCallback {
            override fun onSuccess(screenshot: CapturedScreenshot) {
                this@ScreenshotCaptureSession.onSuccess(screenshot)
            }

            override fun onFailure(errorCode: Int) {
                this@ScreenshotCaptureSession.onFailure(errorCode)
            }
        }

    fun onSuccess(screenshot: CapturedScreenshot) {
        val shouldClose =
            synchronized(stateLock) {
                if (abandoned) {
                    true
                } else {
                    screenshotResult = screenshot
                    latch.countDown()
                    false
                }
            }
        if (shouldClose) {
            screenshot.close()
        }
    }

    fun onFailure(errorCode: Int) {
        synchronized(stateLock) {
            if (abandoned) {
                return
            }
            failure =
                ScreenshotException(
                    message = screenshotFailureMessage(errorCode),
                    retryable = true,
                )
            latch.countDown()
        }
    }

    fun awaitResult(): CapturedScreenshot {
        val captureCompleted = awaitCaptureCompletion()

        if (!captureCompleted) {
            throwCaptureTimedOut()
        }

        return synchronized(stateLock) {
            failure?.let { throw it }
            val screenshot = screenshotResult
            screenshotResult = null
            screenshot
                ?: throw ScreenshotException(
                    message = "screenshot capture failed without result",
                    retryable = true,
                )
        }
    }

    private fun awaitCaptureCompletion(): Boolean =
        try {
            latch.await(captureTimeoutMs, TimeUnit.MILLISECONDS)
        } catch (error: InterruptedException) {
            throwCaptureInterrupted(error)
        }

    private fun abandonAndDetachScreenshot(): CapturedScreenshot? =
        synchronized(stateLock) {
            abandoned = true
            val screenshot = screenshotResult
            screenshotResult = null
            screenshot
        }

    private fun closeStoredScreenshot(screenshot: CapturedScreenshot?) {
        screenshot?.close()
    }

    private fun throwCaptureInterrupted(error: InterruptedException): Nothing {
        closeStoredScreenshot(abandonAndDetachScreenshot())
        Thread.currentThread().interrupt()
        throw screenshotFailure(
            message = "screenshot capture interrupted",
            retryable = true,
            cause = error,
        )
    }

    private fun throwCaptureTimedOut(): Nothing {
        closeStoredScreenshot(abandonAndDetachScreenshot())
        throw screenshotFailure(
            message = "screenshot capture timed out",
            retryable = true,
        )
    }
}
