package com.rainng.androidctl.agent.screenshot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.Callable
import java.util.concurrent.ExecutionException
import java.util.concurrent.Future
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger

class ScreenshotTaskRunnerTest {
    @Test
    fun runsSubmittedTaskAndReturnsResult() {
        val runner =
            ScreenshotTaskRunner(
                executor =
                    ControlledExecutorService { task ->
                        CallableFuture(task)
                    },
                timeoutMs = 1000L,
            )

        val result = runner.run(task = { "encoded" })

        assertEquals("encoded", result)
    }

    @Test
    fun waitsForTaskResultWithConfiguredTimeoutInMilliseconds() {
        val timeoutMs = 4321L
        val future = RecordingTimedGetFuture<String> { "encoded" }
        val runner =
            ScreenshotTaskRunner(
                executor = ControlledExecutorService { future },
                timeoutMs = timeoutMs,
            )

        val result = runner.run(task = { "ignored" })

        assertEquals("encoded", result)
        assertEquals(0, future.plainGetCallCount)
        assertEquals(1, future.timedGetCallCount)
        assertEquals(timeoutMs, future.recordedTimeoutMs)
        assertEquals(TimeUnit.MILLISECONDS, future.recordedTimeUnit)
    }

    @Test
    fun rejectsWhenProcessingQueueIsBusy() {
        val runner =
            ScreenshotTaskRunner(
                executor = RejectingExecutorService(),
                timeoutMs = 1000L,
            )
        val rejectedCount = AtomicInteger(0)

        try {
            runner.run(
                task = { "encoded" },
                onRejected = rejectedCount::incrementAndGet,
            )
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals("screenshot processing is busy", error.message)
            assertTrue(error.retryable)
        }

        assertEquals(1, rejectedCount.get())
    }

    @Test
    fun cancelsTaskWhenProcessingTimesOut() {
        val future = ThrowingFuture<String> { throw TimeoutException("timed out") }
        val runner =
            ScreenshotTaskRunner(
                executor = ControlledExecutorService { future },
                timeoutMs = 10L,
            )

        try {
            runner.run(task = { "encoded" })
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals("screenshot processing timed out", error.message)
            assertTrue(error.retryable)
        }

        assertEquals(1, future.cancelCount)
        assertTrue(future.cancelMayInterruptIfRunning)
    }

    @Test
    fun cancelsBeforeStartCallsCleanupHookExactlyOnce() {
        val future = ThrowingFuture<String> { throw TimeoutException("timed out") }
        val runner =
            ScreenshotTaskRunner(
                executor = ControlledExecutorService { future },
                timeoutMs = 10L,
            )
        val cancelledBeforeStartCount = AtomicInteger(0)

        try {
            runner.run(
                task = { "encoded" },
                onCancelledBeforeStart = cancelledBeforeStartCount::incrementAndGet,
            )
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals("screenshot processing timed out", error.message)
            assertTrue(error.retryable)
        }

        assertEquals(1, future.cancelCount)
        assertEquals(1, cancelledBeforeStartCount.get())
    }

    @Test
    fun wrapsUnexpectedExecutionFailures() {
        val runner =
            ScreenshotTaskRunner(
                executor =
                    ControlledExecutorService {
                        ThrowingFuture<String> {
                            throw ExecutionException(IllegalStateException("boom"))
                        }
                    },
                timeoutMs = 1000L,
            )

        try {
            runner.run(task = { "encoded" })
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals("failed to encode screenshot: boom", error.message)
            assertTrue(error.retryable)
        }
    }

    @Test
    fun surfacesInterruptedProcessing() {
        val future = ThrowingFuture<String> { throw InterruptedException("interrupted") }
        val runner =
            ScreenshotTaskRunner(
                executor = ControlledExecutorService { future },
                timeoutMs = 1000L,
            )

        try {
            runner.run(task = { "encoded" })
            fail("expected ScreenshotException")
        } catch (error: ScreenshotException) {
            assertEquals("screenshot processing interrupted", error.message)
            assertTrue(error.retryable)
            assertTrue(Thread.currentThread().isInterrupted)
        } finally {
            Thread.interrupted()
        }

        assertEquals(1, future.cancelCount)
        assertTrue(future.cancelMayInterruptIfRunning)
    }

    @Test
    fun gracefulShutdownStopsExecutorWithoutForcing() {
        val executor = RecordingShutdownExecutorService(awaitResult = true)
        val runner = ScreenshotTaskRunner(executor = executor, timeoutMs = 1000L)

        runner.shutdown(force = false)

        assertEquals(1, executor.shutdownCalls)
        assertEquals(0, executor.shutdownNowCalls)
        assertEquals(listOf(100L to TimeUnit.MILLISECONDS), executor.awaitCalls)
    }

