package com.rainng.androidctl.agent.screenshot

import com.rainng.androidctl.agent.RequestBudgets
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean

internal class ScreenshotTaskRunner(
    private val executor: ExecutorService,
    private val timeoutMs: Long,
) {
    private val shutdownStarted = AtomicBoolean(false)
    private val forceShutdownStarted = AtomicBoolean(false)

    fun <T> run(
        task: () -> T,
        onRejected: (() -> Unit)? = null,
        onCancelledBeforeStart: (() -> Unit)? = null,
    ): T {
        val started = AtomicBoolean(false)
        val future =
            submit(
                task = {
                    started.set(true)
                    task()
                },
                onRejected = onRejected,
            )
        return try {
            future.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (error: TimeoutException) {
            throwProcessingTimeout(future, started, onCancelledBeforeStart, error)
        } catch (error: InterruptedException) {
            throwProcessingInterrupted(future, started, onCancelledBeforeStart, error)
        } catch (error: ExecutionException) {
            throwProcessingFailure(error)
        }
    }

    private fun <T> submit(
        task: () -> T,
        onRejected: (() -> Unit)?,
    ): Future<T> =
        try {
            executor.submit<T> { task() }
        } catch (error: RejectedExecutionException) {
            onRejected?.invoke()
            throw screenshotFailure(
                message = "screenshot processing is busy",
                retryable = true,
                cause = error,
            )
        }

    private fun <T> throwProcessingTimeout(
        future: Future<T>,
        started: AtomicBoolean,
        onCancelledBeforeStart: (() -> Unit)?,
        error: TimeoutException,
    ): Nothing {
        future.cancel(true)
        notifyCancellationBeforeStart(started, onCancelledBeforeStart)
        throw screenshotFailure(
            message = "screenshot processing timed out",
            retryable = true,
            cause = error,
        )
    }

    private fun <T> throwProcessingInterrupted(
        future: Future<T>,
        started: AtomicBoolean,
        onCancelledBeforeStart: (() -> Unit)?,
        error: InterruptedException,
    ): Nothing {
        future.cancel(true)
        notifyCancellationBeforeStart(started, onCancelledBeforeStart)
        Thread.currentThread().interrupt()
        throw screenshotFailure(
            message = "screenshot processing interrupted",
            retryable = true,
            cause = error,
        )
    }

    private fun throwProcessingFailure(error: ExecutionException): Nothing {
        val cause = error.cause
        if (cause is ScreenshotException) {
            throw cause
        }
        throw screenshotFailure(
            message = "failed to encode screenshot: ${cause?.message ?: "unknown error"}",
            retryable = true,
            cause = cause ?: error,
        )
    }

    private fun notifyCancellationBeforeStart(
        started: AtomicBoolean,
        onCancelledBeforeStart: (() -> Unit)?,
    ) {
        if (!started.get()) {
            onCancelledBeforeStart?.invoke()
        }
    }

    fun shutdown(force: Boolean) {
        if (shutdownStarted.compareAndSet(false, true)) {
            executor.shutdown()
        }
        val terminated =
            try {
                executor.awaitTermination(SHUTDOWN_GRACE_MS, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                false
            }
        if (!terminated && force && forceShutdownStarted.compareAndSet(false, true)) {
            executor.shutdownNow()
        }
    }

    companion object {
        fun createDefault(): ScreenshotTaskRunner {
            val executor =
                ThreadPoolExecutor(
                    0,
                    RequestBudgets.SCREENSHOT_PROCESSOR_MAX_THREADS,
                    RequestBudgets.SCREENSHOT_PROCESSOR_KEEP_ALIVE_SECONDS,
                    TimeUnit.SECONDS,
                    LinkedBlockingQueue(RequestBudgets.SCREENSHOT_PROCESSOR_QUEUE_CAPACITY),
                    Executors.defaultThreadFactory(),
                    ThreadPoolExecutor.AbortPolicy(),
                )
            executor.allowCoreThreadTimeOut(true)
            return ScreenshotTaskRunner(
                executor = executor,
                timeoutMs = RequestBudgets.SCREENSHOT_PROCESS_TIMEOUT_MS,
            )
        }

        private const val SHUTDOWN_GRACE_MS = 100L
    }
}
