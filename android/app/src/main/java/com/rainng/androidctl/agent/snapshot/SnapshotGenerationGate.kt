package com.rainng.androidctl.agent.snapshot

import java.util.concurrent.atomic.AtomicLong

internal class SnapshotGenerationGate {
    private val sequence = AtomicLong(0L)
    private var generation = 0L
    private var activeResets = 0

    fun nextSnapshotId(): Long = sequence.incrementAndGet()

    fun currentGenerationLocked(): Long = generation

    fun beginResetLocked(): Long {
        generation += 1L
        activeResets += 1
        return generation
    }

    fun finishResetLocked() {
        activeResets -= 1
    }

    fun canPublishLocked(candidate: Long): Boolean = activeResets == 0 && candidate == generation

    fun resetInProgressLocked(): Boolean = activeResets > 0

    fun resetForTestLocked() {
        sequence.set(0L)
        generation = 0L
        activeResets = 0
    }
}