    @Test
    fun forcedShutdownAfterGracefulShutdownCancelsExecutorAndIsIdempotent() {
        val executor = RecordingShutdownExecutorService(awaitResult = false)
        val runner = ScreenshotTaskRunner(executor = executor, timeoutMs = 1000L)

        runner.shutdown(force = false)
        runner.shutdown(force = true)
        runner.shutdown(force = true)

        assertEquals(1, executor.shutdownCalls)
        assertEquals(1, executor.shutdownNowCalls)
    }

    @Test
    fun interruptedShutdownRestoresThreadFlag() {
        val executor = RecordingShutdownExecutorService(awaitError = InterruptedException("interrupted"))
        val runner = ScreenshotTaskRunner(executor = executor, timeoutMs = 1000L)

        try {
            runner.shutdown(force = false)

            assertTrue(Thread.currentThread().isInterrupted)
            assertEquals(1, executor.shutdownCalls)
            assertEquals(0, executor.shutdownNowCalls)
        } finally {
            Thread.interrupted()
        }
    }

    private class ControlledExecutorService(
        private val futureFactory: (Callable<*>) -> Future<*>,
    ) : AbstractExecutorService() {
        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable) {
            command.run()
        }

        @Suppress("UNCHECKED_CAST")
        override fun <T> submit(task: Callable<T>): Future<T> = futureFactory(task) as Future<T>
    }

    private class CallableFuture<T>(
        private val task: Callable<T>,
    ) : Future<T> {
        override fun cancel(mayInterruptIfRunning: Boolean): Boolean = false

        override fun isCancelled(): Boolean = false

        override fun isDone(): Boolean = false

        override fun get(): T = get(0L, TimeUnit.MILLISECONDS)

        override fun get(
            timeout: Long,
            unit: TimeUnit,
        ): T =
            try {
                task.call()
            } catch (error: Throwable) {
                throw ExecutionException(error)
            }
    }

    private class ThrowingFuture<T>(
        private val getBehavior: () -> T,
    ) : Future<T> {
        var cancelCount: Int = 0
        var cancelMayInterruptIfRunning: Boolean = false

        override fun cancel(mayInterruptIfRunning: Boolean): Boolean {
            cancelCount += 1
            cancelMayInterruptIfRunning = mayInterruptIfRunning
            return true
        }

        override fun isCancelled(): Boolean = cancelCount > 0

        override fun isDone(): Boolean = false

        override fun get(): T = get(0L, TimeUnit.MILLISECONDS)

        override fun get(
            timeout: Long,
            unit: TimeUnit,
        ): T = getBehavior()
    }

    private class RecordingTimedGetFuture<T>(
        private val getBehavior: () -> T,
    ) : Future<T> {
        var plainGetCallCount: Int = 0
        var timedGetCallCount: Int = 0
        var recordedTimeoutMs: Long? = null
        var recordedTimeUnit: TimeUnit? = null

        override fun cancel(mayInterruptIfRunning: Boolean): Boolean = false

        override fun isCancelled(): Boolean = false

        override fun isDone(): Boolean = false

        override fun get(): T {
            plainGetCallCount += 1
            throw AssertionError("expected timed Future.get(timeout, unit)")
        }

        override fun get(
            timeout: Long,
            unit: TimeUnit,
        ): T {
            timedGetCallCount += 1
            recordedTimeoutMs = timeout
            recordedTimeUnit = unit
            return getBehavior()
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

        override fun execute(command: Runnable): Unit = throw RejectedExecutionException("busy")
    }

    private class RecordingShutdownExecutorService(
        private val awaitResult: Boolean = false,
        private val awaitError: InterruptedException? = null,
    ) : AbstractExecutorService() {
        var shutdownCalls: Int = 0
        var shutdownNowCalls: Int = 0
        val awaitCalls = mutableListOf<Pair<Long, TimeUnit>>()

        override fun shutdown() {
            shutdownCalls += 1
        }

        override fun shutdownNow(): MutableList<Runnable> {
            shutdownNowCalls += 1
            return mutableListOf()
        }

        override fun isShutdown(): Boolean = shutdownCalls > 0

        override fun isTerminated(): Boolean = awaitResult

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean {
            awaitCalls += timeout to unit
            awaitError?.let { throw it }
            return awaitResult
        }

        override fun execute(command: Runnable) {
            command.run()
        }
    }
}
