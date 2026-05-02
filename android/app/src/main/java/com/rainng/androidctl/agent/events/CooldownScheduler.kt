package com.rainng.androidctl.agent.events

import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ThreadFactory
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

internal interface ScheduledTask {
    fun cancel()
}

internal interface CooldownScheduler {
    fun schedule(
        delayMs: Long,
        task: () -> Unit,
    ): ScheduledTask

    fun shutdown()
}

internal object NoOpCooldownScheduler : CooldownScheduler {
    private val completedTask =
        object : ScheduledTask {
            override fun cancel() = Unit
        }

    override fun schedule(
        delayMs: Long,
        task: () -> Unit,
    ): ScheduledTask = completedTask

    override fun shutdown() = Unit
}

internal class ExecutorCooldownScheduler(
    private val executor: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor(CooldownThreadFactory()),
) : CooldownScheduler {
    override fun schedule(
        delayMs: Long,
        task: () -> Unit,
    ): ScheduledTask {
        val future = executor.schedule(task, delayMs, TimeUnit.MILLISECONDS)
        return object : ScheduledTask {
            override fun cancel() {
                future.cancel(false)
            }
        }
    }

    override fun shutdown() {
        executor.shutdownNow()
    }
}

private class CooldownThreadFactory : ThreadFactory {
    private val threadCount = AtomicInteger(1)

    override fun newThread(runnable: Runnable): Thread =
        Thread(runnable, "device-event-cooldown-${threadCount.getAndIncrement()}").apply {
            isDaemon = true
        }
}
