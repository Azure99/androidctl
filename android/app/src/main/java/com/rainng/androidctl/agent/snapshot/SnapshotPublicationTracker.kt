package com.rainng.androidctl.agent.snapshot

import java.util.concurrent.TimeUnit

internal class SnapshotPublicationTracker {
    private val inFlightPublicationSnapshotIds = linkedMapOf<Long, Int>()

    fun beginPublicationLocked(snapshotId: Long) {
        inFlightPublicationSnapshotIds[snapshotId] = (inFlightPublicationSnapshotIds[snapshotId] ?: 0) + 1
    }

    @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
    fun completePublicationLocked(
        snapshotId: Long,
        stateLock: Any,
    ) {
        val snapshotPublicationCount = inFlightPublicationSnapshotIds[snapshotId]
        if (snapshotPublicationCount == null) {
            return
        }
        if (snapshotPublicationCount == 1) {
            inFlightPublicationSnapshotIds.remove(snapshotId)
        } else {
            inFlightPublicationSnapshotIds[snapshotId] = snapshotPublicationCount - 1
        }
        (stateLock as java.lang.Object).notifyAll()
    }

    fun hasActivePublicationsLocked(): Boolean = activePublicationCountLocked() > 0

    fun isPublicationInFlightLocked(snapshotId: Long): Boolean = snapshotId in inFlightPublicationSnapshotIds

    fun inFlightSnapshotIdsLocked(): Set<Long> = inFlightPublicationSnapshotIds.keys.toSet()

    fun waitForActivePublicationsLocked(
        stateLock: Any,
        timeoutMs: Long,
    ): SnapshotPublicationWaitResult {
        require(timeoutMs >= 0L) { "timeoutMs must be non-negative" }
        val deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        var keepWaiting = true
        while (hasActivePublicationsLocked() && keepWaiting) {
            val remainingNanos = deadlineNanos - System.nanoTime()
            keepWaiting = remainingNanos > 0L && waitForRemainingTime(stateLock, remainingNanos)
        }
        return SnapshotPublicationWaitResult(
            completed = !hasActivePublicationsLocked(),
            activePublicationCount = activePublicationCountLocked(),
        )
    }

    @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
    fun abandonActivePublicationsLocked(stateLock: Any) {
        inFlightPublicationSnapshotIds.clear()
        (stateLock as java.lang.Object).notifyAll()
    }

    fun resetForTestLocked() {
        inFlightPublicationSnapshotIds.clear()
    }

    private fun activePublicationCountLocked(): Int = inFlightPublicationSnapshotIds.values.sum()

    @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
    private fun waitForRemainingTime(
        stateLock: Any,
        remainingNanos: Long,
    ): Boolean {
        val waitMs = TimeUnit.NANOSECONDS.toMillis(remainingNanos)
        val waitNanos = (remainingNanos - TimeUnit.MILLISECONDS.toNanos(waitMs)).toInt()
        try {
            (stateLock as java.lang.Object).wait(waitMs, waitNanos)
        } catch (error: InterruptedException) {
            Thread.currentThread().interrupt()
            return false
        }
        return true
    }
}

internal data class SnapshotPublicationWaitResult(
    val completed: Boolean,
    val activePublicationCount: Int,
)
