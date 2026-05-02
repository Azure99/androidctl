package com.rainng.androidctl.agent.events

internal class TestClock(
    var nowMs: Long = 0L,
)

internal class TestCooldownScheduler(
    private val clock: TestClock,
) : CooldownScheduler {
    private val lock = Any()
    private val scheduledTasks = mutableListOf<ScheduledEntry>()
    private var nextTaskId = 0L
    var scheduleCount = 0
        private set
    var shutdownCount = 0
        private set
    var cancelledTaskCount = 0
        private set
    var isShutdown = false
        private set

    val pendingTaskCount: Int
        get() =
            synchronized(lock) {
                scheduledTasks.count { !it.cancelled }
            }

    override fun schedule(
        delayMs: Long,
        task: () -> Unit,
    ): ScheduledTask {
        val entry =
            synchronized(lock) {
                check(!isShutdown) { "cannot schedule after shutdown" }
                scheduleCount += 1
                ScheduledEntry(
                    id = ++nextTaskId,
                    executeAtMs = clock.nowMs + delayMs,
                    task = task,
                ).also(scheduledTasks::add)
            }
        return object : ScheduledTask {
            override fun cancel() {
                synchronized(lock) {
                    if (!entry.cancelled) {
                        entry.cancelled = true
                        cancelledTaskCount += 1
                    }
                }
            }
        }
    }

    override fun shutdown() {
        synchronized(lock) {
            shutdownCount += 1
            scheduledTasks
                .filter { !it.cancelled }
                .forEach {
                    it.cancelled = true
                    cancelledTaskCount += 1
                }
            scheduledTasks.clear()
            isShutdown = true
        }
    }

    fun advanceTo(targetMs: Long) {
        require(targetMs >= clock.nowMs) { "targetMs must be monotonic" }
        clock.nowMs = targetMs
        runDueTasks()
    }

    fun advanceBy(deltaMs: Long) {
        require(deltaMs >= 0L) { "deltaMs must be non-negative" }
        advanceTo(clock.nowMs + deltaMs)
    }

    private fun runDueTasks() {
        while (true) {
            val nextTask =
                synchronized(lock) {
                    scheduledTasks
                        .filter { !it.cancelled && it.executeAtMs <= clock.nowMs }
                        .minByOrNull(ScheduledEntry::executeAtMs)
                        ?.also(scheduledTasks::remove)
                } ?: return

            nextTask.task()
        }
    }
}

private data class ScheduledEntry(
    val id: Long,
    val executeAtMs: Long,
    val task: () -> Unit,
    var cancelled: Boolean = false,
)
